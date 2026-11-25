# Live sources: connecting phones, laptops, pro cameras and microphones (2026-11-22)

Branch: claude/live-sources-2026-11-22 (made from claude/recording-e2e-2026-11-21 @ eddd8f5). Not main.
Reason: Josie asked for the pieces so the studio can take a live feed from a phone, a laptop camera, or a professional
camera or XLR microphone "if we want to, or need to, or can".

## What was added (exact paths)

- modules/capture.py: new input transports for the main recorder. RTMP and SRT in LISTENER mode (the device pushes to the
  host), RTSP (host pulls from an IP camera), WEBCAM (a camera attached to the host: /dev/videoN, avfoundation:N, or a
  DirectShow name). New optional microphone input recorded with the first source. New report warning when FFmpeg never
  opened its input. Live inputs use a 2 s probe instead of FFmpeg's 5 s so less of a take is spent waiting.
- modules/cameras.py: CameraTransport gains RTSP and WEBCAM.
- modules/audio_input_spec.py (new): validates and builds the FFmpeg arguments for a microphone (dshow, alsa, pulse,
  avfoundation) and lists the microphones this host has.
- modules/live_source_routes.py (new), mounted in main.py: POST /api/live-sources/listener (mod role) registers a camera
  that a device pushes to and returns the address to type into the device; GET /api/live-sources/audio-inputs (crew).
- modules/production_routes.py: POST /api/recording/start accepts optional "audio_input" (400 if invalid).
- static/camera_connect.html + static/camera_connect.js (new): open on a phone or laptop, sign in, press Connect.
  Live JPEG preview goes to the studio; Record take records picture and sound in the browser and uploads through the
  existing hash-chained chunk ingest. Reuses static/production25d_frame_publisher.js unchanged; the chain function is the
  same algorithm as static/production25d_archive.js (that module is tied to IndexedDB, so it was not reusable as is).
- tests/test_live_sources_e2e.py (11 tests), tests/test_camera_connect_browser.py (3 tests).
- tests/test_recording_pipeline_e2e.py: the lag-report test now uses a realistic FFmpeg log and also checks the new
  never-opened warning. No behavior of that test was loosened.

## TESTED (real FFmpeg, real host, real Chromium)

- RTMP push (a second FFmpeg sends picture + 440 Hz tone) recorded by the host: video and audio present, tone is 440 Hz.
- SRT push, same result.
- A listener nobody connects to produces a "never opened the input" warning, not a quiet empty take.
- A webcam device that does not exist is reported as a failed start.
- A microphone given to the recorder replaces the source's own sound (880 Hz mic over a 440 Hz source; file has 880 Hz).
- Listener route: bad kind, port, name, key all refused; two cameras on one port refused (409); needs a login.
- Bad audio_input refused with 400. avfoundation names may contain spaces (one argument, never shell text).
- Browser page in Chromium with its fake camera and a fake microphone fed from a real 440 Hz WAV: signed-out state links to
  sign-in; connect gives live JPEG frames that the host serves and that change between fetches; microphone level rises;
  a 5 s take uploads, downloads back byte-identical (SHA-256 matches), has picture and sound, decodes to > 3.5 s, tone is
  440 Hz, and exports to MP4 (H.264/AAC, length and tone intact).
- An upload that fails part way keeps the recording on the device; Retry resumes and the result is verified identical.
- Regression: 228 tests (camera, capture, ingest, recording, switcher suites) pass; all Node tests pass.

## NOT TESTED (no such hardware or setup here)

- A real phone, a real RTMP/SRT-capable camera, OBS on another machine, an RTSP camera.
- A USB or built-in webcam through the WEBCAM transport (only argument building and the missing-device failure are tested).
- Any microphone: USB, or XLR through an audio interface. Windows DirectShow names in particular were never run.
- The device-list parsers (arecord -l, pactl list short sources) are checked against sample text written from the
  documented format, not output captured from a real machine. Windows listing reuses an existing function, untested here.
- Safari/iOS (MP4 recording path), long takes, poor mobile connections.

## Known limits and findings (Josie decides what to do about each)

1. Phones need https for the browser page. Browsers only allow camera and microphone on https or localhost.
   run_reach.py serves plain http, so camera_connect.html works on the laptop itself (localhost) but NOT from a phone
   opening http://<laptop address>:<port>. The page says so when blocked. RTMP/SRT streaming apps do not have this
   limit. Options (not built): TLS in the launcher, or a tunnel.
2. The listener is open only while a recording of that camera is running (FFmpeg is the server only then). The device must
   start streaming AFTER the recording starts, and a second recording needs it to reconnect. A persistent ingest server
   would remove this; not built.
3. SRT here has no passphrase. RTMP's stream key is the only secret. Use on a trusted network.
4. The microphone goes to the first source only, and only through POST /api/recording/start (not the studio-action
   recording path).
5. Browser-made WebM has no duration in its header (Chrome's MediaRecorder). It decodes, plays and exports correctly.
6. No route exists to remove a registered camera, so live_* and p25d_conn_* cameras stay registered; whether they survive a
   restart was not checked.
7. On this 2-core test machine the broadcast_mp4 profile encodes at about 0.9x real time and is reported as lagging
   (correctly). web_stream keeps up (1.2x). test_recording_pipeline_e2e's broadcast_mp4 test fails on this machine today;
   the previous branch fails it identically, so it is machine speed, not this change.
8. Registering a camera needs the moderator role.

## Using it (PowerShell 5)

    $h = @{ Authorization = "Bearer $token" }
    Invoke-RestMethod -Method Post -Uri http://localhost:PORT/api/live-sources/listener -Headers $h -ContentType application/json -Body '{"kind":"rtmp","name":"Phone"}'
    Invoke-RestMethod -Uri http://localhost:PORT/api/live-sources/audio-inputs -Headers $h

Type the returned push address into the phone's streaming app AFTER starting the recording of that camera.
