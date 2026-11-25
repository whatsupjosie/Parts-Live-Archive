"""Liar's Dice routes (modules/liars_dice_api.py): who, saving, stale clicks, hidden dice, a damaged save.

The router is mounted on a real FastAPI app and driven with real bearer tokens, the way the page does it.
"""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from modules.auth import create_access_token
from modules.liars_dice import Game
from modules.liars_dice_api import create_liars_dice_router

URL = "/api/minigames/liarsdice"


@pytest.fixture()
def api(tmp_path):
    app = FastAPI()
    router = create_liars_dice_router(tmp_path)
    app.include_router(router)
    with TestClient(app) as client:
        client.data_dir, client.router = tmp_path, router
        yield client


def auth(user):
    return {"Authorization": "Bearer " + create_access_token({"sub": user, "role": "owner"})}


JOSIE = auth("josie")


def start(api, headers=JOSIE, **body):
    return api.post(URL + "/start", headers=headers, json={"opponents": 2, **body})


def saved(api, user="josie"):
    return Game.from_record(api.router.store.get(user)["progress"]["game"])


def test_you_need_to_be_signed_in_when_auth_is_enforced(api, monkeypatch):
    monkeypatch.setenv("PUBCAST_ENFORCE_AUTH", "1")
    for method, path in [("get", ""), ("post", "/start"), ("post", "/bid"), ("post", "/call"), ("post", "/next"), ("delete", "")]:
        r = getattr(api, method)(URL + path, **({"json": {}} if method == "post" else {}))
        assert r.status_code == 401, (method, path, r.text)


def test_there_is_no_game_until_you_start_one(api):
    assert api.get(URL, headers=JOSIE).json() == {"ok": True, "game": None, "events": []}
    for path in ("/bid", "/call", "/next"):
        r = api.post(URL + path, headers=JOSIE, json={"expected_move": 0, "count": 1, "face": 1})
        assert r.status_code == 404 and "Start one first" in r.json()["detail"]


def test_starting_gives_you_the_table_with_only_your_own_dice(api):
    r = start(api)
    assert r.status_code == 200
    body = r.json()
    g = body["game"]
    assert body["events"][0]["type"] == "round" and g["turn"] == "you" and g["phase"] == "bidding"
    assert len(g["seats"]) == 3 and [s["remaining"] for s in g["seats"]] == [5, 5, 5] and len(g["you"]["dice"]) == 5
    wire = json.dumps(body)
    host = api.router.store.get("josie")["progress"]["game"]
    for seat in host["seats"][1:]:                                   # no opponent's hand appears anywhere in the reply
        assert json.dumps(seat["dice"]) not in wire.replace(json.dumps(g["you"]["dice"]), "")
    assert '"hands"' not in wire and '"style"' not in wire
    assert "dice" not in {k for s in g["seats"] for k in s}
    assert api.get(URL, headers=JOSIE).json()["game"] == g          # and it is still there on a fresh request


@pytest.mark.parametrize("body", [{}, {"opponents": 0}, {"opponents": 4}, {"opponents": "2"}, {"opponents": True},
                                  {"opponents": 2, "wild_ones": "yes"}, {"opponents": 2, "replace": "yes"}])
def test_a_bad_start_is_refused_and_nothing_is_saved(api, body):
    r = api.post(URL + "/start", headers=JOSIE, json=body)
    assert r.status_code == 400 and isinstance(r.json()["detail"], str)
    assert api.get(URL, headers=JOSIE).json()["game"] is None


@pytest.mark.parametrize("raw", [b"{nope", b"[1]", b'"x"', b"x" * 5000])
def test_a_malformed_request_is_refused(api, raw):
    r = api.post(URL + "/start", headers={**JOSIE, "Content-Type": "application/json"}, content=raw)
    assert r.status_code in (400, 413)


def test_starting_over_a_live_game_needs_you_to_say_so(api):
    first = start(api).json()["game"]
    r = start(api)
    assert r.status_code == 409 and r.json()["detail"]["game"]["game_id"] == first["game_id"]
    assert api.get(URL, headers=JOSIE).json()["game"]["game_id"] == first["game_id"]      # untouched
    again = start(api, replace=True)
    assert again.status_code == 200 and again.json()["game"]["game_id"] != first["game_id"]


def test_a_bid_is_played_and_the_opponents_answer(api):
    g = start(api).json()["game"]
    r = api.post(URL + "/bid", headers=JOSIE, json={"count": 1, "face": 2, "expected_move": g["move"]})
    assert r.status_code == 200
    body = r.json()
    kinds = [e["type"] for e in body["events"]]
    assert kinds[0] == "bid" and body["events"][0]["seat"] == "you"
    assert body["game"]["move"] > g["move"]
    # it stopped because the human must act, or because a round was called and is waiting to be shown
    assert body["game"]["turn"] == "you" or body["game"]["phase"] in ("reveal", "over")
    assert saved(api).view() == body["game"]                          # what you were told is what was kept


def test_an_illegal_bid_is_refused_with_the_rule_and_changes_nothing(api):
    g = start(api).json()["game"]
    before = api.router.store.get("josie")
    for payload in ({"count": 0, "face": 2}, {"count": 99, "face": 2}, {"count": 1, "face": 7}, {"count": "1", "face": 2},
                    {"count": 1}, {"face": 2}, {"count": True, "face": 2}):
        r = api.post(URL + "/bid", headers=JOSIE, json={**payload, "expected_move": g["move"]})
        assert r.status_code == 400 and isinstance(r.json()["detail"], str), payload
    assert api.router.store.get("josie") == before


