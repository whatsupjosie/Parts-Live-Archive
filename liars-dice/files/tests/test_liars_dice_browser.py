"""Liar's Dice, as Josie uses it: the real PubCast host (run_reach.py), the real /walk page, a real Chromium.

1. Walk into the Vortex Bar and click the booth table; the bubble offers the game; the table opens.
2. Sit down against two opponents. Only your own dice are on the page. Walking keys do nothing while the table is open.
3. Play by clicking: bid, get answered, call Liar, see every cup turned over, go to the next round.
4. Esc leaves the table; the game is still there. Stop PubCast, start it again, and the same game resumes.
5. With the host unreachable the table says so plainly and recovers when it is back.
Skips when Playwright/Chromium is unavailable.
"""
from __future__ import annotations

import json
import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

sync_api = pytest.importorskip("playwright.sync_api")

ROOT = Path(__file__).resolve().parents[1]
SHOTS = Path(os.environ.get("LIARSDICE_SHOTS", ""))


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Host:
    def __init__(self, data):
        self.data, self.port, self.proc = data, _free_port(), None
        self.base = f"http://127.0.0.1:{self.port}"

    def start(self):
        env = dict(os.environ)
        for k in ("PUBCAST_ENFORCE_AUTH", "PUBCAST_JWT_SECRET", "PUBCAST_DATA_DIR"):
            env.pop(k, None)
        self.log = open(self.data / "host.log", "a", encoding="utf-8")
        self.proc = subprocess.Popen([sys.executable, "run_reach.py", "--port", str(self.port), "--data-dir", str(self.data),
                                      "--no-browser", "--no-legacy-2i"], cwd=str(ROOT), env=env, stdout=self.log, stderr=subprocess.STDOUT)
        deadline = time.time() + 90
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise AssertionError("PubCast exited during startup")
            try:
                if httpx.get(self.base + "/health", timeout=0.5).status_code == 200:
                    return
            except httpx.RequestError:
                pass
            time.sleep(0.2)
        raise AssertionError("PubCast did not start")

    def stop(self):
        self.proc.terminate()
        try:
            self.proc.wait(20)
        except subprocess.TimeoutExpired:
            self.proc.kill(); self.proc.wait(5)
        self.log.close()


def shot(page, name):
    if str(SHOTS) and SHOTS.is_dir():
        page.screenshot(path=str(SHOTS / name))


def open_table(page):
    """Click the booth table the way a person does: at its place in the picture."""
    page.evaluate("window.PubCastWalk.goTo('vortex_bar', 'green_room')")
    page.wait_for_function("window.PubCastWalk.S.room.id === 'vortex_bar'")
    at = page.evaluate("() => window.PubCastWalk.S.view.hotspots.find(h => h.id === 'booth-table-dice').at")
    sx, sy = page.evaluate("(p) => window.PubCastWalk.toScreen(p)", at)
    box = page.locator("#c").bounding_box()
    page.mouse.click(box["x"] + sx, box["y"] + sy)
    page.wait_for_selector("#bubble:not([hidden])")
    return page.locator("#bubble")


def table_text(page):
    return page.inner_text("#ldice")


