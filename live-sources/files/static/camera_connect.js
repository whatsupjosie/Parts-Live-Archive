/* Connect this phone or laptop as a studio camera and microphone.
 *
 * While connected: the studio sees a live preview feed (JPEG frames through the existing 2.5D confidence-frame publisher,
 * static/production25d_frame_publisher.js). While recording: the browser records picture AND sound locally and, on stop,
 * uploads the take through the existing chunked, hash-chained ingest (/api/studio25d/recordings) so the host verifies
 * every byte. The chain function below is the same as static/production25d_archive.js (CHAIN seed and layout).
 *
 * The live feed is a preview, not broadcast video: low frame rate JPEGs. The recorded take is the full-quality media.
 */
(function () {
  "use strict";
  const CHUNK = 256 * 1024, SEED = "pubcast-encoded-take/1";
  const $ = id => document.getElementById(id);
  const debug = { state: "starting", frames: 0, level: 0, sourceId: null, mime: null, hasAudio: false, lastTake: null, errors: [], recordedBytes: 0 };
  window.__camConnect = debug;

  const hex = b => Array.from(new Uint8Array(b), v => v.toString(16).padStart(2, "0")).join("");
  const unhex = s => Uint8Array.from(s.match(/../g) || [], v => parseInt(v, 16));
  const sha = async data => hex(await crypto.subtle.digest("SHA-256", data));
  const rand = n => hex(crypto.getRandomValues(new Uint8Array(n)));
  async function chainStep(previous, digest, size) {
    const bytes = new Uint8Array(72);
    bytes.set(unhex(previous)); bytes.set(unhex(digest), 32);
    new DataView(bytes.buffer).setUint32(68, size);
    return sha(bytes);
  }

  let stream = null, publisher = null, publishTimer = null, audioCtx = null, levelTimer = null;
  let recorder = null, pieces = [], recordStartedAt = 0, pending = null, sessionId = null;

  function say(text, bad) { const s = $("status"); s.textContent = text; s.classList.toggle("bad", !!bad); if (bad) debug.errors.push(text); }
  function setButtons() {
    const live = !!stream, rec = !!recorder;
    $("connect").disabled = live || debug.state === "unauthenticated" || debug.state === "starting";
    $("record").disabled = !live;
    $("record").textContent = rec ? "Stop and upload" : "Record take";
    $("disconnect").disabled = !live;
    $("cam").disabled = live; $("mic").disabled = live; $("name").disabled = live;
    $("retry").hidden = !pending;
  }

  async function api(path, init) {
    const r = await fetch(path, { credentials: "same-origin", ...init });
    if (!r.ok) {
      let detail = ""; try { detail = (await r.json()).detail; } catch (_) {}
      const err = new Error(`${path} → HTTP ${r.status}${detail ? ": " + (typeof detail === "string" ? detail : JSON.stringify(detail)) : ""}`);
      err.status = r.status; throw err;
    }
    return r.status === 204 ? null : r.json();
  }

  async function fillDevices() {
    const list = await navigator.mediaDevices.enumerateDevices();
    for (const [id, kind] of [["cam", "videoinput"], ["mic", "audioinput"]]) {
      const select = $(id), keep = select.value;
      select.innerHTML = "";
      const none = new Option(id === "mic" ? "Default microphone" : "Default camera", ""); select.add(none);
      list.filter(d => d.kind === kind).forEach((d, i) => select.add(new Option(d.label || `${kind} ${i + 1}`, d.deviceId)));
      if (keep) select.value = keep;
    }
  }

  function pickMime() {
    for (const m of ["video/webm;codecs=vp9,opus", "video/webm;codecs=vp8,opus", "video/webm", "video/mp4"]) {
      if (window.MediaRecorder && MediaRecorder.isTypeSupported(m)) return m;
    }
    return "";
  }

  function startMeter() {
    const track = stream.getAudioTracks()[0];
    debug.hasAudio = !!track;
    if (!track) return;
    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    const analyser = audioCtx.createAnalyser(); analyser.fftSize = 1024;
    audioCtx.createMediaStreamSource(new MediaStream([track])).connect(analyser);
    const data = new Uint8Array(analyser.fftSize);
    levelTimer = setInterval(() => {
      analyser.getByteTimeDomainData(data);
      let peak = 0; for (const v of data) peak = Math.max(peak, Math.abs(v - 128) / 128);
      debug.level = Math.max(debug.level * 0.7, peak);      // decays so a brief sound is visible to a person and to a test
      $("meter").firstElementChild.style.width = Math.min(100, Math.round(debug.level * 140)) + "%";
    }, 80);
  }

  async function connect() {
    try {
      say("Asking for camera and microphone…");
      const cam = $("cam").value, mic = $("mic").value;
      const constraints = {
        video: cam ? { deviceId: { exact: cam }, width: { ideal: 1280 }, height: { ideal: 720 } } : { facingMode: "environment", width: { ideal: 1280 }, height: { ideal: 720 } },
        // Raw audio: echo cancellation and gain control are made for phone calls and ruin a studio microphone.
        audio: { ...(mic ? { deviceId: { exact: mic } } : {}), echoCancellation: false, noiseSuppression: false, autoGainControl: false },
      };
      try { stream = await navigator.mediaDevices.getUserMedia(constraints); }
      catch (e) {
        if (e && e.name === "NotFoundError" || e && e.name === "OverconstrainedError") {
          // No microphone (or that exact device is gone): connect the picture alone and say so.
          stream = await navigator.mediaDevices.getUserMedia({ video: constraints.video, audio: false });
          say("No microphone available: connected with picture only.", true);
        } else throw e;
      }
      const video = $("preview"); video.srcObject = stream; await video.play();
      await fillDevices();
      startMeter();
      const name = ($("name").value || "Phone camera").trim().slice(0, 64);
      debug.sourceId = "p25d_conn_" + rand(6);
      await api("/api/cameras/register", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source_id: debug.sourceId, name, location: "studio25d", tags: ["studio25d"] }) });
      sessionId = "conn" + rand(10);
      publisher = PubCast25DFramePublisher.create({ session: sessionId, sourceId: () => debug.sourceId,
        onState: s => { if (s.frames !== undefined) debug.frames = s.frames; },
        onError: m => { debug.errors.push(m); } });
      publishTimer = setInterval(() => { if (video.videoWidth) publisher.publish("cam", video); }, 250);
      debug.state = "connected";
      say(`Connected as ${name}.\nThe studio can see this camera. ${debug.hasAudio ? "Microphone is on." : "No microphone."}`);
    } catch (e) {
      await teardown();
      debug.state = "error";
      const permission = e && (e.name === "NotAllowedError" || e.name === "SecurityError");
      say(permission ? "The browser blocked camera access. Allow camera and microphone for this page (it must be opened over https or localhost)." : `Could not connect: ${e.message || e}`, true);
    }
    setButtons();
  }

  function startRecording() {
    const mime = pickMime();
    if (!mime) { say("This browser cannot record video.", true); return; }
    pieces = []; debug.recordedBytes = 0; debug.mime = mime;
    recorder = new MediaRecorder(stream, { mimeType: mime, videoBitsPerSecond: 4_000_000, audioBitsPerSecond: 160_000 });
    recorder.ondataavailable = e => { if (e.data && e.data.size) { pieces.push(e.data); debug.recordedBytes += e.data.size; } };
    recorder.start(1000);
    recordStartedAt = performance.now();
    debug.state = "recording";
    say("Recording. Press Stop and upload when finished.");
  }

  function stopRecording(incomplete) {
    return new Promise(resolve => {
      const r = recorder; recorder = null;
      r.onstop = async () => {
        const blob = new Blob(pieces, { type: debug.mime || "video/webm" }); pieces = [];
        const video = $("preview");
        const durationMs = Math.max(1, Math.round(performance.now() - recordStartedAt));
        const device = (stream && stream.getVideoTracks()[0] && stream.getVideoTracks()[0].label) || "";
        resolve({ blob, durationMs, width: video.videoWidth || 1280, height: video.videoHeight || 720, incomplete: !!incomplete, device });
      };
      r.stop();
    });
  }

  async function upload(job) {
    if (!crypto.subtle) throw new Error("Saving takes needs https or localhost for media integrity checks");
    const { blob } = job;
    if (!job.spec) {
      const digests = [], chains = [await sha(new TextEncoder().encode(SEED))];
      for (let o = 0; o < blob.size; o += CHUNK) {
        const bytes = await blob.slice(o, o + CHUNK).arrayBuffer();
        const d = await sha(bytes); digests.push(d);
        chains.push(await chainStep(chains.at(-1), d, Math.min(CHUNK, blob.size - o)));
      }
      job.digests = digests; job.chains = chains;
      job.spec = { take_id: "take_" + rand(8), production_id: "conn_" + rand(6), source_id: debug.sourceId, mime: blob.type || debug.mime,
        bytes: blob.size, chain: chains.at(-1), width: job.width, height: job.height, duration_ms: job.durationMs,
        capture_incomplete: job.incomplete, manifest: { origin: "camera_connect", device: job.device.slice(0, 120), recorded_at: new Date().toISOString() } };
    }
    debug.state = "uploading";
    let ack = await api("/api/studio25d/recordings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(job.spec) });
    for (let i = ack.next_index; i < job.digests.length; i++) {
      say(`Uploading ${Math.round((i / job.digests.length) * 100)}%`);
      let tries = 0;
      for (;;) {
        try {
          ack = await api(`/api/studio25d/recordings/${ack.id}/chunks/${i}`, { method: "PUT",
            headers: { "Content-Type": "application/octet-stream", "X-Chunk-SHA256": job.digests[i] }, body: blob.slice(i * CHUNK, (i + 1) * CHUNK) });
          break;
        } catch (e) { if (++tries >= 3) throw e; await new Promise(r => setTimeout(r, 400 * tries)); }
      }
      if (ack.next_index !== i + 1) throw new Error("The host did not keep exactly the chunk that was sent");
    }
    ack = await api(`/api/studio25d/recordings/${ack.id}/complete`, { method: "POST" });
    if (ack.state !== "complete" || !/^[a-f0-9]{64}$/.test(ack.sha256 || "")) throw new Error("The host did not confirm a validated take");
    return ack;
  }

  async function finish(job) {
    pending = job; setButtons();
    try {
      const ack = await upload(job);
      pending = null;
      debug.lastTake = { id: ack.id, bytes: ack.received_bytes, sha256: ack.sha256, mime: job.spec.mime, incomplete: job.incomplete };
      debug.state = stream ? "connected" : "idle";
      say(`Saved on the studio: ${ack.received_bytes} bytes, verified.${job.incomplete ? "\nThis take was cut short by disconnecting." : ""}`);
      $("result").innerHTML = "";
      const a = document.createElement("a"); a.href = `/api/studio25d/recordings/${encodeURIComponent(ack.id)}/media`; a.textContent = "Download this take";
      $("result").append(a);
    } catch (e) {
      debug.state = "upload_failed";
      say(`Upload did not finish: ${e.message}\nYour recording is still on this device. Press Retry upload; it resumes where it stopped.`, true);
    }
    setButtons();
  }

  async function teardown() {
    clearInterval(publishTimer); clearInterval(levelTimer); publishTimer = levelTimer = null;
    if (publisher) { try { await publisher.stop(); } catch (_) {} publisher = null; }
    if (audioCtx) { try { await audioCtx.close(); } catch (_) {} audioCtx = null; }
    if (stream) { stream.getTracks().forEach(t => t.stop()); stream = null; }
    $("preview").srcObject = null;
    $("meter").firstElementChild.style.width = "0";
  }

  $("connect").onclick = connect;
  $("record").onclick = async () => {
    if (!recorder) { startRecording(); setButtons(); return; }
    const job = await stopRecording(false);
    await finish(job);
  };
  $("disconnect").onclick = async () => {
    let job = null;
    if (recorder) job = await stopRecording(true);        // keep what was recorded rather than throw it away
    await teardown();
    debug.state = "idle"; say("Disconnected."); setButtons();
    if (job) await finish(job);
  };
  $("retry").onclick = async () => { if (pending) await finish(pending); };

  (async function init() {
    try {
      const who = await api("/api/auth/verify");
      if (!who || !who.valid) throw Object.assign(new Error("not signed in"), { status: 401 });
      debug.state = "ready";
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        say("This page needs https or localhost to use the camera. Open the studio's secure address.", true); debug.state = "unsupported"; return;
      }
      say(`Signed in as ${who.username}. Press Connect.`);
      try { await fillDevices(); } catch (_) {}
    } catch (e) {
      debug.state = "unauthenticated";
      say("Sign in first.", true);
      const a = document.createElement("a"); a.href = "/static/login.html?next=" + encodeURIComponent(location.pathname);
      a.textContent = "Open sign-in"; $("result").append(a);
    }
    setButtons();
  })();
})();
