# Integrated 2i and PubCast verification

Ran on local Linux against the existing draft PR, starting at `a773d80`. Node 24.22.3, Python 3.12.14 and pytest 9.2.0. The main browser environment used Playwright 1.56 and Chromium Headless Shell 141.0.7390.37. Chrome for Testing Headless Shell 158.0.8127.36 provided the alternative browser. The system Chromium 156 binary failed to create its process-singleton socket (EPERM). Installed Python runtime and dev requirements and Node lockfile dependencies. Used disposable worktrees, runtime directories, manuscripts and accounts. All runs used local compute.

## Run ledger

| Run | Passed | Failed | Errors | Skipped | Expected failures |
| --- | ---: | ---: | ---: | ---: | ---: |
| Focused Node, baseline | 148 | 14 | 0 | 0 | 0 |
| Focused Node, repaired | 163 | 0 | 0 | 0 | 0 |
| Requested Python, baseline | 140 | 3 | 0 | 0 | 0 |
| Expanded focused Python, including 5 key-map checks and new tablet test | 148 | 1 | 0 | 0 | 0 |
| Requested Python, after repairs, 368.86 s | 144 | 0 | 0 | 0 | 0 |
| Repository Node, baseline | 267 | 17 | 0 | 0 | 0 |
| Repository Node, repaired | 285 | 0 | 0 | 0 | 0 |
| Additional real bridge and Primp Node checks | 41 | 0 | 0 | 0 | 0 |
| Full configured Python on starting tree, 1718.37 s | 2817 | 5 | 14 | 22 | 1 |
| Additional studio acceptance, 60-second setting, baseline | 5 | 2 | 0 | 0 | 0 |
| Additional studio acceptance, retest including new tone regression | 8 | 0 | 0 | 0 | 0 |

Counts overlap. Baseline Node counted a test file that threw during loading as a single failure. Final Python results following the full baseline are targeted retests. An additional 16 unittest subtests appear in the broad JUnit file.

```sh
node --test tests/test_2i_*_node.js
python -m pytest -v -ra tests/test_2i_*.py tests/test_saves_survive_restart.py tests/test_writers_room_real_http.py tests/test_reach_writers_room.py
node --test tests/*_node.js tests/*.cjs
python -m pytest -ra --continue-on-collection-errors
PUBCAST_ENDURANCE_SECONDS=60 python -m pytest -q tools/acceptance
```

## Writer's Room evidence

Chromium navigated the actual host, completed sign-in and edited the manuscript. The focused runs covered Standard, Notebook, interactive Typewriter, keyboard and touch, Write and Collab, locks, undo and redo, replace, file import and export, Save Now, autosave, conflicts, host failures, recovery and fresh process restarts. The 32 format cases included office documents, PDF, text, images and media. Browser download and OPFS tests used actual browser storage. Picker tests also used stand-in file handles.

At 820 × 1180, a signed-in user selected Collab and Typewriter in the real menus and touched the painted X. After Save, an independent authenticated read returned the unchanged approved sentence and a draft containing only x. Offline, the tablet touched A on the actual typewriter, rendered its keys and paper, reloaded and recovered the text with networking blocked. Other exercised sizes included 390 × 844 and 1280 × 800. The standard desktop axe scan recorded 19 passing rules, zero automatic violations and two rules requiring manual review (prohibited ARIA attributes and color contrast). Physical devices and screen-reader review remain open. The editor engine language check passed with 135,155 offline pronunciations. The separate live Reach no-key test passed with a disposable configuration explicitly disabling Pete's normal offline fallback.

[Desktop writer](writer.png) · [Hosted tablet Collab](host-collab.png) · [Offline tablet Typewriter](offline-tablet.png)

## Published repairs

| Commit | Cause and correction |
| --- | --- |
| `a6543a2` | Restored native undo in served editor fields. Fixed recovery shelf ordering where prepending a newest-first list promoted older, sometimes blank, content. Updated stale Node fixtures, including the structured-clone boundary. |
| `bb2417e` | The disk Typewriter referenced a server-only route. Restored its local artwork, key map and existing renderer with a frame-specific message channel. Prevented a file-based PubPartner call from attempting a host-only URL. Added offline touch/reload regression. |
| `b91c3a0` | A queued autosave overwrote the file picker's cancellation message. Explicit save now consumes that timer. Primp displayed an earlier saved status while newer work was pending. It now confirms the current revision only. Also isolated the Alex route's message and memory test store and corrected a stale cast-name fixture. |
| `e5bbb1e` | Alex's negation window crossed an independent clause. Scoped negation to the clause and retained handling for comma-separated lists. |
| `0c8760c` | Added optional studio browser executable selection for repeatable build comparisons. |
| `18eb8c1` | Fixed two acceptance fixtures. The virtual guest had received HTTP 409 when joining before the host audio socket was ready. The endurance assertion could cancel a sustained tone when phase changed within a second. It now checks the original amplitude threshold in every complete 50 ms window and rejects a short sample, a missing frequency or a dropout. |

