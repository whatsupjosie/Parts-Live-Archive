"""The browser camera-and-microphone connect page, in real Chromium against the real host.

Chromium runs with its fake camera (a moving test picture) and a fake microphone fed from a real 440 Hz WAV file, so
the page does what it does on a phone or laptop except that the devices are Chromium's stand-ins. What is judged is
what the HOST ended up with:

* signed out, the page says so and links to sign-in;
* connected, the host serves live JPEG frames from the page's camera, and they change between fetches;
* the microphone level the page reports rises (sound reaches the page);
* a recorded take is uploaded through the hash-chained chunk ingest, downloaded back from the host, holds picture and
  sound, is long enough, and the tone in it is 440 Hz;
* an upload that fails part way keeps the take on the device and Retry resumes it to a verified, identical result.

Not proved: a real phone or laptop camera or microphone, Safari/iOS (MP4 recording path), or a long take over a poor
mobile connection. Skips without Playwright/Chromium/FFmpeg.
"""
from __future__ import annotations

import io
import re
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_recording_pipeline_e2e import TONE_HZ, _start_host, _stop_host, dominant_hz, ffmpeg, probe, sha256  # noqa: E402


@pytest.fixture(scope="module")
def host(tmp_path_factory):
    h = _start_host(tmp_path_factory, "camera_connect")
    yield h
    _stop_host(h)


@pytest.fixture(scope="module")
def mic_wav(tmp_path_factory):
    path = tmp_path_factory.mktemp("fakemic") / "mic440.wav"
    ffmpeg("-f", "lavfi", "-i", f"sine=frequency={TONE_HZ}:duration=60:sample_rate=48000", "-ac", "1", "-c:a", "pcm_s16le", path)
    return path


def decoded_seconds(path):
    """Length by actually decoding. Chrome's MediaRecorder writes WebM with no duration in its header, so ffprobe's
    container duration is absent for a browser take; the decoded length is the honest measure."""
    done = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(path), "-f", "null", "-", "-progress", "pipe:1"],
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    stamps = re.findall(r"out_time_ms=(\d+)", done.stdout)
    return int(stamps[-1]) / 1e6


def _probe_streams(path):
    """Stream kinds only: the container duration is missing for a browser-made WebM, which ``probe`` requires."""
    import json
    out = subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name", "-of", "json", str(path)], timeout=30)
    streams = json.loads(out)["streams"]
    return {"streams": streams, "video": [x for x in streams if x["codec_type"] == "video"], "audio": [x for x in streams if x["codec_type"] == "audio"]}


def _browser(p, mic_wav):
    try:
        return p.chromium.launch(args=["--no-sandbox", "--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream",
                                       f"--use-file-for-fake-audio-capture={mic_wav}"])
    except Exception as exc:
        pytest.skip(f"no Chromium available: {exc}")


def _signed_in_page(browser, host):
    token = host.client.headers["Authorization"].split(" ", 1)[1]
    ctx = browser.new_context(viewport={"width": 420, "height": 800})
    ctx.add_init_script(f"try{{sessionStorage.setItem('pubcast_token','{token}')}}catch(e){{}}")
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(host.base + "/static/camera_connect.html")
    return page, errors


def _state(page):
    return page.evaluate("window.__camConnect")


def _wait(page, expression, timeout=30000, what=""):
    try:
        page.wait_for_function(expression, timeout=timeout)
    except Exception:
        raise AssertionError(f"timed out waiting for {what or expression}; page state: {_state(page)}")


def test_signed_out_page_asks_for_sign_in(host, mic_wav):
    with sync_api.sync_playwright() as p:
        browser = _browser(p, mic_wav)
        try:
            page = browser.new_page()
            page.goto(host.base + "/static/camera_connect.html")
            _wait(page, "window.__camConnect && window.__camConnect.state === 'unauthenticated'", what="the signed-out state")
            assert page.is_disabled("#connect")
            assert "login" in page.get_attribute("#result a", "href")
        finally:
            browser.close()


