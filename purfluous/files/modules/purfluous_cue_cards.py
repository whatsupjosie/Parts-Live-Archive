"""Sir Purfluous's cue cards: one deck that serves both of his modes.

The deck lives in data/characters/sir_purfluous.json under "cue_cards", the
same file as his offline craft answers. Josie edits the lines there; nothing
here holds dialogue.

Offline (no speaking model): ``compose(message, history)`` reads the message,
picks cards that fit its topic and mood, skips any card whose text he said
recently, and joins two or three of them into one line. The choice is seeded
from the message and the recent history, so the same moment gives the same
line (testable) while a new moment gives a new combination.

Online (speaking model available): ``cues_for(message, history)`` returns the
same picks as a short private stage note. The model may use them or ignore
them; they steer him, they do not script him.

When someone is hurting he uses one plain card and nothing else: no openers,
no jokes, no stage business, and never a pointer to services.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

DECK_PATH = Path(__file__).resolve().parent.parent / "data" / "characters" / "sir_purfluous.json"
RECENT_WINDOW = 24

# Words that mean the person is hurting. The EQ reader adds its own judgement
# when it is importable; these keep him safe when it is not.
_HURT_WORDS = re.compile(
    r"\b(died|dead|death|passed away|funeral|grief|grieving|lost my|miss (him|her|them)|"
    r"broke up|breakup|divorce|dumped|left me|alone|lonely|nobody|no one cares|"
    r"hurt|hurting|crying|cried|can't stop crying|heartbroken|depressed|"
    r"hate myself|worthless|give up on|giving up|can't do this|cannot do this|"
    r"hit me|abused|attacked|scared of (him|her|them))\b"
)
_HURT_CONTEXTS = {"loss", "violence", "betrayal", "abandonment", "pain"}


@lru_cache(maxsize=1)
def _load(path: str = str(DECK_PATH)) -> Mapping:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    deck = data.get("cue_cards") or {}
    topics = {name: [re.compile(p, re.IGNORECASE) for p in pats] for name, pats in deck.get("topics", {}).items()}
    return {"topics": topics, "cards": list(deck.get("cards", [])), "slots": dict(deck.get("slots", {}))}


def reload() -> None:
    """Drop the cached deck so edits to the JSON are picked up."""
    _load.cache_clear()
    _reader_and_selector.cache_clear()


# Catalog cards he never uses. Josie's ruling (2026-11-24): "Fuck the crisis
# resources." These are the catalog's crisis_support cards (hotlines, RAINN,
# "Are you safe right now?"). The catalog itself is unchanged for other bots.
_CATALOG_EXCLUDED_TYPES = {"crisis_support"}
_CATALOG_SOLO = {"fatigue", "autonomy", "identity", "interpersonal", "betrayal"}


@lru_cache(maxsize=1)
def _reader_and_selector():
    try:
        from companion_support.eq_disambiguation_engine import ContextDisambiguator
        from modules.wired.eq_card_selector import ContextAwareCardSelector
        return ContextDisambiguator(), ContextAwareCardSelector()
    except Exception as exc:
        logger.debug("EQ card catalog unavailable: %s", exc)
        return None, None


def _catalog_read(message: str) -> Tuple[str, List[dict]]:
    """Read the message with the EQ reader and return (situation, catalog cards that fit it).

    Uses the card catalog's own picker (modules/wired/eq_card_selector.py), minus
    crisis cards. Each call is read fresh, with no carried-over history.
    """
    reader, selector = _reader_and_selector()
    if reader is None:
        return "unclear", []
    try:
        result = reader.analyze(message, "purfluous_cue_cards", include_history=False)
        picks = selector.select_response(result, num_options=len(selector.library.cards))
    except Exception as exc:  # the reader is advisory; never let it break a reply
        logger.debug("EQ reader failed: %s", exc)
        return "unclear", []
    context = getattr(result.primary_context, "value", "unclear") or "unclear"
    cards = [{"id": f"catalog:{c.id}", "kind": "catalog", "topics": [context], "moods": [], "text": c.text}
             for c, _score in picks if c.response_type not in _CATALOG_EXCLUDED_TYPES]
    return context, cards


def _eq_context(message: str) -> str:
    return _catalog_read(message)[0]


def _eq_hurt(message: str) -> bool:
    return _eq_context(message) in _HURT_CONTEXTS


def read(message: str) -> Tuple[List[str], str]:
    """Return (topics, mood) for a message. Mood is hurt, setback, celebrate or neutral."""
    deck = _load()
    text = message.strip()
    topics = [name for name, pats in deck["topics"].items() if any(p.search(text) for p in pats)]
    if _HURT_WORDS.search(text.casefold()) or _eq_hurt(text):
        mood = "hurt"
    elif "setback" in topics:
        mood = "setback"
    elif "celebrate" in topics:
        mood = "celebrate"
    else:
        mood = "neutral"
    return topics, mood


def _recent_text(history: Sequence[Mapping[str, str]]) -> str:
    said = []
    for entry in list(history)[-RECENT_WINDOW:]:
        if isinstance(entry, Mapping):
            said.append(str(entry.get("text", "")))
        else:
            said.append(str(entry))
    return "\n".join(said).casefold()


def _fill(text: str, slots: Mapping[str, str]) -> str:
    for key, value in slots.items():
        text = text.replace(key, value)
    return text


def _pick(cards: List[dict], seed: bytes, salt: str, recent: str, slots: Mapping[str, str]) -> Optional[dict]:
    if not cards:
        return None
    fresh = [c for c in cards if _fill(c["text"], slots).casefold() not in recent] or cards
    digest = hashlib.sha256(seed + salt.encode()).digest()
    return fresh[int.from_bytes(digest[:4], "big") % len(fresh)]


def select(message: str, history: Sequence[Mapping[str, str]] = ()) -> Dict[str, object]:
    """Choose the cards for this moment. Returns topics, mood and the chosen cards in speaking order."""
    deck = _load()
    topics, mood = read(message)
    slots = deck["slots"]
    recent = _recent_text(history)
    seed = hashlib.sha256((message.strip().casefold() + "\x00" + recent[-2000:]).encode()).digest()
    cards = deck["cards"]

    def fits(card: dict, kind: str) -> bool:
        return card["kind"] == kind and mood in card["moods"]

    context, catalog = _catalog_read(message)

    if mood == "hurt":
        pool = [c for c in cards if fits(c, "plain")] + catalog
        plain = _pick(pool, seed, "plain", recent, slots)
        return {"topics": topics, "mood": mood, "cards": [plain] if plain else []}

    if context in _CATALOG_SOLO and catalog and not (set(topics) - {"celebrate", "setback"}):
        card = _pick(catalog, seed, "catalog", recent, slots)
        return {"topics": topics, "mood": mood, "cards": [card]}

    spoken = [t for t in topics if t not in ("celebrate", "setback")] or [t for t in topics]
    beats = []
    if mood in ("celebrate", "setback"):  # a win or a stumble outranks the subject it happened in
        beats = [c for c in cards if c["kind"] == "beat" and mood in c["topics"]]
        if mood == "celebrate":
            beats += [c for c in catalog if context in ("achievement", "milestone")]
    beats = beats or [c for c in cards if c["kind"] == "beat" and mood in c["moods"] and set(c["topics"]) & set(spoken)]
    if not beats:
        return {"topics": topics, "mood": mood, "cards": []}
    beat = _pick(beats, seed, "beat", recent, slots)
    if beat.get("solo"):  # a card that ends on its own beat (a cut-off, an exit) stands alone
        return {"topics": topics, "mood": mood, "cards": [beat]}
    chosen = []
    roll = seed[0]
    if roll % 3 != 0:  # two times in three he opens before the beat
        opener = _pick([c for c in cards if fits(c, "opener")], seed, "opener", recent, slots)
        if opener:
            chosen.append(opener)
    chosen.append(beat)
    tail = roll % 4
    if tail == 1:
        extra = _pick([c for c in cards if fits(c, "aside")], seed, "aside", recent, slots)
    elif tail == 2:
        extra = _pick([c for c in cards if fits(c, "business")], seed, "business", recent, slots)
    elif tail == 3:
        extra = _pick([c for c in cards if fits(c, "closer")], seed, "closer", recent, slots)
    else:
        extra = None
    if extra:
        chosen.append(extra)
    return {"topics": topics, "mood": mood, "cards": chosen}


def _join(parts: List[str]) -> str:
    out = ""
    for part in parts:
        part = part.strip()
        if not out:
            out = part
        elif out.endswith(",") and part[:1].isupper() and not re.match(r"I\b", part):
            out = out + " " + part[0].lower() + part[1:]
        else:
            out = f"{out} {part}"
    return out


def compose(message: str, history: Sequence[Mapping[str, str]] = ()) -> Optional[str]:
    """Build one offline line from his cards, or None if no card fits this message."""
    if not isinstance(message, str) or not message.strip():
        return None
    picked = select(message, history)
    if not picked["cards"]:
        return None
    slots = _load()["slots"]
    return _join([_fill(card["text"], slots) for card in picked["cards"]])


def cues_for(message: str, history: Sequence[Mapping[str, str]] = ()) -> Optional[str]:
    """A private note for the speaking model: the cards for this moment, to use or ignore."""
    if not isinstance(message, str) or not message.strip():
        return None
    picked = select(message, history)
    if not picked["cards"]:
        return None
    slots = _load()["slots"]
    lines = " | ".join(_fill(card["text"], slots) for card in picked["cards"])
    if picked["mood"] == "hurt":
        return ("Cue (private): this person is hurting. Be plain and warm, no jokes, no stage business, "
                f"do not point them to services. A line in your voice: {lines}")
    return (f"Cue cards (private; use, rephrase or ignore, never read them out as a list): {lines}")
