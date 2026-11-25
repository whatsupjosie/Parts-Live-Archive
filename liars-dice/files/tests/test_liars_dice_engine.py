"""Liar's Dice engine (modules/liars_dice.py): rules, hidden information, opponents, saving.

The engine is pure Python, so these are real behaviour checks, not mocks: dice are stacked by hand for the rule
cases, and the opponents are run through hundreds of seeded full games to prove they only ever play legal moves and
that every game finishes.
"""
from __future__ import annotations

import copy
import itertools
import json
import random

import pytest

from modules.liars_dice import (
    DICE_EACH, FACES, HUMAN_ID, STYLES, Game, RuleError, ai_decide, binomial_at_least, bid_probability, legal_raise,
    new_game,
)


def stacked(hands, wild=False, styles=None, turn=0, seed=0):
    """A game with exactly these dice (hands[0] is the human)."""
    g = new_game(len(hands) - 1, wild, random.Random(seed))
    for i, (seat, hand) in enumerate(zip(g.seats, hands)):
        seat.dice, seat.remaining = list(hand), len(hand)
        if styles and i:
            seat.style = styles[i - 1]
    g.turn = turn
    return g


# ── the arithmetic the opponents rely on ────────────────────────────────────────

@pytest.mark.parametrize("n", [0, 1, 2, 3, 4, 5])
def test_binomial_tail_matches_counting_every_outcome(n):
    for k in range(-1, n + 2):
        hits = sum(1 for roll in itertools.product(FACES, repeat=n) if sum(1 for d in roll if d == 3) >= k)
        assert binomial_at_least(n, 1 / 6, k) == pytest.approx(hits / 6 ** n, abs=1e-12)


def test_bid_probability_uses_own_dice_and_wild_ones():
    g = stacked([[2, 2, 2], [3, 3, 3]], seed=1)
    # holds three 2s itself: "3 x 2" is certain, "4 x 2" needs one of the other 3 dice (1/6 each)
    assert bid_probability(g, 0, 3, 2) == 1.0
    assert bid_probability(g, 0, 4, 2) == pytest.approx(1 - (5 / 6) ** 3)
    w = stacked([[2, 2, 1], [3, 3, 3]], wild=True, seed=1)
    # with wild ones the single 1 is a 2 as well, and each unseen die is a 2 or a 1 (2/6)
    assert bid_probability(w, 0, 4, 2) == pytest.approx(1 - (4 / 6) ** 3)
    # bidding on ones: ones are not wild there, so it needs three more literal ones, each 1/6
    assert bid_probability(w, 0, 4, 1) == pytest.approx((1 / 6) ** 3)


def test_ones_are_not_wild_when_the_bid_is_on_ones():
    w = stacked([[1, 1, 4], [3, 3, 3]], wild=True)
    assert bid_probability(w, 0, 3, 1) == pytest.approx(1 - (5 / 6) ** 3)         # needs one more literal 1 of 3 unseen: p = 1/6
    assert bid_probability(w, 0, 2, 1) == 1.0


# ── the rules ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("prev,new,total,ok", [
    ((0, 0), (1, 1), 10, True), ((0, 0), (10, 6), 10, True), ((0, 0), (11, 1), 10, False), ((0, 0), (0, 3), 10, False),
    ((3, 4), (4, 1), 10, True),                       # higher count, any face
    ((3, 4), (3, 5), 10, True),                       # same count, higher face
    ((3, 4), (3, 4), 10, False), ((3, 4), (3, 3), 10, False), ((3, 4), (2, 6), 10, False),
    ((3, 4), (3, 7), 10, False), ((3, 4), (3, 0), 10, False),
    ((10, 6), (10, 6), 10, False), ((9, 6), (10, 1), 10, True),
])
def test_what_counts_as_a_raise(prev, new, total, ok):
    assert legal_raise(prev[0], prev[1], new[0], new[1], total) is ok


@pytest.mark.parametrize("bad", [0, 4, -1, True, "2", 2.0, None])
def test_the_number_of_opponents_is_one_to_three(bad):
    with pytest.raises(RuleError):
        new_game(bad)


