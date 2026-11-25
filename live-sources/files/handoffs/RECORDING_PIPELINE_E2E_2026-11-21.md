# Recording pipeline, video + audio, end to end (2026-11-21)

Branch: `claude/recording-e2e-2026-11-21` (from `claude/avatar-live-camera-2026-11-21`). `main` untouched.

## What was asked
"The recording pipeline, video and audio, tested together, back end to end, and proof it can export, import, upload, download, and change file types."

## Result in one line
It works end to end now, and the test proves it on real files. Getting there exposed three bugs (two silent), fixed additively below.

## Bugs found by running it (all reproduced before fixing)
1. **`web_stream` (WebM) profile recorded nothing, and the API said success.** `modules/capture.py` only translated `h264`; the profile's `vp9`/`opus` are not FFmpeg encoder names (`libvpx-vp9`/`libopus`), so FFmpeg exited at once. `/api/recording/.../stop` returned 200 with an empty artifact list. Same latent risk for any profile naming `vp8`, `h265`, `mp3`.
2. **A recording could be silently much shorter than the take.** The 1080p/59.94 `broadcast_mp4` profile encodes at 0.53x real time with x264's default preset on this 2-core machine: an 8 s take held 4.6 s of footage, FFmpeg's output was discarded, and nothing said so. After the change the same take holds 8.9 s.
3. **Importing any recording bundle over 1 MB failed** with `413 Request body too large (max 1MB)`. `main.BodySizeLimitMiddleware` applies a 1 MB cap to every route not on its allow-list; `/api/recording/import` (route limit: 64 GiB) and `/api/upload` (route limit: 5 MB) were not on it. Same class of bug as the production-notes one already fixed there.

## Changes (additive; each is small and testable)
| Path | Change |
|---|---|
| `modules/capture.py` | encoder alias table (`vp9`->`libvpx-vp9`, `opus`->`libopus`, `h265`/`hevc`->`libx265`, `vp8`->`libvpx`, `mp3`->`libmp3lame`); live settings (`-preset veryfast` for x264, `-deadline realtime -cpu-used 8 -row-mt 1` for VP9); FFmpeg's own output kept as `<take>.ffmpeg.log` next to each take; startup check (0.6 s) for an FFmpeg that exits at once; a per-source report on stop (exit status, encode speed, encoded seconds, warnings) |
| `modules/production_routes.py` | `/api/recording/start` adds `capture_errors` when a capture could not start; `/api/recording/{id}/stop` adds `capture_report`; the `recording_stopped` hub event carries `capture_warnings` |
| `main.py` | `BodySizeLimitMiddleware` limit for `/api/recording/import` (follows `PUBCAST_RECORDING_IMPORT_MAX_BYTES` + 1 MB) and `/api/upload` (5 MB + 256 KB) |
| `tests/test_recording_pipeline_e2e.py` (new) | 21 tests, below |

## Needs Josie's decision
- **x264 preset default changed to `veryfast` for live capture.** Not part of any profile, so it was not a design change, but it does trade some compression for keeping up in real time. Set `PUBCAST_CAPTURE_X264_PRESET=medium` (any x264 preset) to restore the old behaviour; the lag warning will then tell you when a machine can't keep up.
- **`/api/vod` (list) and `/api/vod/{session}/{name}` (download) need no login.** Verified against a live host: an anonymous client lists every recording and downloads its bytes; export and import correctly require login. This may be deliberate for the public gallery page; I did not change it.

## Proven (real host, real FFmpeg/ffprobe; `tests/test_recording_pipeline_e2e.py`, 21 passed in ~290 s)
- **Record, video + audio together**: a source with picture and a 440 Hz tone recorded through `/api/recording` as MP4 (H.264/AAC) and WebM (VP9/Opus). Each file: one video and one audio stream, decodes with no errors, length within 85% to +3 s of the 6 s hold, audio and video lengths agree within 0.6 s, real frame rate, audible (RMS > 500), and the recorded audio's dominant frequency is 440 Hz (FFT of the decoded file).
- **Failure is reported**: a capture that cannot start returns `capture_errors` naming the source and the FFmpeg error; stop reports "No media was written"; a lagging encoder produces a warning with its speed (unit-tested on FFmpeg's real progress format).
- **Virtual camera is honest**: its take is flagged `synthetic` and has no audio stream (the built-in virtual camera is FFmpeg's test pattern).
- **Download**: `/api/vod` serves bytes identical to the file on disk (SHA-256).
- **Change file type**: export of one recording to MP4, WebM, MOV (ProRes + PCM 24-bit) and WAV (PCM 16-bit, no video). Each render decodes cleanly, has the expected codecs, keeps its length (within 0.6 s) and keeps the 440 Hz tone. The original is bundled byte-for-byte; every hash in `EXPORT_MANIFEST.json` is true; no converted render is labelled a master.
- **Import**: the bundle goes into a **second, separate host**; the media lands in that host's own storage, byte-identical, playable, downloadable there, and exportable again to another type. Importing the same recording twice is refused and changes nothing. A bundle with one flipped byte is refused and leaves no directory behind. A non-zip and a bare `.mp4` are refused.
- **Upload**: a browser take (VP9/Opus, several chunks) uploaded through `/api/studio25d/recordings` (out-of-order chunk 409, corrupted chunk 422), completed, downloaded back byte-identical (SHA matches the host's), bundled, and converted to MP4 with audio intact. A corrupt upload is refused at completion. `/api/upload` accepts a real audio+video MP4, serves it back byte-identical, and refuses a fake MP4, a wrong video type and an oversize file.

## Regression runs (this session)
Pass: `test_camera_capture_cleanup`, `test_recording_integrity_audit`, `test_recording_portable_bundle` (15), `test_segmented_capture_and_preservation` (17), `test_production_notes_host_limits`, `test_studio25d_recording_ingest` (19), `test_studio25d_recording_guard`, `test_avatar_imports` (37), `test_blackbox_capture_disconnect`, `test_communications_capture`, `test_camera_fixes_2026_11_21` (61), `test_recording_integration_browser` (3), three node capture tests.
**Fail, identically on a clean checkout of the starting commit (so not caused by this work):** `test_admitted_guest_recording_browser` (1 failed: the in-browser STOP RECORDING button never becomes enabled) and `test_clacker_recording_browser` (1 failed, 1 passed: a wait times out). I did not diagnose them; they depend on in-browser recording in this sandbox's Chromium.

## NOT done / not proven
- **Capture of a live camera, microphone, RTMP/SRT/NDI or the Rust renderer was not tested.** Sound was proven for a file source carrying audio and for browser uploads; real devices were not available. The virtual camera and `voxel_3d` path carry no audio by construction.
- `broadcast_mp4` keeps up at 1.03x here with `veryfast`, only just. `prores_hq` (3840x2160/60) will not run live on modest hardware; the new warning will say so, but it was not run.
- A recording stopped through the **studio action** path (`recording.stop` action in `production_routes.py`) does not carry `capture_report`; only the direct stop route does.
- Time-synchronisation between separate camera files, EDL/FCPXML export (`recording_pipeline_routes.py`), and pause/resume were not exercised.
- Export of very large bundles, and the 64 GiB import ceiling, were not exercised.
- The two pre-existing browser failures above are unexplained.
