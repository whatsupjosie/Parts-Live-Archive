"""Liar's Dice: the rules, the opponents and the table state. Pure Python, no web framework, no I/O.

The host owns the dice. The browser only ever receives ``Game.view()``, which holds the viewer's own dice and the
*number* of dice every other seat has left. Opponents' dice are revealed only in the ``reveal`` block that follows a
call, which is exactly when a real table would turn the cups over. (Routes and saving: modules/liars_dice_api.py.)

Rules implemented (Josie, 2026-11-24 spec):

* Two to four seats: the human ("you") and one to three computer opponents. Everyone starts with five dice.
* Every round all dice are rolled and kept hidden. The first seat opens with a bid: "count x face".
* Each later bid must go up: a higher count, or the same count on a higher face. Counts cannot exceed the
  number of dice still in play.
* Instead of bidding, the next seat may call "Liar" on the standing bid. All dice are revealed. If at least
  ``count`` dice show the bid face the bid was true and the caller loses a die; otherwise the bidder loses one.
* Optional rule ``wild_ones`` (off by default): ones count as any face except when the bid is on ones.
* A seat with no dice is out. The loser of a round (or, if that seat is out, the next seat still in) opens the
  next round. The last seat standing wins. If the human is knocked out the game is over for the human.

Round-ending decisions are made here, never by the browser: a bid or call from the wrong seat, the wrong phase, or
on a stale move number is refused with ``RuleError``.
"""
from __future__ import annotations

import math
import random
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

FACES: Tuple[int, ...] = (1, 2, 3, 4, 5, 6)
DICE_EACH = 5
MIN_OPPONENTS = 1
MAX_OPPONENTS = 3
HUMAN_ID = "you"
STYLES: Tuple[str, ...] = ("cautious", "balanced", "bold")

# How each opponent style thinks (all numbers are probabilities a bid is true, from that seat's point of view):
#   call_below  call "Liar" when the standing bid is less likely true than this
#   raise_at    raise honestly to the lowest bid at least this likely
#   bluff       chance per turn of raising to a lowest bid that is merely plausible (>= BLUFF_FLOOR)
_STYLE = {
    "cautious": {"call_below": 0.42, "raise_at": 0.58, "bluff": 0.05},
    "balanced": {"call_below": 0.35, "raise_at": 0.48, "bluff": 0.10},
    "bold":     {"call_below": 0.27, "raise_at": 0.38, "bluff": 0.18},
}
BLUFF_FLOOR = 0.12
CALL_NOISE = 0.05          # spread of the random nudge to call_below, so an opponent is not a fixed threshold
MAX_AI_STEPS = 400         # a round cannot hold more than 6 x total-dice raises; this is only a hard stop

PHASES = ("bidding", "reveal", "over")
_REVEAL_KEYS = {"caller", "bidder", "count", "face", "matching", "bid_true", "loser", "hands", "wild_ones"}


class RuleError(ValueError):
    """The move is not allowed. The message is written to be shown to the person."""


@dataclass
class Seat:
    id: str
    name: str
    human: bool
    style: str = "balanced"
    remaining: int = DICE_EACH
    dice: List[int] = field(default_factory=list)   # in play only while the phase is "bidding"; empty otherwise