def test_a_new_game_has_you_first_and_five_dice_each():
    for opponents in (1, 2, 3):
        g = new_game(opponents, rng=random.Random(opponents))
        assert len(g.seats) == opponents + 1 and g.seats[0].id == HUMAN_ID and g.seats[0].human
        assert not any(s.human for s in g.seats[1:])
        assert all(s.remaining == DICE_EACH and len(s.dice) == DICE_EACH and all(d in FACES for d in s.dice) for s in g.seats)
        assert g.turn == 0 and g.phase == "bidding" and not g.has_bid and g.round == 1
        assert {s.style for s in g.seats[1:]} <= set(STYLES)
        assert len({s.id for s in g.seats}) == len(g.seats)


def test_dice_come_from_the_system_generator_unless_one_is_given():
    g = new_game(1)
    assert type(g.rng).__name__ == "SystemRandom"


def test_bids_are_checked_by_the_host():
    g = stacked([[1, 2, 3], [4, 5, 6]])
    for count, face in [(0, 3), (7, 3), (2, 0), (2, 7), (True, 3), (2, True), ("2", 3), (2.0, 3), (None, 3)]:
        with pytest.raises(RuleError):
            g.bid(0, count, face)
    with pytest.raises(RuleError, match="turn"):
        g.bid(1, 1, 1)                                              # not their turn
    assert g.move == 0 and not g.has_bid                           # nothing above changed anything
    g.bid(0, 2, 3)
    with pytest.raises(RuleError, match="beat 2 x 3"):
        g.bid(1, 2, 3)
    with pytest.raises(RuleError, match="beat 2 x 3"):
        g.bid(1, 1, 6)
    g.bid(1, 2, 4)
    assert g.turn == 0 and g.move == 2


def test_you_cannot_call_before_anyone_has_bid():
    g = stacked([[1, 2, 3], [4, 5, 6]])
    with pytest.raises(RuleError, match="no bid"):
        g.call(0)


def test_a_true_bid_costs_the_caller_a_die():
    g = stacked([[3, 3, 5], [3, 6, 6]])                              # four 3s in play
    g.bid(0, 4, 3)
    ev = g.call(1)
    assert [e["type"] for e in ev] == ["call", "reveal", "lost_die"]
    r = g.reveal
    assert r["matching"] == 3 and r["bid_true"] is False             # three 3s: the bid of four was a lie
    assert r["loser"] == HUMAN_ID and g.seats[0].remaining == 2 and g.seats[1].remaining == 3
    g2 = stacked([[3, 3, 5], [3, 3, 6]])
    g2.bid(0, 4, 3)
    g2.call(1)
    assert g2.reveal["bid_true"] is True and g2.reveal["loser"] == "seat2" and g2.seats[1].remaining == 2


def test_the_bid_is_true_when_exactly_met():
    g = stacked([[2, 2], [2, 5]])
    g.bid(0, 3, 2)
    g.call(1)
    assert g.reveal["matching"] == 3 and g.reveal["bid_true"] is True and g.reveal["loser"] == "seat2"


def test_wild_ones_count_for_every_face_but_ones():
    g = stacked([[1, 1, 4], [4, 6, 6]], wild=True)
    g.bid(0, 4, 4)                                                  # two 4s plus two wild ones
    g.call(1)
    assert g.reveal["matching"] == 4 and g.reveal["bid_true"] is True
    g = stacked([[1, 1, 4], [4, 6, 6]], wild=True)
    g.bid(0, 3, 1)                                                  # bidding on ones: only real ones count
    g.call(1)
    assert g.reveal["matching"] == 2 and g.reveal["bid_true"] is False
    g = stacked([[1, 1, 4], [4, 6, 6]], wild=False)
    g.bid(0, 4, 4)
    g.call(1)
    assert g.reveal["matching"] == 2 and g.reveal["bid_true"] is False


def test_the_loser_opens_the_next_round_with_fresh_dice_and_no_bid():
    g = stacked([[1, 2, 3], [4, 5, 6], [6, 6, 6]], seed=5)
    g.bid(0, 2, 2)
    g.bid(1, 2, 3)
    g.call(2)                                                       # seat3 calls seat2's 2 x 3: only one 3 exists -> seat2 loses
    assert g.reveal["loser"] == "seat2" and g.phase == "reveal" and g.next_first == 1
    assert all(s.dice == [] for s in g.seats)
    with pytest.raises(RuleError):
        g.bid(1, 3, 3)                                              # no bidding between rounds
    g.next_round()
    assert g.round == 2 and g.turn == 1 and not g.has_bid and g.reveal is None and g.phase == "bidding"
    assert [len(s.dice) for s in g.seats] == [s.remaining for s in g.seats] == [3, 2, 3]


