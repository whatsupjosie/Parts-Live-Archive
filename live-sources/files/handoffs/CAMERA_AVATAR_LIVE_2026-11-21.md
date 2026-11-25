# Camera view: a 3D avatar in front of a camera, moving live (2026-11-21)

Branch: `claude/avatar-live-camera-2026-11-21` (built on `claude/camera-fixes-2026-11-21`). `main` untouched.

## What was asked
"Fix the entire camera system, stopping when we can put one of our 3D avatars in front of the camera and see it move live."

## What was found (verified by running it, not by reading)
1. Nothing connected a camera's pose to a rendered picture. The registry (`modules/cameras.py`) holds poses, the browser pieces draw GLB avatars, but no page drew "what camera X sees". `CAMERA_RENDERER_CONTRACT_V1.md` already said the same about the Rust renderer. That Rust path is **not** used or proven here.
2. `assets/avatar/manny.glb` and `assets/avatar/sheila.glb` cannot be loaded by three.js. All seven bufferViews in each file omit the required `buffer` index (glTF 2.0 requires it). three.js fails with `Cannot read properties of undefined (reading 'type')`. `assets/pete_avatar_pubcast_v56.glb` and `static/world/test_avatars/Xbot.glb` are valid.
3. The existing frame endpoint (`/api/studio25d/media/frames/{id}`) only accepts registered studio 2.5D cameras (`p25d_*`, location `studio25d`, tag `studio25d`). This is Josie's design and was **not** loosened.

## What was added (all additive; nothing existing was changed except one `include_router` in `main.py`)
| Path | Purpose |
|---|---|
| `modules/camera_view_routes.py` (new) | `GET /api/camera-view/state/{camera_id}?avatar=<id>` (crew role): the camera's pose read from the canonical `CameraManager`, plus the avatar's motion as bone rotations in the format `static/avatar_glb_motion_consumer.js` already reads |
| `main.py` | mounts that router next to the other camera routers |
| `static/camera_avatar_live.html` + `.js` (new) | the live view: loads a GLB, polls the state route (50 ms), aims a perspective camera from the pose, applies motion through the existing `PubcastGLBMotionConsumer`, renders, and with `publish=1` posts JPEG frames to the existing frame endpoint |
| `static/glb_buffer_repair.js` (new) | repairs the missing `buffer` index **in memory only** when a GLB declares exactly one buffer. Valid files come back byte-identical; ambiguous files are left alone. The files on disk were not touched |
| `tests/test_camera_avatar_live.py`, `tests/test_glb_buffer_repair_node.js` (new) | see below |

Open it: `/static/camera_avatar_live.html?camera=<id>&avatar=/assets/avatar/manny.glb&publish=1` (signed in).

## Pose convention (decision needed from Josie)
`rotation = [tilt, pan, roll]` degrees; a camera at `[0,0,0]` looks along **-z**; tilt up positive; pan right positive. This is the only reading under which the registry defaults make sense (`wide_shot` stands at z=+5 and must face the stage). **`static/camera_rig.js` documents pan 0 as +z** instead. The two disagree. I did not change either. If both are meant to drive the same cameras, one of them needs to change; that is Josie's call.

## Tested (real, this session)
- `tests/test_camera_avatar_live.py`: 7 passed. The browser test starts the real host via `run_reach.py` on a throwaway data folder, registers a studio camera through the real API, opens the page in real Chromium (WebGL), and judges JPEG frames **fetched back from the host's own frame endpoint**:
  - avatar is in frame and centred; frames 0.35 s apart differ, and the changed pixels sit on the avatar; motion commands reached the bones (`applied > 20`);
  - dolly in (z 4 -> 3) makes the avatar taller; pan right moves the subject left; tilt up moves the subject down — all through `/api/switcher/ptz`, i.e. through the real registry.
- Mutation check: freezing the host clock makes the browser test fail with 0 changed pixels. The test can fail.
- `tests/test_glb_buffer_repair_node.js`: ok (both avatar files repaired, binary data identical, valid file returned untouched, ambiguous file untouched).
- A bug in my own first motion values (angles beyond the consumer's 35 degree clamp, and the 0.55 scale of `procedural_animation`) was caught by the new unit test and fixed (`kind: "motion"`, values inside +-35).

## Regression runs (this session)
- 13 non-browser camera/switcher/studio25d/pool/recording test files: all pass (`test_camera_broadcast`, `_capture_cleanup`, `_configuration_persistence`, `_configuration_restart`, `test_camera_fixes_2026_11_21` 61, `test_camera_moves` 46, `test_camera_pool`, `test_shared_studio_switchers`, `test_studio25d_media_routes`, `_recording_guard`, `_recording_ingest`, `test_studio_camera_preflight`, `test_glb_motion_retargeter`).
- 8 node tests (consumer, rig, keyboard, pool, bridge, confidence, repair): ok.
- Browser suites: `test_world_walk_glb_browser` 7 passed, `test_camera_try_browser` 1 passed, `test_camera_pool_browser` 1 passed, `test_camera_pool_review_browser` 12 passed, `test_studio25d_archive_browser` 2 passed.
- **`test_studio25d_capture_browser` did not finish**: it ran past 150 s with my change and past 540 s on a clean checkout of the starting commit, so my change did not cause it; whether it passes given enough time is **unknown**.

## NOT done / untested / honest limits
- **The motion is a demo performer on the host clock** (breathing, head turn, a waving arm), labelled `motion.source = "demo_performer"` in every response. It is not mocap and not the Rust `AvatarPerformerManager` feed.
- Browser-verified with `manny.glb` only. `sheila.glb` is covered by the repair node test, not by a browser run.
- Frames are a **confidence feed**: 640x360 JPEG at about 4 per second through the existing endpoint. This is not program-quality video, and it does **not** feed the FFmpeg capture/recording path. Virtual-camera recordings are still the test pattern (see `CAMERA_FIXES_2026-11-21.md`).
- Browser polls the host every 50 ms over HTTP; it is not a websocket push.
- Camera `focus` is carried but not rendered (no depth of field). `roll` is applied but has no test.
- Only studio 2.5D cameras (`p25d_*`) can publish frames, by existing design. Other cameras can be viewed locally without `publish=1`.
- The Rust `bin/ws_renderer` was not rebuilt or involved. Nothing here changes the contract's finding that the CameraManager-to-Rust-GPU frame path is unproven.
- Needs a WebGL-capable browser. Chromium in the test sandbox used software GL.
- Josie's avatar files are spec-invalid on disk. I left them as they are; repairing the files themselves is her decision.
