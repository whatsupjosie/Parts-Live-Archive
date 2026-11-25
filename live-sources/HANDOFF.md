# Installed: Live sources, recording pipeline and live avatar camera (Nov 21-22) (2026-11-25)

**Status: installed in main-assembly and proven. Copied to Parts-Live-Archive as `live-sources/`.**

## What it is

Three studio pieces that went in together: live inputs (RTMP, SRT, RTSP and webcam, microphone input, and a browser camera page); the recording pipeline proven end to end for picture and sound, with silent capture failures now reported and the import size cap fixed; and a camera view that shows a 3D avatar moving live in front of a registered camera.

## Where it came from

pre-dec branch `claude/live-sources-2026-11-22` (f1d634e, which holds `claude/recording-e2e-2026-11-21` eddd8f5 and `claude/avatar-live-camera-2026-11-21` d11a1ff), installed through the studio line's merge d85784b0 (Nov 22). Nothing in it was changed on the way in. The original stays in its storage repo.

## What it changed in main-assembly

Installed on top of main-assembly e98e3e28: 20 files changed, 2473 insertions(+), 15 deletions(-). Every file it added or changed is in Parts-Live-Archive under `live-sources/files/`, and the exact change is `live-sources/INSTALL.patch`.

## How it was proven (Nov 25, in main-assembly after installing)

- Its own tests (the test files this piece added or changed: 4): 32 passed, 2 skipped, 2 warnings in 188.16s (0:03:08). Python 3.13 with fastapi 0.115.14, starlette 0.41.3, httpx 0.28.1. scikit-learn is not installed (not okayed), so a stand-in that fails loudly if anything actually calls it let the real host start. Browser tests that need playwright are skipped; async tests run without pytest-asyncio.
- Compile and syntax: py files 11  js 4  json 0; all clean.
- The app boots with it: main.py builds the app with 204 routes. Packages not installed here and not okayed to install (scikit-learn, numpy, bcrypt, psutil, websockets and some Windows-only ones) are empty stand-ins for this check only, so it proves the wiring, not those features.

## Notes

The end-to-end tests start the real host and record real streams with ffmpeg. They took about three minutes. The first run without the scikit-learn stand-in failed only because the host could not start (24 errors, 1 failure, all the same missing package), not because of this piece.