## Disposition of every broader non-pass

| Baseline result | Reason | Retest |
| --- | --- | --- |
| 3 media failures | M4A, MP4 and MOV playback timed out on Chromium 141. | Identical playback checks passed on Chromium 158 for all nine media cases. Final 2i suite used 158 for Foresight fixtures and pinned 141 elsewhere. |
| 1 Alex route failure | In suite order, the test read recent memory from a different store than the route used. | Ordered startup and route run passed 10/10. |
| 1 clacker failure | Decoded visual timing exceeded the 250 ms limit. | See repeat measurements below. Remains open. |
| 14 Primp fixture errors | Main environment lacked pytest-playwright's browser fixture. | Installed the separately pinned lab, Playwright 1.65, Chromium 158 and local axe. After the save-status fix all 14 passed. |
| 1 Alex expected failure | Negation crossed a clause boundary. | Removed the expected-failure marker after the repair. Matcher and route cases passed 43/43. |
| 8 avatar import, 1 Bella launch, 1 Bella progress skips | A temporary Playwright version change requested a missing headless-shell revision. | Restored main pins and isolated the Primp lab. The three files passed 20/20. |
| 4 Foresight airgapped and 1 world-chat widget skips | System Chromium closed during launch in the socket-restricted environment. | All four Foresight cases passed with the browser override. The world widget passed with Chromium 158 on PATH. |
| 2 situation-reading skips | ALEX_ENGINE_DIR was unset. | Both passed with the production modules/affect_context_engine.py. The situation and widget command also passed 60/60. |
| 1 Alex research module skip | ENABLE_ALEX_LITTLE_ONE was unset. | Enabled the canonical module and passed 10/10. |
| 1 relocated research module skip | The previous file deliberately skips at collection and points to the canonical location. | Its executable cases ran at the canonical location. |
| 1 speech host and 2 speech system skips | PUBCAST_SPEECH_TEST_MODELS was unset. | Installed optional CPU dependencies and both pinned, hash-verified Kokoro and Whisper asset packs. All 13 speech system tests visibly passed, including two-voice synthesis/transcription, cancellation and recovery. End-to-end host microphone dictation/read-aloud did not produce a verified final result. |

Automatic approval review interrupted the initial host-speech run over possible outbound Microsoft telemetry. An isolated retry disabled ONNX telemetry and ran with verified loopback TCP and no external IPv4 route. The execution computer became unavailable during the last host-browser case. The pending speech fixture and telemetry changes were local experiments and were not published. Full local per-case files could not be recovered after the disconnection. This report preserves the verified observations and the three screenshots already committed.

## Recorded-media measurements

FFmpeg decoded the actual isolated pulse and program pixels. Positive values are milliseconds of video lag. The limit is strictly below 250 ms for every mark.

| Browser trial, same product recording code | Flash 1 | Flash 2 | Result |
| --- | ---: | ---: | --- |
| 141 full run | +315 | +225 | Failed |
| 141 repeat | +292 | +221 | Failed |
| 158 first | +47 | +47 | Passed |
| 158 second | +115 | +321 | Failed |
| 158 third | +103 | +111 | Passed |

In the failing 158 repeat, the two paint observations came within 8 ms of their audio-clock targets, but their wall timestamps were 15.205 seconds apart. The recorded file still missed tolerance. Experimental render pacing, resolution and scheduling changes did not correct the problem consistently and were discarded. The exact source of the remaining drift is unresolved.

The additional studio acceptance run tested capture export and fresh import, relinking, 4K canvas observation, phone touch and reload, virtual clock skew and transport stalls. The shortened endurance case encoded 64.119 seconds and reopened with an identical SHA-256. The failing baseline file independently showed the 440 Hz tone above 0.082 in every 50 ms window at the disputed position even though a whole-second measurement dropped to 0.0073. The 30-minute endurance variant and physical cross-device sync remain unverified in this run.