def test_you_cannot_call_with_no_bid_on_the_table(api):
    g = start(api).json()["game"]
    r = api.post(URL + "/call", headers=JOSIE, json={"expected_move": g["move"]})
    assert r.status_code == 400 and "no bid" in r.json()["detail"]


def test_a_stale_click_is_refused_with_the_current_table(api):
    g = start(api).json()["game"]
    ok = api.post(URL + "/bid", headers=JOSIE, json={"count": 1, "face": 2, "expected_move": g["move"]})
    assert ok.status_code == 200
    # the same click arrives again (double tap, a second tab)
    stale = api.post(URL + "/bid", headers=JOSIE, json={"count": 1, "face": 3, "expected_move": g["move"]})
    assert stale.status_code == 409
    d = stale.json()["detail"]
    assert d["game"] == ok.json()["game"] and "moved on" in d["message"]
    for bad in (None, "0", 1.5, True, -1):
        r = api.post(URL + "/call", headers=JOSIE, json={"expected_move": bad} if bad is not None else {})
        assert r.status_code in (400, 409)


def _play_until(api, want):
    """Human bids the lowest legal raise or calls once a bid is on the table, until `want(game)` holds."""
    game = api.get(URL, headers=JOSIE).json()["game"]
    for _ in range(400):
        if want(game):
            return game
        if game["phase"] == "reveal":
            r = api.post(URL + "/next", headers=JOSIE, json={"expected_move": game["move"]})
        elif game["bid"] is not None and game["can_call"] and (not game["can_raise"] or game["bid"]["count"] >= 4):
            r = api.post(URL + "/call", headers=JOSIE, json={"expected_move": game["move"]})
        else:
            c, f = (1, 1) if game["bid"] is None else ((game["bid"]["count"], game["bid"]["face"] + 1) if game["bid"]["face"] < 6
                                                       else (game["bid"]["count"] + 1, 1))
            r = api.post(URL + "/bid", headers=JOSIE, json={"count": c, "face": f, "expected_move": game["move"]})
        assert r.status_code == 200, r.text
        game = r.json()["game"]
    raise AssertionError("never reached the wanted state")


def test_a_call_reveals_every_cup_then_the_next_round_hides_them(api):
    start(api, opponents=1)
    g = _play_until(api, lambda g: g["phase"] == "reveal")
    rv = g["reveal"]
    assert set(rv["hands"]) == {"you", "seat2"} and rv["loser"] in rv["hands"]
    assert len(rv["hands"]["you"]) + len(rv["hands"]["seat2"]) == sum(len(h) for h in rv["hands"].values())
    nxt = api.post(URL + "/next", headers=JOSIE, json={"expected_move": g["move"]})
    assert nxt.status_code == 200
    assert nxt.json()["game"]["reveal"] is None and "hands" not in json.dumps(nxt.json()["game"])
    # asking for the next round when none is waiting is refused
    again = api.post(URL + "/next", headers=JOSIE, json={"expected_move": nxt.json()["game"]["move"]})
    assert again.status_code == 400


def test_a_whole_game_can_be_played_through_the_routes_to_a_winner(api):
    start(api, opponents=1)
    g = _play_until(api, lambda g: g["phase"] == "over")
    assert g["winner"] in ("you", "seat2", None) and sum(1 for s in g["seats"] if s["remaining"]) >= 1
    assert api.post(URL + "/bid", headers=JOSIE, json={"count": 1, "face": 1, "expected_move": g["move"]}).status_code == 400
    # a finished game may be replaced without saying so
    assert start(api).status_code == 200


def test_each_person_has_their_own_table(api):
    mine = start(api).json()["game"]
    theirs = api.get(URL, headers=auth("someone-else")).json()
    assert theirs["game"] is None
    other = start(api, headers=auth("someone-else"), opponents=3).json()["game"]
    assert len(other["seats"]) == 4 and api.get(URL, headers=JOSIE).json()["game"]["game_id"] == mine["game_id"]
    # leaving only forgets your own
    assert api.delete(URL, headers=JOSIE).json() == {"ok": True, "game": None, "events": []}
    assert api.get(URL, headers=JOSIE).json()["game"] is None
    assert api.get(URL, headers=auth("someone-else")).json()["game"]["game_id"] == other["game_id"]


def test_the_game_is_still_there_when_the_host_is_restarted(api, tmp_path):
    g = start(api).json()["game"]
    api.post(URL + "/bid", headers=JOSIE, json={"count": 1, "face": 2, "expected_move": g["move"]})
    before = api.get(URL, headers=JOSIE).json()["game"]
    app2 = FastAPI()
    app2.include_router(create_liars_dice_router(tmp_path))          # a new host on the same data folder
    with TestClient(app2) as c2:
        assert c2.get(URL, headers=JOSIE).json()["game"] == before


def test_a_damaged_save_does_not_lock_you_out_of_the_table(api, caplog):
    start(api)
    path = api.router.store.path_for("josie")
    for damage in ("{not json", json.dumps({"progress": {"game": {"v": 99}}}), json.dumps({"progress": {"game": "x"}})):
        path.write_text(damage, encoding="utf-8")
        with caplog.at_level("WARNING"):
            assert api.get(URL, headers=JOSIE).json()["game"] is None
        assert start(api).status_code == 200                          # and a new game starts normally
        assert api.get(URL, headers=JOSIE).json()["game"] is not None
    assert any("unreadable Liar's Dice save" in m for m in caplog.messages)


def test_the_saved_file_holds_the_hidden_dice_but_only_on_the_host(api):
    start(api)
    raw = api.router.store.path_for("josie").read_text(encoding="utf-8")
    assert '"dice"' in raw and "josie" in raw
    assert not list(api.data_dir.rglob("*.tmp"))                      # atomic replace left nothing behind
