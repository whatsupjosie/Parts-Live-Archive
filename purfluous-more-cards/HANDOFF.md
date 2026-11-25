# Installed: Sir Purfluous: more cards, diagnostics and fixes (Nov 24) (2026-11-25)

**Status: installed in main-assembly and proven. Copied to Parts-Live-Archive as `purfluous-more-cards/`.**

## What it is

The rest of the Nov 24 Sir Purfluous work: 160 more cue cards (Sancho rationed, grief answered first), the unclothed-years cards told posh and sideways, defending his craft and wanting his awards with Pete shutting him up, her legend line, sentence frames, Mad Libs and everyday talk, continued stories, and three diagnostics (tools/purfluous_diagnostics.py) with the fixes they found.

## Where it came from

pre-dec branch `claude/purfluous-more-cards-2026-11-24` (a959a35): its eleven Purfluous commits after c403fe6, copied in order with `git cherry-pick -x` (each new commit names its original). Code Collector, which is also on that branch, was left out on purpose: it is not proven yet (see the December setup handoff). None of the eleven commits touch Code Collector, and nothing in them was changed. The original stays in its storage repo.

## What it changed in main-assembly

Installed on top of main-assembly 7431b289: 6 files changed, 5653 insertions(+), 14 deletions(-). Every file it added or changed is in Parts-Live-Archive under `purfluous-more-cards/files/`, and the exact change is `purfluous-more-cards/INSTALL.patch`.

## How it was proven (Nov 25, in main-assembly after installing)

- Its own tests (the test files this piece added or changed: 1): 29 passed, 2 warnings in 0.72s. Python 3.13 with fastapi 0.115.14, starlette 0.41.3, httpx 0.28.1. scikit-learn is not installed (not okayed), so a stand-in that fails loudly if anything actually calls it let the real host start. Browser tests that need playwright are skipped; async tests run without pytest-asyncio.
- Compile and syntax: py files 4  js 0  json 1; all clean.
- The app boots with it: main.py builds the app with 204 routes. Packages not installed here and not okayed to install (scikit-learn, numpy, bcrypt, psutil, websockets and some Windows-only ones) are empty stand-ins for this check only, so it proves the wiring, not those features.

## Notes

Exercised two ways: all three Purfluous test files together, 99 passed (cue cards, AI wiring through bots.py, EQ card matching), and the piece's own diagnostics tool, run in main-assembly: a soak of 20 simulated users for 50 turns each, 0 errors, repeat rate 1.7%. No AI model was used for either.