def test_an_eliminated_loser_hands_the_opening_to_the_next_seat_in():
    g = stacked([[1, 2], [4], [6, 6]])
    g.turn = 1
    g.bid(1, 2, 4)                                                  # one 4 exists
    g.call(2)
    assert g.reveal["loser"] == "seat2" and g.seats[1].remaining == 0
    assert g.next_first == 2                                        # seat2 is out, so seat3 opens
    g.next_round()
    assert g.turn == 2 and g.seats[1].dice == []


def test_last_seat_standing_wins_and_play_stops():
    g = stacked([[3], [3]])
    g.bid(0, 2, 6)                                                  # a lie: nobody holds a six
    ev = g.call(1)
    assert ev[-1] == {"type": "over", "winner": "seat2", "human_won": False}
    assert g.phase == "over" and g.winner == "seat2" and g.seats[0].remaining == 0
    assert g.view()["you_out"] is True and g.view()["winner"] == "seat2"
    for action in (lambda: g.bid(0, 1, 1), lambda: g.call(0), g.next_round):
        with pytest.raises(RuleError):
            action()


def test_you_win_when_the_last_opponent_loses_their_last_die():
    g = stacked([[3, 3], [4]])
    g.bid(0, 2, 3)                                                  # true: two 3s
    ev = g.call(1)                                                  # seat2 calls, is wrong, and it had one die left
    assert g.reveal["bid_true"] is True and g.reveal["loser"] == "seat2"
    assert g.phase == "over" and g.winner == HUMAN_ID
    assert ev[-1] == {"type": "over", "winner": HUMAN_ID, "human_won": True}
    v = g.view()
    assert v["winner"] == HUMAN_ID and v["you_out"] is False and v["turn"] is None


def test_the_game_ends_when_you_are_knocked_out_even_with_opponents_left():
    g = stacked([[1], [2, 2], [3, 3]])
    g.bid(0, 1, 6)
    ev = g.call(1)
    assert g.seats[0].remaining == 0 and g.phase == "over" and g.winner is None
    assert ev[-1] == {"type": "over", "winner": None, "human_won": False}
    assert sum(1 for s in g.seats if s.remaining) == 2


# ── hidden information ─────────────────────────────────────────────────────────

def test_the_view_holds_only_your_own_dice_while_bidding():
    g = stacked([[1, 2, 3], [4, 5, 6], [6, 6, 6]])
    g.bid(0, 1, 1)
    v = g.view()
    assert v["you"]["dice"] == [1, 2, 3]
    assert v["reveal"] is None and v["phase"] == "bidding"
    assert all("dice" not in s for s in v["seats"])
    blob = json.dumps(v)
    assert "hands" not in blob and "style" not in blob                 # opponents' thinking is not shown either
    assert [s["remaining"] for s in v["seats"]] == [3, 3, 3]


def test_a_call_turns_every_cup_over_and_the_next_round_hides_them_again():
    g = stacked([[1, 2, 3], [4, 5, 6]])
    g.bid(0, 1, 1)
    g.call(1)
    v = g.view()
    assert v["phase"] == "reveal" and v["reveal"]["hands"] == {"you": [1, 2, 3], "seat2": [4, 5, 6]}
    assert v["you"]["dice"] == [] and v["turn"] is None and v["bid"] is None
    g.next_round()
    v2 = g.view()
    assert v2["reveal"] is None and len(v2["you"]["dice"]) == v2["seats"][0]["remaining"]
    assert "hands" not in json.dumps(v2)


def test_the_view_says_what_you_may_do():
    g = stacked([[1, 2, 3], [4, 5, 6]])
    v = g.view()
    assert v["can_call"] is False and v["can_raise"] is True and v["turn"] == HUMAN_ID
    g.bid(0, 6, 6)                                                  # the highest possible bid: nothing can beat it
    g.turn = 0
    v = g.view()
    assert v["can_raise"] is False and v["can_call"] is True


# ── the opponents ──────────────────────────────────────────────────────────────

