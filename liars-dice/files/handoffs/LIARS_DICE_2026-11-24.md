# Liar's Dice at the Vortex Bar booth — 2026-11-24

Branch: `claude/liars-dice-bar-2026-11-24`. `main` is untouched.

## What Josie asked for

In her words (the ChatGPT transcript she attached, ending with the instruction to that assistant): "Yours is dressing
the bar and Liar's Dice, please." "I want to be able to walk around, I want to be able to go into the bar, and I want to
be able to play Liar's Dice…" "This needs to be tight, solid, professional, stable code… Do it the right way."

Game rules she gave: up to four players (you plus one to three opponents); each bid must be higher (more dice, or the same
number on a higher face); a "call Liar" action reveals the dice; the loser of the call loses a die; a player with no dice
is out; the last player standing wins. Also wanted: dice-rolling visuals, a jangly dice sound, a cup; ideally a
first-person view with hands, and opponents with avatars and voices.

The cameras, control room, jukebox, TV and other minigames were named by her as a different AI's job. Nothing in them
was touched. The Sir Purphulous lore in the transcript was context only and nothing in it was used or changed.

## Decision about the bar art

The Vortex Bar picture, floor trace, occluders and doors were **not changed**. The bar was already walkable at `/walk`.
I did not re-dress it. If "dress the bar" means new art, that needs Josie's pictures.

## What was built

| Piece | Where |
| --- | --- |
| Rules engine: seats, bids, calls, wild-ones option, elimination, winner, opponents, save/load with strict checking | `modules/liars_dice.py` |
| Routes: get / start / bid / call / next / leave, one game per signed-in person, stale-click protection | `modules/liars_dice_api.py`, registered in `main.py` |
| The table: setup, seats, your dice, bidding controls, Call Liar, reveal with matching dice marked, replay of what opponents did, synthesized rattle / thud / chimes, mute, keyboard and screen-reader support, reduced motion | `static/world_walk_liarsdice.js` |
| The click spot on the booth table, and the walker's "Play Liar's Dice" button | `tools/world_src/gen_placements.py`, `data/world/placements.json`, `static/world/world.json`, `static/world_walk.html` |

How it works: the host holds every die. The browser gets only your own dice. The others' dice are sent only in the
reveal that follows a call. A game is saved on the host after every move, so it survives leaving the table, closing the
browser and restarting PubCast. Opponents use the exact chance that a bid is true given their own dice, with three
temperaments (cautious, balanced, bold) picked at random per seat.

## Decisions I made that are Josie's to confirm

1. You open round one. The loser of a call opens the next round (if they are out, the next seat in turn).
2. If you run out of dice the game ends (the opponents do not play on without you).
3. Ones are not wild by default. A "Ones are wild" checkbox at setup turns it on; when it is on, ones count for any
   face except when the bid is on ones.
4. Five dice each, at most 3 opponents, at least 1.
5. **The table spot is my placement** (`booth-table-dice`, status "proposed", at about 240,585 in the picture, on the
   nearest striped booth). I could not see the table clearly. Marked as my proposal in its note. Tell me if she meant another.
6. Opponents are named "Seat 2" to "Seat 4". No avatars, voices or banter.

## Tested (Linux, Python 3.13, headless Chromium)

- `tests/test_liars_dice_engine.py` — 79 passed. Raise rule table, wrong turn / phase, calls with hand-stacked dice
  (including wild ones), elimination and who opens next, human knocked out, win, view redaction, 400 opponent games
  across all styles with wild on and off (only legal moves, every game ends), exact binomial arithmetic checked against
  counting every outcome, opponent tendencies, record round-trip, 28 kinds of damaged saved game refused.
  Mutation check: breaking the "bid is true" test, the raise rule, and the view's hidden dice each made a test fail.
- `tests/test_liars_dice_api.py` — 25 passed: sign-in required when auth is enforced, one table per person, bad input
  refused with nothing changed, stale clicks refused with the current table, hidden dice absent from replies, whole
  games played through the routes, restart on the same data folder, damaged save recovers.
- `tests/test_liars_dice_browser.py` — 1 passed, against the real host (`run_reach.py`) and real Chromium: walk to the
  bar, click the table at its place in the picture, "Play Liar's Dice", walking keys blocked while open, sit down, only
  five dice on the page, play by clicking until a call, every hand shown with matches marked, next round hides them
  again, Esc keeps the game and does not walk you back a room, stop PubCast and start it again and the same game
  resumes ("Welcome back"), host unreachable shows a plain message and "Try again" recovers, two-click "Forget this game".
  Screenshots were looked at (bubble, setup, round, reveal, offline).
- Generated data: `data/world/placements.json` differs from the previous version only by the one new hotspot;
  `static/world/world.json` is 16 added lines and no removed lines.

Regression run (not the full suite): 10 neighbouring files (world walk host and browser, studio progress, world data,
route authority and authorization parity, production notes limits, pubworld) — 164 passed, 3 failed, 1 error. The same
3 failures (`test_main_route_authorization_parity.py`: bot speak, governance audit, governance user audit) and the same
1 order-dependent error (`test_pubworld_private.py::test_a_change_of_room_is_announced_once...`, passes alone) occur on
the untouched starting commit run the same way, so they are not from this change; I did not look into them.
The **full suite was not completed**: it ran past 30 minutes and was stopped, and `tests/primp_chromium` errors here
(not investigated). Baseline before this work: 131 tests green in the set I ran first.

## Not tested / not built

- **Sound has never been heard.** The sounds are made with Web Audio in the browser; headless Chromium cannot play
  them. The code runs without error, but whether they sound good ("jangly") is untested. The rattle will probably need tuning by ear.
- Dice are drawn with CSS pips, flat, with a short tumble. No 3D dice physics, no cup art, no first-person view with
  hands: those need art assets that do not exist here.
- No opponent avatars, voices or banter lines (her text, not mine to write).
- Phone / touch layout: sized to fit narrow screens but not tested on a phone.
- Keyboard-only play and a screen reader were designed for (focus kept, live status, dialog role) but only the
  keyboard Esc path was run; no screen reader was used.
- The jukebox, TV, cameras and other minigames: not touched.
- The "Implementation Pack v1" canvas mentioned in the transcript was not in the pasted text, so nothing was taken from it.

## A problem found and left alone

`tools/world_src/gen_placements.py` has fallen behind `data/world/placements.json`: running it as written **deletes**
Josie's 2026-11-02 "floor-painting" hotspot in the manager's office and her note on the painting view. I did not
regenerate from it. I restored the committed file and added only the new hotspot (also added to the generator so the
hotspot is not lost if it is regenerated later), then rebuilt `world.json` with `tools/build_world_json.py`.
Someone should bring the generator up to date with her 2026-11-02 data before anyone runs it again.

## Previous version

Everything changed is in git history on this branch's parent. Backups of the two files I changed by script:
the previous `data/world/placements.json` and `static/world/world.json` are the committed ones.
