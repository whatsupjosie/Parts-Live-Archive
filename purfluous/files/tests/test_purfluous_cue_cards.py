"""Sir Purfluous's cue cards: one deck for both modes (offline lines, online cues)."""
import json
from pathlib import Path

from modules import purfluous_cue_cards as cc
from modules.sir_purfluous_runtime import respond

DECK = json.loads(Path("data/characters/sir_purfluous.json").read_text(encoding="utf-8"))["cue_cards"]


def test_deck_lives_in_his_character_file_and_is_marked_draft():
    assert DECK["status"] == "draft_for_josie_to_edit"
    ids = [c["id"] for c in DECK["cards"]]
    assert len(ids) == len(set(ids)) and len(ids) > 80


def test_josie_lines_are_verbatim():
    texts = [c["text"] for c in DECK["cards"]]
    assert "You'll never know just when or where, but it shall strike. The tiny chair." in texts


def test_composes_from_topic_cards():
    out = respond("is that chair safe to sit in?")
    assert any(w in out.lower() for w in ("chair", "inch"))


def test_scottish_play_ritual():
    assert "scottish play" in respond("Break a leg in Macbeth tonight").lower() or "spit" in respond("Break a leg in Macbeth tonight").lower()


def test_deterministic_but_varies_with_history():
    a = cc.compose("tell me about the ghost")
    assert a == cc.compose("tell me about the ghost")
    seen = {a}
    hist = []
    for _ in range(5):
        line = cc.compose("tell me about the ghost", hist)
        seen.add(line)
        hist.append({"speaker": "Sir Purfluous", "text": line})
    assert len(seen) >= 3


def test_hurt_gets_one_plain_card_no_jokes_no_services():
    topics, mood = cc.read("my dog died last week and I can't stop crying")
    assert mood == "hurt"
    picked = cc.select("my dog died last week and I can't stop crying")
    assert len(picked["cards"]) == 1 and picked["cards"][0]["kind"] == "plain"
    out = respond("my dog died last week and I can't stop crying").lower()
    for banned in ("hotline", "therapist", "counsel", "services", "are you safe"):
        assert banned not in out


def test_unknown_still_honest():
    assert "will not invent" in respond("what is the capital of Mongolia").lower()
    assert cc.compose("what is the capital of Mongolia") is None


def test_online_cue_is_private_note():
    cue = cc.cues_for("nice suit")
    assert cue.startswith("Cue cards (private")
    assert cc.cues_for("what is the capital of Mongolia") is None


def test_no_card_slots_left_unfilled():
    for c in DECK["cards"]:
        assert "{" not in cc._fill(c["text"], DECK["slots"])


def test_he_draws_on_the_eq_card_catalog():
    from modules.wired.eq_card_selector import CardLibrary
    catalog_texts = {c.text for c in CardLibrary().cards.values()}
    assert cc.compose("I am so burned out") in catalog_texts
    assert cc.compose("my best friend lied to me") in catalog_texts


def test_crisis_resource_cards_are_never_his():
    for msg in ("he hit me", "I was shot", "my dad died", "I can't do this anymore", "I lost my mother"):
        _ctx, cards = cc._catalog_read(msg)
        assert all("crisis" not in c["id"] and "trauma_safety" not in c["id"] and "resources" not in c["id"] for c in cards)
        out = respond(msg)
        for banned in ("988", "RAINN", "safe right now", "Lifeline"):
            assert banned not in out, (msg, out)