@dataclass
class Game:
    seats: List[Seat]
    wild_ones: bool = False
    game_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    round: int = 1
    turn: int = 0                               # index of the seat that must act
    bid_count: int = 0                          # 0 means no standing bid
    bid_face: int = 0
    bidder: int = -1                            # index of the seat that made the standing bid
    phase: str = "bidding"
    move: int = 0                               # counts every change, so a stale click can be refused
    reveal: Optional[Dict[str, Any]] = None
    winner: Optional[str] = None
    next_first: int = 0                         # who opens the next round (set when a round ends)
    rng: random.Random = field(default_factory=random.SystemRandom, repr=False, compare=False)

    # ── queries ────────────────────────────────────────────────────────────────

    @property
    def has_bid(self) -> bool:
        return self.bid_count > 0

    def total_dice(self) -> int:
        return sum(s.remaining for s in self.seats)

    def alive(self) -> List[int]:
        return [i for i, s in enumerate(self.seats) if s.remaining > 0]

    def seat_index(self, seat_id: str) -> int:
        for i, s in enumerate(self.seats):
            if s.id == seat_id:
                return i
        raise RuleError("There is no such seat at this table.")

    def next_alive(self, index: int) -> int:
        """The next seat after ``index`` (clockwise) that still has dice."""
        n = len(self.seats)
        for step in range(1, n + 1):
            j = (index + step) % n
            if self.seats[j].remaining > 0:
                return j
        raise RuleError("No seat has any dice left.")

    def is_legal_raise(self, count: int, face: int) -> bool:
        return legal_raise(self.bid_count, self.bid_face, count, face, self.total_dice())

    def legal_raises(self) -> List[Tuple[int, int]]:
        """Every raise open to the seat to act, lowest first."""
        total = self.total_dice()
        return [(c, f) for c in range(1, total + 1) for f in FACES
                if legal_raise(self.bid_count, self.bid_face, c, f, total)]

    def matching(self, face: int, dice_by_seat: Sequence[Sequence[int]]) -> int:
        n = 0
        for dice in dice_by_seat:
            for d in dice:
                if d == face or (self.wild_ones and d == 1 and face != 1):
                    n += 1
        return n

    # ── actions ────────────────────────────────────────────────────────────────

    def _need_phase(self, phase: str) -> None:
        if self.phase != phase:
            word = {"bidding": "bidding is not open right now", "reveal": "the round is already over",
                    "over": "the game is over"}[self.phase]
            raise RuleError(f"That is not possible: {word}.")

    def bid(self, seat_index: int, count: int, face: int) -> List[Dict[str, Any]]:
        self._need_phase("bidding")
        if seat_index != self.turn:
            raise RuleError("It is not that seat's turn.")
        if isinstance(count, bool) or isinstance(face, bool) or not isinstance(count, int) or not isinstance(face, int):
            raise RuleError("A bid is a whole number of dice and a face from 1 to 6.")
        if face not in FACES:
            raise RuleError("The face must be from 1 to 6.")
        total = self.total_dice()
        if count < 1 or count > total:
            raise RuleError(f"The count must be from 1 to {total}, the number of dice in play.")
        if not self.is_legal_raise(count, face):
            raise RuleError(f"A bid must beat {self.bid_count} x {self.bid_face}: a higher count, or the same count on a higher face.")
        self.bid_count, self.bid_face, self.bidder = count, face, seat_index
        self.turn = self.next_alive(seat_index)
        self.move += 1
        return [{"type": "bid", "seat": self.seats[seat_index].id, "count": count, "face": face}]

    def call(self, seat_index: int) -> List[Dict[str, Any]]:
        self._need_phase("bidding")
        if seat_index != self.turn:
            raise RuleError("It is not that seat's turn.")
        if not self.has_bid:
            raise RuleError("There is no bid to call yet. Open the round with a bid.")
        caller, bidder = seat_index, self.bidder
        hands = {s.id: list(s.dice) for s in self.seats if s.remaining > 0}
        matching = self.matching(self.bid_face, [s.dice for s in self.seats])
        bid_true = matching >= self.bid_count
        loser = caller if bid_true else bidder
        events: List[Dict[str, Any]] = [
            {"type": "call", "seat": self.seats[caller].id, "against": self.seats[bidder].id},
            {"type": "reveal", "hands": hands, "face": self.bid_face, "count": self.bid_count,
             "matching": matching, "bid_true": bid_true, "loser": self.seats[loser].id},
        ]
        self.reveal = {"caller": self.seats[caller].id, "bidder": self.seats[bidder].id, "count": self.bid_count,
                       "face": self.bid_face, "matching": matching, "bid_true": bid_true,
                       "loser": self.seats[loser].id, "hands": hands, "wild_ones": self.wild_ones}
        for s in self.seats:
            s.dice = []
        self.seats[loser].remaining -= 1
        events.append({"type": "lost_die", "seat": self.seats[loser].id, "remaining": self.seats[loser].remaining})
        if self.seats[loser].remaining == 0:
            events.append({"type": "out", "seat": self.seats[loser].id})
        human_alive = any(s.human and s.remaining > 0 for s in self.seats)
        alive = self.alive()
        if len(alive) == 1 or not human_alive:
            self.phase = "over"
            self.winner = self.seats[alive[0]].id if len(alive) == 1 else None
            events.append({"type": "over", "winner": self.winner, "human_won": bool(self.winner and self.seats[self.seek_human()].id == self.winner)})
        else:
            self.phase = "reveal"
            self.next_first = loser if self.seats[loser].remaining > 0 else self.next_alive(loser)
        self.move += 1
        return events

    def seek_human(self) -> int:
        for i, s in enumerate(self.seats):
            if s.human:
                return i
        raise RuleError("There is no human seat at this table.")

    def next_round(self) -> List[Dict[str, Any]]:
        self._need_phase("reveal")
        self.round += 1
        self.roll_all()
        self.bid_count = self.bid_face = 0
        self.bidder = -1
        self.reveal = None
        self.turn = self.next_first
        self.phase = "bidding"
        self.move += 1
        return [{"type": "round", "round": self.round, "first": self.seats[self.turn].id}]

    def roll_all(self) -> None:
        for s in self.seats:
            s.dice = sorted(self.rng.randint(1, 6) for _ in range(s.remaining))

    # ── opponents ──────────────────────────────────────────────────────────────

    def advance_ai(self) -> List[Dict[str, Any]]:
        """Play every computer seat's turn until the human must act or the round ends."""
        events: List[Dict[str, Any]] = []
        for _ in range(MAX_AI_STEPS):
            if self.phase != "bidding" or self.seats[self.turn].human:
                return events
            idx = self.turn
            decision = ai_decide(self, idx)
            events += self.call(idx) if decision[0] == "call" else self.bid(idx, decision[1], decision[2])
        raise RuntimeError("Opponents did not finish their turns (rule bug)")

    # ── what the browser may see ───────────────────────────────────────────────

    def view(self) -> Dict[str, Any]:
        human = self.seats[self.seek_human()]
        playing = self.phase == "bidding"
        you_turn = playing and self.seats[self.turn].human
        total = self.total_dice()
        return {
            "game_id": self.game_id,
            "round": self.round,
            "phase": self.phase,
            "move": self.move,
            "wild_ones": self.wild_ones,
            "turn": self.seats[self.turn].id if playing else None,
            "bid": ({"count": self.bid_count, "face": self.bid_face, "by": self.seats[self.bidder].id}
                    if self.has_bid and playing else None),
            "total_dice": total,
            "you": {"id": human.id, "dice": list(human.dice)},
            "seats": [{"id": s.id, "name": s.name, "human": s.human, "remaining": s.remaining, "out": s.remaining == 0}
                      for s in self.seats],
            "can_call": bool(you_turn and self.has_bid),
            "can_raise": bool(you_turn and self.legal_raises()),
            "reveal": self.reveal,
            "winner": self.winner,
            "you_out": human.remaining == 0,
        }

    # ── saving ─────────────────────────────────────────────────────────────────

    def to_record(self) -> Dict[str, Any]:
        """Everything, including every seat's hidden dice. Host-side only: never send this to a browser."""
        return {
            "v": 1, "game_id": self.game_id, "round": self.round, "turn": self.turn, "wild_ones": self.wild_ones,
            "bid_count": self.bid_count, "bid_face": self.bid_face, "bidder": self.bidder, "phase": self.phase,
            "move": self.move, "reveal": self.reveal, "winner": self.winner, "next_first": self.next_first,
            "seats": [{"id": s.id, "name": s.name, "human": s.human, "style": s.style, "remaining": s.remaining,
                       "dice": list(s.dice)} for s in self.seats],
        }

    @classmethod
    def from_record(cls, rec: Any, rng: Optional[random.Random] = None) -> "Game":
        """Rebuild a saved game, refusing anything that is not a state this engine could have produced."""
        def bad(why: str) -> ValueError:
            return ValueError("saved Liar's Dice game is not valid: " + why)

        def whole(v: Any, lo: int, hi: int, what: str) -> int:
            if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
                raise bad(f"{what} must be a whole number from {lo} to {hi}")
            return v

        if not isinstance(rec, dict) or rec.get("v") != 1:
            raise bad("unknown format")
        raw_seats = rec.get("seats")
        if not isinstance(raw_seats, list) or not MIN_OPPONENTS + 1 <= len(raw_seats) <= MAX_OPPONENTS + 1:
            raise bad("wrong number of seats")
        seats: List[Seat] = []
        for i, rs in enumerate(raw_seats):
            if not isinstance(rs, dict):
                raise bad("a seat is not an object")
            sid, name, human, style = rs.get("id"), rs.get("name"), rs.get("human"), rs.get("style")
            if not isinstance(sid, str) or not sid or len(sid) > 40 or not isinstance(name, str) or not name or len(name) > 60:
                raise bad("a seat has no id or name")
            if not isinstance(human, bool) or style not in STYLES:
                raise bad("a seat has a bad type or style")
            remaining = whole(rs.get("remaining"), 0, DICE_EACH, "dice remaining")
            dice = rs.get("dice")
            if not isinstance(dice, list) or any(isinstance(d, bool) or d not in FACES for d in dice):
                raise bad("dice must be faces from 1 to 6")
            seats.append(Seat(sid, name, human, style, remaining, list(dice)))
        if len({s.id for s in seats}) != len(seats):
            raise bad("seat ids repeat")
        if [s.human for s in seats].count(True) != 1 or not seats[0].human:
            raise bad("there must be exactly one human, in the first seat")
        phase = rec.get("phase")
        if phase not in PHASES:
            raise bad("unknown phase")
        n = len(seats)
        turn = whole(rec.get("turn"), 0, n - 1, "turn")
        next_first = whole(rec.get("next_first"), 0, n - 1, "next opener")
        bid_count = whole(rec.get("bid_count"), 0, DICE_EACH * n, "bid count")
        bid_face = whole(rec.get("bid_face"), 0, 6, "bid face")
        bidder = whole(rec.get("bidder"), -1, n - 1, "bidder")
        if (bid_count == 0) != (bid_face == 0) or (bid_count == 0) != (bidder == -1):
            raise bad("the standing bid is incomplete")
        if phase == "bidding":
            if any(len(s.dice) != s.remaining for s in seats):
                raise bad("a seat's dice do not match its count")
            if seats[turn].remaining == 0:
                raise bad("the seat to act is out")
            if bid_count > sum(s.remaining for s in seats):
                raise bad("the standing bid is larger than the dice in play")
            if bid_count and bid_face == 0:
                raise bad("the standing bid has no face")
            if bid_count and seats[bidder].remaining == 0:
                raise bad("the seat that made the standing bid is out")
        else:
            if any(s.dice for s in seats):
                raise bad("dice are held outside a round")
            if phase == "reveal" and seats[next_first].remaining == 0:
                raise bad("the next round would open on a seat that is out")
        winner = rec.get("winner")
        if winner is not None and winner not in {s.id for s in seats}:
            raise bad("the winner is not a seat")
        if (phase == "over") != (winner is not None or not any(s.human and s.remaining > 0 for s in seats)):
            raise bad("the game is over, or not, inconsistently")
        reveal = rec.get("reveal")
        if (phase == "bidding") != (reveal is None):
            raise bad("reveal does not match the phase")
        if reveal is not None:
            ids = {s.id for s in seats}
            hands = reveal.get("hands") if isinstance(reveal, dict) else None
            ok = (isinstance(reveal, dict) and set(reveal) == _REVEAL_KEYS
                  and reveal["caller"] in ids and reveal["bidder"] in ids and reveal["loser"] in ids
                  and isinstance(reveal["bid_true"], bool) and isinstance(reveal["wild_ones"], bool)
                  and all(isinstance(reveal[k], int) and not isinstance(reveal[k], bool) for k in ("count", "face", "matching"))
                  and reveal["face"] in FACES
                  and isinstance(hands, dict) and set(hands) <= ids
                  and all(isinstance(h, list) and len(h) <= DICE_EACH and all(not isinstance(d, bool) and d in FACES for d in h)
                          for h in hands.values()))
            if not ok:
                raise bad("the reveal is malformed")
        wild = rec.get("wild_ones")
        if not isinstance(wild, bool):
            raise bad("wild_ones must be true or false")
        gid = rec.get("game_id")
        if not isinstance(gid, str) or not 1 <= len(gid) <= 40:
            raise bad("bad game id")
        g = cls(seats=seats, wild_ones=wild, game_id=gid, round=whole(rec.get("round"), 1, 10_000, "round"), turn=turn,
                bid_count=bid_count, bid_face=bid_face, bidder=bidder, phase=phase,
                move=whole(rec.get("move"), 0, 10_000_000, "move"), reveal=reveal, winner=winner, next_first=next_first)
        if rng is not None:
            g.rng = rng
        return g


