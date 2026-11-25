# Installed: Camera fixes (Nov 21) (2026-11-25)

**Status: installed in main-assembly and proven. Copied to Parts-Live-Archive as `camera-fixes/`.**

## What it is

Fixes to the camera layer: camera config reloads without a restart, probes for USB and IP cameras, removing a camera, focus control in the Rust camera path, and a marker on synthetic (test pattern) cameras so they are never mistaken for real ones.

## Where it came from

pre-dec branch `claude/camera-fixes-2026-11-21` (4ad7c0d, five commits), installed through the same merge the studio line used for it (e0ca03bc, Nov 22).
 Nothing in it was changed on the way in. The original stays in its storage repo.

## What it changed in main-assembly

Installed on top of main-assembly 2547e265: 13 files changed, 973 insertions(+), 59 deletions(-). Every file it added or changed is in Parts-Live-Archive under `camera-fixes/files/`, and the exact change is `camera-fixes/INSTALL.patch`.

## How it was proven (Nov 25, in main-assembly after installing)

- Its own tests (the test files this piece added or changed: 3): 65 passed, 1 skipped, 2 warnings in 8.85s. Python 3.13 with fastapi 0.115.14, starlette 0.41.3, httpx 0.28.1. Browser tests that need playwright are skipped; async tests run without pytest-asyncio.
- Compile and syntax: py files 11  js 0  json 0; all clean.
- The app boots with it: main.py builds the app with 204 routes. Packages not installed here and not okayed to install (scikit-learn, numpy, bcrypt, psutil, websockets and some Windows-only ones) are empty stand-ins for this check only, so it proves the wiring, not those features.
