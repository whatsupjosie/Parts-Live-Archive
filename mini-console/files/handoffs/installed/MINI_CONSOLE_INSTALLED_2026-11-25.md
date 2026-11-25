# Installed: On-set mini console (Nov 21) (2026-11-25)

**Status: installed in main-assembly and proven. Copied to Parts-Live-Archive as `mini-console/`.**

## What it is

The on-set mini console recovered and wired to the current switcher: a small console page (static/mini_console.html and mini_console.js, with its reference picture) and an "On-set Mini Console" link on the director switcher's rail.

## Where it came from

pre-dec branch `codex/restore-mini-console` (59675d6), installed through the studio line's merge c805cca9 (Nov 22). Nothing in it was changed on the way in. The original stays in its storage repo.

## What it changed in main-assembly

Installed on top of main-assembly ec187b8a: 4 files changed, 179 insertions(+). Every file it added or changed is in Parts-Live-Archive under `mini-console/files/`, and the exact change is `mini-console/INSTALL.patch`.

## How it was proven (Nov 25, in main-assembly after installing)

- Its own tests (the test files this piece added or changed: 0): no test files changed by this piece. Python 3.13 with fastapi 0.115.14, starlette 0.41.3, httpx 0.28.1. scikit-learn is not installed (not okayed), so a stand-in that fails loudly if anything actually calls it let the real host start. Browser tests that need playwright are skipped; async tests run without pytest-asyncio.
- Compile and syntax: py files 0  js 1  json 0; all clean.
- The app boots with it: main.py builds the app with 204 routes. Packages not installed here and not okayed to install (scikit-learn, numpy, bcrypt, psutil, websockets and some Windows-only ones) are empty stand-ins for this check only, so it proves the wiring, not those features.

## Notes

No tests of its own (page code only). Extra check: the app was booted and /director-switcher and /static/mini_console.html were served (both 200), and every /static/ file each page names was fetched (14, none missing). mini_console.js passes node --check. Not clicked in a real browser.
