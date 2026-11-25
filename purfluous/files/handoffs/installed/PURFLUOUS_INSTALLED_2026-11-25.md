# Installed: Sir Purfluous: salvage, AI wiring, canon and cue cards (Nov 24) (2026-11-25)

**Status: installed in main-assembly and proven. Copied to Parts-Live-Archive as `purfluous/`.**

## What it is

Sir Purfluous as a working character: his character material salvaged from the old scene-director file, the owner canon decisions of Nov 24, the AI Purfluous wired to his whole canon with paced interruptions, one cue-card deck for offline lines and online cues (including her TikTok watch line, where Pete cuts him off by saying his name), and a fix to the EQ card catalog reader so cards match. He runs through the bots system (modules/bots.py), which main.py loads.

## Where it came from

pre-dec branch `claude/purfluous-cue-cards-2026-11-24` (f762b3e, eight commits), which holds `claude/purfluous-character-salvage-2026-11-24`, `claude/purfluous-ai-wiring-2026-11-24` and `claude/purfluous-canon-2026-11-24`. Built straight on main (538a40f), so it went in with no conflicts. Nothing in it was changed on the way in. The original stays in its storage repo.

## What it changed in main-assembly

Installed on top of main-assembly 7f234bb6: 14 files changed, 3173 insertions(+), 367 deletions(-). Every file it added or changed is in Parts-Live-Archive under `purfluous/files/`, and the exact change is `purfluous/INSTALL.patch`.

## How it was proven (Nov 25, in main-assembly after installing)

- Its own tests (the test files this piece added or changed: 3): 81 passed, 2 warnings in 0.72s. Python 3.13 with fastapi 0.115.14, starlette 0.41.3, httpx 0.28.1. scikit-learn is not installed (not okayed), so a stand-in that fails loudly if anything actually calls it let the real host start. Browser tests that need playwright are skipped; async tests run without pytest-asyncio.
- Compile and syntax: py files 11  js 0  json 3; all clean.
- The app boots with it: main.py builds the app with 204 routes. Packages not installed here and not okayed to install (scikit-learn, numpy, bcrypt, psutil, websockets and some Windows-only ones) are empty stand-ins for this check only, so it proves the wiring, not those features.

## Notes

The 81 tests drive the real code: his profile and runtime through bots.py, the cue-card deck, interruption pacing, and the EQ reader matching cards. This piece also changes companion_support/eq_disambiguation_engine.py, which is Alex's EQ file. The copy on the alex branch is now older than main-assembly's for that one file.