# ── rules helpers ──────────────────────────────────────────────────────────────

def legal_raise(prev_count: int, prev_face: int, count: int, face: int, total_dice: int) -> bool:
    """Is ``count x face`` an allowed bid after ``prev_count x prev_face`` (0 x 0 means no bid yet)?"""
    if face not in FACES or not 1 <= count <= total_dice:
        return False
    if prev_count == 0:
        return True
    return count > prev_count or (count == prev_count and face > prev_face)


def new_game(opponents: int, wild_ones: bool = False, rng: Optional[random.Random] = None) -> Game:
    """Start a game: you plus ``opponents`` (1 to 3) computer seats, dice rolled, you to open round one."""
    if isinstance(opponents, bool) or not isinstance(opponents, int) or not MIN_OPPONENTS <= opponents <= MAX_OPPONENTS:
        raise RuleError(f"Choose {MIN_OPPONENTS} to {MAX_OPPONENTS} opponents.")
    if not isinstance(wild_ones, bool):
        raise RuleError("wild_ones must be true or false.")
    rng = rng or random.SystemRandom()
    seats = [Seat(HUMAN_ID, "You", True)]
    styles = [rng.choice(STYLES) for _ in range(opponents)]
    for n, style in enumerate(styles, start=2):
        seats.append(Seat(f"seat{n}", f"Seat {n}", False, style))
    g = Game(seats=seats, wild_ones=wild_ones, rng=rng)
    g.roll_all()
    return g