def test_connect_stream_record_upload_and_download(host, mic_wav, tmp_path):
    with sync_api.sync_playwright() as p:
        browser = _browser(p, mic_wav)
        try:
            page, errors = _signed_in_page(browser, host)
            _wait(page, "window.__camConnect.state === 'ready'", what="a signed-in page")
            page.click("#connect")
            _wait(page, "window.__camConnect.state === 'connected' && window.__camConnect.frames >= 4", 40000, "connection and preview frames")
            state = _state(page)
            assert state["sourceId"].startswith("p25d_conn_") and state["hasAudio"]

            # the microphone is live on the page
            _wait(page, "window.__camConnect.level > 0.05", 10000, "microphone level to rise")

            # the HOST has live frames from this camera, and they change
            grabbed = []
            for _ in range(3):
                r = host.client.get(f"/api/studio25d/media/frames/{state['sourceId']}")
                assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and r.content[:3] == b"\xff\xd8\xff"
                grabbed.append((int(r.headers["x-studio-sequence"]), r.content))
                time.sleep(1.2)
            assert grabbed[-1][0] > grabbed[0][0], "the sequence number must advance while connected"
            try:
                from PIL import Image, ImageChops
                a, b = (Image.open(io.BytesIO(g[1])).convert("L") for g in (grabbed[0], grabbed[-1]))
                assert ImageChops.difference(a, b).getbbox() is not None, "successive frames are identical: the feed is frozen"
            except ImportError:
                assert grabbed[0][1] != grabbed[-1][1]

            # record a take, stop, and wait for the verified upload
            page.click("#record")
            _wait(page, "window.__camConnect.state === 'recording'", 5000, "recording to start")
            time.sleep(5)
            page.click("#record")
            _wait(page, "window.__camConnect.lastTake !== null", 60000, "the take to upload and be confirmed")
            take = _state(page)["lastTake"]
            assert take["bytes"] > 100_000 and not take["incomplete"]

            # download it back from the host and judge the FILE
            media = host.client.get(f"/api/studio25d/recordings/{take['id']}/media")
            assert media.status_code == 200
            assert sha256(media.content) == take["sha256"] == media.headers["x-source-sha256"]
            out = tmp_path / "browser_take.webm"
            out.write_bytes(media.content)
            info = _probe_streams(out)
            assert info["video"] and info["audio"], f"picture and sound must both be present: {info['streams']}"
            assert decoded_seconds(out) > 3.5, "a 5 s take decoded to less than 3.5 s"
            assert abs(dominant_hz(out) - TONE_HZ) < 20, "the sound in the take is not the microphone's tone"

            # and it converts: the host's export turns the browser take into a normal MP4 with picture, sound and length
            conv = host.client.post(f"/api/recording/{take['id']}/export", json={"format": "mp4"})
            assert conv.status_code == 200, conv.text[:300]
            with zipfile.ZipFile(io.BytesIO(conv.content)) as z:
                z.extractall(tmp_path / "conv")
            mp4 = next((tmp_path / "conv" / "renders").glob("*.mp4"))
            mp4_info = probe(mp4)
            assert [v["codec_name"] for v in mp4_info["video"]] == ["h264"] and [a["codec_name"] for a in mp4_info["audio"]] == ["aac"]
            assert mp4_info["seconds"] > 3.5 and abs(dominant_hz(mp4) - TONE_HZ) < 20

            page.click("#disconnect")
            _wait(page, "window.__camConnect.state === 'idle'", 10000, "disconnect")
            assert page.is_disabled("#record")
            assert not errors, f"script errors on the page: {errors}"
        finally:
            browser.close()


def test_failed_upload_keeps_the_take_and_retry_resumes(host, mic_wav, tmp_path):
    with sync_api.sync_playwright() as p:
        browser = _browser(p, mic_wav)
        try:
            page, _ = _signed_in_page(browser, host)
            _wait(page, "window.__camConnect.state === 'ready'", what="a signed-in page")
            page.click("#connect")
            _wait(page, "window.__camConnect.state === 'connected'", 40000, "connection")

            blocking = {"on": True}

            def fail_chunk_two(route):
                if blocking["on"]:
                    route.abort("connectionreset")
                else:
                    route.continue_()
            page.route("**/chunks/2", fail_chunk_two)

            page.click("#record")
            time.sleep(6)                     # long enough for several 256 KiB chunks
            page.click("#record")
            _wait(page, "window.__camConnect.state === 'upload_failed'", 60000, "the upload to fail")
            assert page.is_visible("#retry"), "a failed upload must offer Retry and keep the recording"
            assert _state(page)["lastTake"] is None

            blocking["on"] = False
            page.click("#retry")
            _wait(page, "window.__camConnect.lastTake !== null", 60000, "the resumed upload to finish")
            take = _state(page)["lastTake"]
            media = host.client.get(f"/api/studio25d/recordings/{take['id']}/media")
            assert media.status_code == 200 and sha256(media.content) == take["sha256"]
            out = tmp_path / "resumed.webm"
            out.write_bytes(media.content)
            info = _probe_streams(out)
            assert info["video"] and info["audio"] and decoded_seconds(out) > 3.5
            assert abs(dominant_hz(out) - TONE_HZ) < 20
        finally:
            browser.close()
