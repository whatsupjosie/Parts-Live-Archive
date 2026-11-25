# Installed: 2i unified (Nov 20) (2026-11-25)

**Status: installed in main-assembly and proven. Copied to Parts-Live-Archive as `2i-unified/`.**

## What it is

The Nov 20 2i unification: the single-page 2i with its tools drawer, the offline interactive typewriter restored, native undo and newest recovery kept, disk Save and Primp recovery status kept current, the typewriter bridge to the real editor, negation kept inside its clause in Alex's keyword readings, and the acceptance evidence that went with it.

## Where it came from

pre-dec branch `integration/2i-unified-2026-11-20` (c514284, 15 commits), installed through the studio line's merge e64f15bc (Nov 22). Nothing in it was changed on the way in. The original stays in its storage repo.

## What it changed in main-assembly

Installed on top of main-assembly 2d0edaa1: 34 files changed, 550 insertions(+), 64 deletions(-). Every file it added or changed is in Parts-Live-Archive under `2i-unified/files/`, and the exact change is `2i-unified/INSTALL.patch`.

## How it was proven (Nov 25, in main-assembly after installing)

- Its own tests (the test files this piece added or changed: 9): 1 failed, 42 passed, 6 skipped, 2 warnings, 1 error in 27.35s. Python 3.13 with fastapi 0.115.14, starlette 0.41.3, httpx 0.28.1. scikit-learn is not installed (not okayed), so a stand-in that fails loudly if anything actually calls it let the real host start. Browser tests that need playwright are skipped; async tests run without pytest-asyncio.
- Compile and syntax: py files 13  js 14  json 0; all clean.
- The app boots with it: main.py builds the app with 204 routes. Packages not installed here and not okayed to install (scikit-learn, numpy, bcrypt, psutil, websockets and some Windows-only ones) are empty stand-ins for this check only, so it proves the wiring, not those features.

## Notes

Of the Python tests, the one failure is `test_hostile_turns_do_not_raise_the_battery_during_escalation` in tests/test_alex_keyword_matching.py. It fails the same way on main before this piece went in, so it was already failing and this piece did not cause it. One test file (tests/primp_chromium/test_mirror.py) needs playwright and could not load. The 2i Node tests this piece changed were run with node: eight of nine passed (claude functionality merge, offline save, professional UI, typewriter real adapter, typewriter relay, wiring contract, Foresight file close, wiring smoke). The ninth, tests/test_reach_editor.cjs, needs the jsdom package, which is not installed; it cannot run on main either.