# ── the opponents' reasoning ───────────────────────────────────────────────────

def binomial_at_least(n: int, p: float, k: int) -> float:
    """P(X >= k) for X ~ Binomial(n, p), summed exactly (n never exceeds 20 here)."""
    if k <= 0:
        return 1.0
    if k > n:
        return 0.0
    q = 1.0 - p
    return min(1.0, sum(math.comb(n, i) * p ** i * q ** (n - i) for i in range(k, n + 1)))


def _own_matching(game: Game, dice: Sequence[int], face: int) -> int:
    return game.matching(face, [dice])


def bid_probability(game: Game, seat_index: int, count: int, face: int) -> float:
    """How likely ``count x face`` is true as seen from ``seat_index``: its own dice are known, the rest are random."""
    me = game.seats[seat_index]
    unseen = game.total_dice() - len(me.dice)
    p = 2 / 6 if (game.wild_ones and face != 1) else 1 / 6
    return binomial_at_least(unseen, p, count - _own_matching(game, me.dice, face))


def ai_decide(game: Game, seat_index: int) -> Tuple[Any, ...]:
    """Return ``("call",)`` or ``("bid", count, face)``. Always a legal move, drawn only from what the seat can see."""
    seat, rng = game.seats[seat_index], game.rng
    style = _STYLE[seat.style]
    if game.has_bid:
        raises = game.legal_raises()
        threshold = min(0.9, max(0.05, style["call_below"] + rng.gauss(0.0, CALL_NOISE)))
        if not raises or bid_probability(game, seat_index, game.bid_count, game.bid_face) < threshold:
            return ("call",)
        rated = [(c, f, bid_probability(game, seat_index, c, f)) for c, f in raises]
        if rng.random() < style["bluff"]:
            for c, f, p in rated:
                if BLUFF_FLOOR <= p < style["raise_at"]:
                    return ("bid", c, f)
        for c, f, p in rated:
            if p >= style["raise_at"]:
                return ("bid", c, f)
        best = max(rated, key=lambda r: (r[2], -r[0], -r[1]))     # nothing is safe: take the likeliest
        return ("bid", best[0], best[1])
    # Opening bid: the face this seat holds most of, plus its share of what the others probably hold.
    unseen = game.total_dice() - len(seat.dice)
    if rng.random() < style["bluff"]:
        face = rng.choice(FACES)
    else:
        face = max(FACES, key=lambda f: (_own_matching(game, seat.dice, f), f))
    p = 2 / 6 if (game.wild_ones and face != 1) else 1 / 6
    count = _own_matching(game, seat.dice, face) + int(unseen * p * 0.6)
    return ("bid", max(1, min(count, game.total_dice())), face)


__all__ = ["Game", "Seat", "RuleError", "new_game", "legal_raise", "ai_decide", "bid_probability",
           "binomial_at_least", "FACES", "DICE_EACH", "HUMAN_ID", "MIN_OPPONENTS", "MAX_OPPONENTS", "STYLES"]
