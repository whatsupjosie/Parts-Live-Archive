# Installed: Liar's Dice at the Vortex Bar booth (2026-11-25)

**Status: installed in main-assembly and proven. Copied to Parts-Live-Archive as `liars-dice/`.**

## What it is

Liar's Dice you can play in `/walk`: walk into the Vortex Bar, click the booth table, choose "Play Liar's Dice". The host holds the dice and runs the opponents (one to three), one saved game per signed-in person, kept across restarts. The full build notes are in `handoffs/LIARS_DICE_2026-11-24.md`.

## Where it came from

pre-dec branch `claude/liars-dice-bar-2026-11-24`, commit 736273e (Nov 24, 8:15 PM PT). That branch was built on pre-dec main 538a40f, which is the same commit December main started from, so it went in with no conflicts and nothing was changed in it. The branch is still in pre-dec.

## What it changed in main-assembly

New: `modules/liars_dice.py`, `modules/liars_dice_api.py`, `static/world_walk_liarsdice.js`, three test files, `handoffs/LIARS_DICE_2026-11-24.md`.
Edited: `main.py` (three lines that mount the Liar's Dice routes, after the studio progress routes), `static/world_walk.html` (loads the table, blocks walking while it is open, adds the "Play Liar's Dice" button), `data/world/placements.json` and `static/world/world.json` (the booth table click spot), `tools/world_src/gen_placements.py` (the same spot in the generator), `STATUS.md`.

## How it was proven (Nov 25, in main-assembly after installing)

- Its own tests: `tests/test_liars_dice_engine.py` and `tests/test_liars_dice_api.py`, 104 passed (Python 3.13, fastapi 0.115.14, starlette 0.41.3, httpx 0.28.1, the versions requirements.txt asks for).
- The app boots with it: main.py imports and builds the app. Routes went from 199 before installing to 204 after; the five new ones are `/api/minigames/liarsdice` plus `/start`, `/bid`, `/call`, `/next`. Packages that are not installed here and not okayed to install (scikit-learn, numpy, bcrypt, psutil, websockets and a few Windows-only ones) were replaced by empty stand-ins for the boot only, so this check proves the wiring, not those features.
- Every changed Python file compiles, the new script passes `node --check`, and both changed JSON files load.
- Not run here: `tests/test_liars_dice_browser.py` needs a real browser (playwright). Its earlier pass is recorded in the build notes, not repeated.

## Still open (from the build notes, for her)

The table spot is a proposal ("proposed" status). Opponents have no avatars or voices yet. The generator `tools/world_src/gen_placements.py` is behind the committed placements file; do not regenerate from it until it is brought up to date.