def test_the_booth_table_plays_liars_dice_and_the_game_survives_a_restart(tmp_path):
    host = Host(tmp_path)
    host.start()
    try:
        password = secrets.token_urlsafe(20)
        owner = httpx.post(host.base + "/api/auth/setup", json={"username": "josie-dice", "password": password, "confirm_password": password}, timeout=10).json()
        token = owner["access_token"]
        init = ("sessionStorage.setItem('pubcast_token', %s); sessionStorage.setItem('pubcast_user', JSON.stringify({username:'josie-dice',user_id:'josie-dice',role:'owner'}));"
                % json.dumps(token))

        def session_page(browser):
            ctx = browser.new_context(viewport={"width": 1400, "height": 860})
            ctx.add_init_script(init)
            pg = ctx.new_page(); pg.problems = []
            pg.on("pageerror", lambda e: pg.problems.append(str(e)))
            pg.on("console", lambda m: pg.problems.append(m.text) if m.type == "error" and "favicon" not in m.text else None)
            pg.goto(host.base + "/walk?live=bar")
            pg.wait_for_function("document.documentElement.dataset.ready === '1' && window.PubCastLive && window.PubCastLive.state.host === true", timeout=30000)
            return ctx, pg

        with sync_api.sync_playwright() as p:
            try:
                browser = p.chromium.launch(args=["--no-sandbox"])
            except Exception as exc:
                pytest.skip(f"no Chromium available: {exc}")
            try:
                ctx, page = session_page(browser)
                bubble = open_table(page)
                assert "Booth table" in bubble.inner_text() and "Not wired yet" not in bubble.inner_text()
                shot(page, "01_bubble.png")
                bubble.get_by_role("button", name="Play Liar’s Dice").click()
                page.wait_for_selector("#ldice:not([hidden])")
                page.wait_for_selector("text=Sit down and roll")
                shot(page, "02_setup.png")

                # walking keys do nothing while the table is open
                before = page.evaluate("window.PubCastWalk.S.pos.slice()")
                for key in ("ArrowRight", "d", "ArrowDown"):
                    page.keyboard.down(key); page.wait_for_timeout(300); page.keyboard.up(key)
                assert page.evaluate("window.PubCastWalk.S.pos") == pytest.approx(before, abs=0.01)

                page.check("input[name=ld-opp][value='2']")
                page.click("text=Sit down and roll")
                page.wait_for_selector("#ldice .ld-seat")
                assert page.locator("#ldice .ld-seat").count() == 3
                shot(page, "03_round1.png")
                # only your own five dice are on the page
                page.wait_for_selector("#ldice .ld-hand .ld-die")
                assert page.locator("#ldice .ld-hand .ld-die").count() == 5
                api = lambda: httpx.get(host.base + "/api/minigames/liarsdice", headers={"Authorization": "Bearer " + token}).json()["game"]
                first = api()
                assert first["you"]["dice"] == page.eval_on_selector_all("#ldice .ld-hand .ld-die", "els => els.map(e => Number(e.getAttribute('aria-label').split(' ').pop()))")
                assert "seat3" in table_text(page).lower().replace(" ", "") or "Seat 3" in table_text(page)

                # play: bid the lowest raise, and call once the standing bid is high, until a round is revealed
                revealed = False
                for _ in range(80):
                    page.wait_for_function("""() => !document.querySelector('#ldice .ld-bid[aria-hidden=true]') &&
                        document.querySelector('#ldice .ld-actions button:not(:disabled)')""", timeout=30000)
                    if page.locator("#ldice").get_by_role("button", name="Next round").count() or page.locator("#ldice .ld-hands").count():
                        revealed = True; break
                    call, bid = page.locator("#ldice .ld-call"), page.locator("#ldice .ld-bid-btn:not(:disabled)")
                    if call.count() and (not bid.count() or _bid_is_high(page)):
                        call.click()
                    else:
                        bid.click()
                    page.wait_for_timeout(150)
                assert revealed, "no round was ever revealed"
                page.wait_for_timeout(1000)                                                         # let the dice finish tumbling
                shot(page, "04_reveal.png")
                text = table_text(page)
                assert "Cups up" in text or "called" in text or "call " in text
                # every cup is turned over: each seat that still has dice shows its hand, and the matching dice are marked
                game = api()
                assert game["phase"] in ("reveal", "over")
                rows = page.locator("#ldice .ld-hands .row")
                assert rows.count() == len(game["reveal"]["hands"])
                shown = page.eval_on_selector_all("#ldice .ld-hands .row", "rows => rows.map(r => r.querySelectorAll('.ld-die').length)")
                assert shown == [len(h) for h in game["reveal"]["hands"].values()]
                assert page.locator("#ldice .ld-die.hit, #ldice .ld-die.miss, #ldice .ld-die.wild").count() == sum(shown)

                if game["phase"] == "reveal":
                    page.locator("#ldice").get_by_role("button", name="Next round").click()
                    page.wait_for_function("() => document.querySelectorAll('#ldice .ld-hands').length === 0", timeout=20000)
                    assert api()["reveal"] is None
                    # the other cups are hidden again: only your own dice are on the page
                    assert page.locator("#ldice .ld-hand .ld-die").count() == api()["seats"][0]["remaining"]

                # Esc leaves the table and keeps the game
                gid = api()["game_id"]
                page.keyboard.press("Escape")
                page.wait_for_selector("#ldice", state="hidden")
                assert api()["game_id"] == gid
                assert page.evaluate("window.PubCastWalk.S.room.id") == "vortex_bar"                 # Esc did not walk you back a room
                assert page.problems == [], page.problems
                ctx.close()
            finally:
                browser.close()

        # quit PubCast, start it again, sit down again: the same game is waiting
        host.stop()
        host.start()
        with sync_api.sync_playwright() as p:
            browser = p.chromium.launch(args=["--no-sandbox"])
            try:
                ctx, page = session_page(browser)
                bubble = open_table(page)
                bubble.get_by_role("button", name="Play Liar’s Dice").click()
                page.wait_for_selector("#ldice .ld-seat")
                assert page.locator("#ldice .ld-seat").count() == 3
                resumed = httpx.get(host.base + "/api/minigames/liarsdice", headers={"Authorization": "Bearer " + token}).json()["game"]
                assert resumed["game_id"] == gid
                assert "Welcome back" in page.inner_text("#ldice [role=status]")
                shot(page, "05_resumed.png")

                # the host unreachable: the table says so, keeps your place, and recovers
                assert page.problems == [], page.problems
                page.keyboard.press("Escape")
                page.route("**/api/minigames/liarsdice**", lambda route: route.abort())
                page.evaluate("window.PubCastLiarsDice.open()")
                page.wait_for_selector("#ldice [role=alert]:not([hidden])")
                assert "Can't reach the PubCast host" in page.inner_text("#ldice [role=alert]")
                shot(page, "06_offline.png")
                page.unroute("**/api/minigames/liarsdice**")
                page.problems[:] = [m for m in page.problems if "ERR_FAILED" not in m]       # the failed request we caused on purpose
                page.locator("#ldice").get_by_role("button", name="Try again").click()
                page.wait_for_selector("#ldice .ld-seat")
                assert page.locator("#ldice [role=alert]:not([hidden])").count() == 0

                # forgetting takes two clicks and really forgets
                page.click("#ldice .ld-forget")
                page.click("#ldice .ld-forget-yes")
                page.wait_for_selector("text=Sit down and roll")
                assert httpx.get(host.base + "/api/minigames/liarsdice", headers={"Authorization": "Bearer " + token}).json()["game"] is None
                assert page.problems == [], page.problems
                ctx.close()
            finally:
                browser.close()
    finally:
        host.stop()


def _bid_is_high(page):
    """Call once the standing bid is 4 or more dice, otherwise keep raising by the minimum."""
    return bool(page.evaluate("""() => { const m = /(\\d+) \\u00D7/.exec((document.querySelector('#ldice .ld-table > .ld-bid') || {}).textContent || '');
                                         return !!m && Number(m[1]) >= 4; }"""))
