# Camera fixes, 2026-11-21, Claude

Branch `claude/camera-fixes-2026-11-21`, built on `integration/2i-camera-repairs-2026-11-20` (`8ddfd31`).
`main` was not touched. Nothing was deleted. Every change adds to or tightens existing behaviour.

## Reason

`integration/2i-camera-repairs-2026-11-20` added `tests/test_camera_configuration_restart.py`. Its two
persistence tests failed on that branch's own tip (2 failed, 96 passed across the camera test files). The cause is
older than the branch: `save_configuration` writes each camera as its metadata plus an `id` key, and
`CameraMetadata.from_dict` was `cls(**data)`, which raised `TypeError` on `id`. The manager caught it, logged it,
and silently rebuilt the default cameras, so every restart discarded saved names, roles, program/preview picks
and recording assignments. The same code is on `main`.

## Changes, with paths

| # | Change | Paths |
|---|--------|-------|
| 1 | Saved camera configuration now reloads. Unknown keys ignored, enums and tuples restored, bad entries skipped one by one instead of discarding everything. | `modules/camera_config_io.py` (new), `modules/cameras_advanced.py`, `modules/evo/camera_manager.py` |
| 2 | If a configuration cannot be fully loaded, the file is copied to `cameras.json.unreadable-<timestamp>` before defaults are used, so the next save cannot overwrite it. Never overwrites an earlier copy. | same |
| 3 | A saved `connecting`, `online` or `recording` status loads as `offline`. Nothing is connected after a restart, and `cut_to_camera` only accepts `online` cameras. `error`, `maintenance`, `offline` are kept. | same |
| 4 | Program, preview and recording references to cameras that were not loaded are dropped on load. | same |
| 5 | USB/OBS probe chose `dshow` on Linux (`"linux" in "/dev/video0"` is false). Now uses the rule `_capture_input_args` already used: `/dev/...` is `video4linux2`. | same |
| 6 | IP camera credentials are percent-encoded in the RTSP URL (an `@`, `:` or `/` in a password broke the host). Plain credentials produce the same URL as before. | same |
| 7 | `remove_camera` raised `RuntimeError` before removing anything when called without a running event loop. It now removes the camera and stops an owned capture process directly. With a loop it behaves as before. | same |
| 8 | Removing a camera from the simple registry clears it as program/preview, so `take()` no longer swaps in a missing camera and emits events for it. `update_pose` rejects NaN and infinite position and rotation. | `modules/cameras.py` |
| 9 | Rust camera control: focus (metres) is now sent on register, set_mode and update_pose. The Rust side accepts it as an optional field (range 0.3 to 200, last good value kept when omitted); envelopes without it still parse. In-flight mirror tasks are referenced and cancelled on stop, and a mirror failure is recorded in `last_error` instead of vanishing as an unretrieved task error. | `modules/rust_camera_runtime.py`, `rust_crate/src/camera_control.rs` |
| 10 | Recordings made from a `virtual` camera are an FFmpeg test pattern. The engine now logs a warning, remembers which outputs they are, and the saved artifact carries `"synthetic": true` (only written when true, so existing records and exports are byte-for-byte the same shape). | `modules/capture.py`, `modules/recording.py`, `modules/production_routes.py` |

## Tests

New: `tests/test_camera_fixes_2026_11_21.py`, 61 tests. Manager tests run against both camera managers. They check
disk state, the exact ffprobe commands that would run, and the payloads that would be sent. Two use real FFmpeg,
one through the real production router (start, record two seconds, stop, reload from disk). Run against the old
code, 43 of the 61 fail. The other 18 are guards that pin behaviour that must not change (plain credentials give
the same URL, Windows device names still use `dshow`, removing an unrelated camera, the exact record shape for
real video, valid poses still accepted, the status never claiming frame proof), plus the live-status-after-restart
tests, which pass on the old code only because the old loader never worked. Those were checked separately: with
the status reset switched off, all three fail.

Rust: `cargo test --locked camera_control` in `rust_crate`: 3 passed (1 existing, 2 new).

Regression: all 110 non-browser test files that import or mention the changed modules:
1308 passed, 1 failed, 4 skipped, 16 subtests passed. The one failure,
`test_alex_routes_bridge::test_alex_routes_message_and_recent_memory`, is the order-dependent failure the
2026-11-20 merge report already lists as inherited. It passes alone (9 passed) and fails identically in the same
group on the unmodified branch tip. Before this work the camera files showed 96 passed, 2 failed; after, 98 passed,
0 failed. No GitHub Actions or CI were used; every number is from a local sandbox run.

## Not tested, not changed, or left for a decision

- `bin/ws_renderer` was **not rebuilt**. The shipped binary ignores the new `focus` field (unknown fields are
  ignored), so nothing breaks, but focus does not reach the renderer until the binary is rebuilt from
  `rust_crate/src/ws_renderer.rs` and checked against a live renderer. Rust ACKs still prove control state only:
  `frame_path_proven` stays `false`.
- No physical camera, RTSP stream or voxel renderer was used. The USB and IP fixes are tested by capturing the
  command that would run, not by opening a device.
- Browser and Chromium tests were not run. Node tests were not touched.
- The full Python suite was not run; the 110-file subset above was.
- Not changed, worth a look: `password` is saved in plain text in `cameras.json` (it is part of the metadata that
  `save_configuration` writes). The IP URL builder appends `:port` after the connection string, which is wrong if
  the connection string already has a path or a port. After `cut_to_camera`, `preview_source` can still name the
  camera that just became program.
- Left alone on purpose: the `codex/main-debug-20261119` recording-architecture decision, and the two reference
  images on `codex/studio-integration` that are phone screenshots.
