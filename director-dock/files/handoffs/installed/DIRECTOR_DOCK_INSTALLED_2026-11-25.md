# Installed: Director Dock restore and Control Room routing (Nov 21) (2026-11-25)

**Status: installed in main-assembly and proven. Copied to Parts-Live-Archive as `director-dock/`.**

## What it is

The Control Room's Director Dock brought back to how it worked in The Prime, plus fixes to how the Control Room finds its files and the order the studio starts up in. Page code only (control.html, control_stations.js, the director switcher, the camera pool controller, the 2.5D production app, the editor sync station).

## Where it came from

pre-dec branch `codex/restore-director-dock` (f66f285, two commits, which holds `codex/live-production` 17b29be), installed through the studio line's merge 2d0be28d (Nov 22). Nothing in it was changed on the way in. The original stays in its storage repo.

## What it changed in main-assembly

Installed on top of main-assembly 89012cf7: 6 files changed, 125 insertions(+), 68 deletions(-). Every file it added or changed is in Parts-Live-Archive under `director-dock/files/`, and the exact change is `director-dock/INSTALL.patch`.

## How it was proven (Nov 25, in main-assembly after installing)

- Its own tests (the test files this piece added or changed: 0): no test files changed by this piece. Python 3.13 with fastapi 0.115.14, starlette 0.41.3, httpx 0.28.1. scikit-learn is not installed (not okayed), so a stand-in that fails loudly if anything actually calls it let the real host start. Browser tests that need playwright are skipped; async tests run without pytest-asyncio.
- Compile and syntax: py files 0  js 4  json 0; all clean.
- The app boots with it: main.py builds the app with 204 routes. Packages not installed here and not okayed to install (scikit-learn, numpy, bcrypt, psutil, websockets and some Windows-only ones) are empty stand-ins for this check only, so it proves the wiring, not those features.

## Notes

This piece has no tests of its own (it changes page code only). Extra check run instead: the app was booted and /control, /director-switcher and /studio-control were each served (all 200), and every /static/ script, stylesheet and picture each page names was fetched: 43 files, none missing. The four changed scripts pass node --check. Not clicked in a real browser (playwright is not installed).