def _play(seed, wild, opponents):
    """One whole game against a simple human: bids the lowest legal raise, calls half the time when it is likely false."""
    rng = random.Random(seed)
    g = new_game(opponents, wild, random.Random(seed + 10_000))
    guard = 0
    while g.phase != "over":
        guard += 1
        assert guard < 5000
        if g.phase == "reveal":
            g.next_round()
            g.advance_ai()
            continue
        if g.turn != 0:
            g.advance_ai()
            continue
        total = g.total_dice()
        if g.has_bid and (not g.legal_raises() or rng.random() < 0.35):
            g.call(0)
        elif g.has_bid and bid_probability(g, 0, g.bid_count, g.bid_face) < 0.3 and rng.random() < 0.6:
            g.call(0)
        else:
            c, f = g.legal_raises()[0] if rng.random() < 0.7 else rng.choice(g.legal_raises())
            assert 1 <= c <= total
            g.bid(0, c, f)
        g.advance_ai()
    return g


@pytest.mark.parametrize("wild", [False, True])
def test_opponents_play_only_legal_moves_and_every_game_finishes(wild):
    results = []
    for seed in range(200):
        g = _play(seed, wild, 1 + seed % 3)
        assert g.phase == "over"
        assert g.winner is None or g.winner in {s.id for s in g.seats}
        assert sum(1 for s in g.seats if s.remaining) >= 1
        results.append(g.winner == HUMAN_ID)
    # a human who bids the minimum and calls at random should lose most games but not all of them
    assert 0 < sum(results) < len(results)


def _call_rate(style, count, seeds=300):
    calls = 0
    for seed in range(seeds):
        g = stacked([[1, 2, 3, 4, 5], [2, 2, 3, 6, 6], [1, 1, 1, 1, 1], [5, 5, 5, 5, 5]], styles=[style, "balanced", "balanced"], seed=seed)
        g.bid(0, count, 2)
        calls += ai_decide(g, 1) == ("call",)
    return calls / seeds


def test_opponents_call_more_as_the_bid_gets_less_believable_and_styles_differ():
    # the opponent holds two 2s and cannot see 15 other dice; "4 x 2" needs two more, "9 x 2" needs seven
    rates = {s: [_call_rate(s, c) for c in range(4, 10)] for s in STYLES}
    for style, series in rates.items():
        assert series == sorted(series), (style, series)           # never calls less as the bid climbs
        assert series[0] == 0.0 and series[-1] == 1.0, (style, series)
    mid = lambda s: sum(rates[s])
    assert mid("bold") < mid("balanced") < mid("cautious"), rates  # bolder opponents call less readily


def test_an_opponent_always_calls_a_bid_it_can_see_is_impossible():
    for style in STYLES:
        for seed in range(50):
            g = stacked([[1, 1, 1, 1, 1], [2, 2, 3, 3, 4]], styles=[style], seed=seed)
            g.bid(0, 9, 6)                                          # nine 6s from ten dice, and it holds none
            assert ai_decide(g, 1) == ("call",)


def test_an_opponent_never_calls_a_bid_its_own_dice_already_make_true():
    for style in STYLES:
        for seed in range(50):
            g = stacked([[1, 2, 3, 4, 5], [2, 2, 2, 2, 2]], styles=[style], seed=seed)
            g.bid(0, 3, 2)                                          # it holds five 2s
            decision = ai_decide(g, 1)
            assert decision[0] == "bid"
            g.bid(1, decision[1], decision[2])                      # and the raise it chose is legal


def test_an_opponent_must_call_when_no_raise_is_left():
    g = stacked([[1, 2], [3, 4]], styles=["bold"])
    g.bid(0, 4, 6)
    assert g.legal_raises() == [] and ai_decide(g, 1) == ("call",)


def test_opening_bids_are_always_legal():
    for wild in (False, True):
        for style in STYLES:
            for seed in range(120):
                g = stacked([[1, 2, 3, 4, 5], [6, 6, 6, 1, 2]], wild=wild, styles=[style], turn=1, seed=seed)
                d = ai_decide(g, 1)
                assert d[0] == "bid"
                g.bid(1, d[1], d[2])


def test_the_same_seed_plays_the_same_game():
    def run(seed):
        g = new_game(3, False, random.Random(seed))
        events = []
        g.bid(0, 1, 2)
        events += g.advance_ai()
        record = g.to_record()
        record.pop("game_id")                                       # unique per game on purpose
        return json.dumps([events, record], sort_keys=True)
    assert run(7) == run(7) and run(7) != run(8)


# ── saving ─────────────────────────────────────────────────────────────────────

def test_a_game_survives_being_written_and_read_back_in_every_phase():
    g = new_game(3, True, random.Random(3))
    g.bid(0, 2, 5)
    g.advance_ai()
    for step in range(3):
        rec = json.loads(json.dumps(g.to_record()))
        back = Game.from_record(rec)
        assert back.to_record() == g.to_record() and back.view() == g.view()
        if g.phase == "bidding" and g.turn == 0 and g.has_bid:
            g.call(0)
        elif g.phase == "reveal":
            g.next_round(); g.advance_ai()
        elif g.phase == "bidding" and g.turn == 0:
            g.bid(0, *g.legal_raises()[0]); g.advance_ai()


def _mutations():
    base = new_game(2, False, random.Random(1))
    base.bid(0, 2, 3)
    base.advance_ai()
    rec = base.to_record()
    reveal_game = new_game(1, False, random.Random(1))
    reveal_game.bid(0, 1, 1)
    reveal_game.call(1)
    rrec = reveal_game.to_record()
    cases = {
        "not a dict": [1, 2], "wrong version": {**rec, "v": 2}, "no seats": {**rec, "seats": []},
        "five seats": {**rec, "seats": rec["seats"] * 2},
        "dice out of range": {**rec, "seats": [{**rec["seats"][0], "dice": [7, 1, 1, 1, 1]}, *rec["seats"][1:]]},
        "bool die": {**rec, "seats": [{**rec["seats"][0], "dice": [True, 1, 1, 1, 1]}, *rec["seats"][1:]]},
        "dice count mismatch": {**rec, "seats": [{**rec["seats"][0], "dice": [1, 2]}, *rec["seats"][1:]]},
        "remaining too big": {**rec, "seats": [{**rec["seats"][0], "remaining": 9}, *rec["seats"][1:]]},
        "bad style": {**rec, "seats": [rec["seats"][0], {**rec["seats"][1], "style": "wild"}, rec["seats"][2]]},
        "two humans": {**rec, "seats": [rec["seats"][0], {**rec["seats"][1], "human": True}, rec["seats"][2]]},
        "human not first": {**rec, "seats": [rec["seats"][1], rec["seats"][0], rec["seats"][2]]},
        "duplicate ids": {**rec, "seats": [rec["seats"][0], rec["seats"][1], {**rec["seats"][2], "id": rec["seats"][1]["id"]}]},
        "turn out of range": {**rec, "turn": 9}, "bad phase": {**rec, "phase": "dancing"},
        "half a bid": {**rec, "bid_face": 0, "bid_count": 2}, "bid beyond the dice": {**rec, "bid_count": 99},
        "bad winner": {**rec, "winner": "nobody"}, "reveal during bidding": {**rec, "reveal": rrec["reveal"]},
        "wild not bool": {**rec, "wild_ones": 1}, "empty game id": {**rec, "game_id": ""},
        "reveal missing": {**rrec, "reveal": None}, "reveal junk": {**rrec, "reveal": {"hands": {}}},
        "reveal bad hand": {**rrec, "reveal": {**rrec["reveal"], "hands": {"you": [9]}}},
        "reveal unknown seat": {**rrec, "reveal": {**rrec["reveal"], "loser": "ghost"}},
        "dice held between rounds": {**rrec, "seats": [{**rrec["seats"][0], "dice": [1]}, *rrec["seats"][1:]]},
        "over without end": {**rrec, "phase": "over"},
    }
    return list(cases.items())


@pytest.mark.parametrize("name,record", _mutations(), ids=[n for n, _ in _mutations()])
def test_a_damaged_saved_game_is_refused_not_trusted(name, record):
    with pytest.raises(ValueError, match="saved Liar's Dice game is not valid"):
        Game.from_record(copy.deepcopy(record))


def test_the_next_round_cannot_open_on_a_seat_that_is_out():
    g = stacked([[1, 2], [4], [6, 6]])
    g.turn = 1
    g.bid(1, 2, 4)
    g.call(2)
    rec = g.to_record()
    rec["next_first"] = 1
    with pytest.raises(ValueError, match="out"):
        Game.from_record(rec)
