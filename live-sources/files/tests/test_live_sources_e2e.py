"""Connecting real cameras and microphones to the recorder.

What this file proves, and what it cannot.

PROVED here with real FFmpeg and a real host (``run_reach.py``, throwaway data folder):
* a stream PUSHED to the host over RTMP and over SRT (the way a phone app or a camera with network output does it) is
  recorded with picture and sound, and the tone that went in is the tone in the file;
* a listener nobody connects to is reported as "never opened the input" instead of looking like a recording;
* a webcam device that does not exist is reported as a failed start, never a silent success;
* a microphone given to the recorder replaces the source's own sound in the file (the mix-in mapping works);
* the listener and audio-input routes validate their input, and bad ``audio_input`` is refused with 400.

NOT proved here, because this machine has no such hardware: a real phone, a real RTMP/SRT camera, an RTSP camera, a
USB or built-in webcam, or a microphone behind an XLR interface. For those the tests check the exact FFmpeg arguments
that would be run, and the device-list parsers are checked against sample text written from the documented output
format of ``arecord -l`` and ``pactl list short sources``, not against output captured from a real machine.
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_recording_pipeline_e2e import (  # noqa: E402  (shared helpers: real host, ffprobe, spectrum)
    TONE_HZ, _free_port, _start_host, _stop_host, dominant_hz, ffmpeg, probe)

ROOT = Path(__file__).resolve().parents[1]
PROFILE = "web_stream"      # WebM/VP9/Opus: keeps up with real time even on a slow CPU, so timing never muddies a result


@pytest.fixture(scope="module")
def host(tmp_path_factory):
    h = _start_host(tmp_path_factory, "live_sources")
    yield h
    _stop_host(h)


# --- exact FFmpeg arguments (no hardware needed) ---------------------------------------------------------------
def test_input_arguments_for_every_transport():
    from modules.capture import _build_ffmpeg_input as build
    rtmp = build("rtmp", "rtmp://0.0.0.0:1935/live/key12345")
    assert rtmp[:2] == ["-listen", "1"] and rtmp[-2:] == ["-i", "rtmp://0.0.0.0:1935/live/key12345"]
    assert "-listen" not in build("rtmp", "rtmp://192.0.2.9/live/key")     # a remote address is pulled, not listened on
    assert build("srt", "srt://0.0.0.0:9000")[-1] == "srt://0.0.0.0:9000?mode=listener"
    assert build("srt", "srt://203.0.113.5:9000")[-1] == "srt://203.0.113.5:9000"
    assert build("srt", "srt://0.0.0.0:9000?mode=caller")[-1].count("mode=") == 1
    rtsp = build("rtsp", "rtsp://cam.local/stream")
    assert rtsp[:2] == ["-rtsp_transport", "tcp"] and rtsp[-1] == "rtsp://cam.local/stream"
    assert build("webcam", "/dev/video0")[:4] == ["-f", "v4l2", "-i", "/dev/video0"]
    assert build("webcam", "avfoundation:0")[:2] == ["-f", "avfoundation"]
    assert build("webcam", "video=Integrated Camera")[:4] == ["-f", "dshow", "-i", "video=Integrated Camera"]


def test_audio_input_validation_and_arguments():
    from modules.audio_input_spec import AudioInputError, parse_audio_input
    assert parse_audio_input("dshow:Microphone (USB Audio)").ffmpeg_args() == ["-f", "dshow", "-i", "audio=Microphone (USB Audio)"]
    assert parse_audio_input("alsa:hw:1,0").ffmpeg_args() == ["-f", "alsa", "-i", "hw:1,0"]
    assert parse_audio_input("pulse:alsa_input.usb-Focusrite").ffmpeg_args() == ["-f", "pulse", "-i", "alsa_input.usb-Focusrite"]
    assert parse_audio_input("avfoundation:0").ffmpeg_args() == ["-f", "avfoundation", "-i", ":0"]
    for bad in ["", "nokind", "ffmpeg:foo", "alsa:-f lavfi", "alsa:hw:0;rm", "dshow:" + "x" * 300, "dshow:a\x00b",
                'dshow:he said "hi"', "dshow:-i evil", "pulse:../x", "avfoundation:-f lavfi"]:
        # (an avfoundation name may contain spaces, e.g. "MacBook Pro Microphone"; it is one argv item, never shell text)
        with pytest.raises(AudioInputError):
            parse_audio_input(bad)
    assert parse_audio_input("avfoundation:MacBook Pro Microphone").ffmpeg_args()[-1] == ":MacBook Pro Microphone"


def test_device_list_parsers_on_sample_text():
    """Samples follow the documented output format of the two tools; they are not captured from a real machine."""
    from modules.audio_input_spec import parse_arecord_list, parse_pactl_sources
    arecord = ("**** List of CAPTURE Hardware Devices ****\n"
               "card 1: USB [Scarlett 2i2 USB], device 0: USB Audio [USB Audio]\n  Subdevices: 1/1\n")
    found = parse_arecord_list(arecord)
    assert found == [("alsa:hw:1,0", "Scarlett 2i2 USB - USB Audio")]
    pactl = ("1\talsa_output.pci-0000_00_1f.3.analog-stereo.monitor\tmodule-alsa-card.c\ts16le 2ch 44100Hz\tRUNNING\n"
             "2\talsa_input.usb-Focusrite_Scarlett_2i2-00.analog-stereo\tmodule-alsa-card.c\ts16le 2ch 48000Hz\tIDLE\n")
    sources = parse_pactl_sources(pactl)
    assert [value for value, _ in sources] == ["pulse:alsa_input.usb-Focusrite_Scarlett_2i2-00.analog-stereo"], "monitors are not microphones"


# --- the real recorder with a microphone -----------------------------------------------------------------------
def test_microphone_replaces_the_source_sound_in_the_recording(tmp_path):
    from modules.capture import FFmpegCaptureEngine
    from modules.cameras import CameraSource, CameraTransport
    from modules.recording import ContainerFormat, EncodingProfile
    media = tmp_path / "source_440.mp4"
    ffmpeg("-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=30", "-f", "lavfi",
           "-i", f"sine=frequency={TONE_HZ}:duration=30:sample_rate=48000", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", media)
    # Stands in for a real microphone: a live-paced 880 Hz tone arriving as a second FFmpeg input.
    mic = SimpleNamespace(ffmpeg_args=lambda: ["-re", "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=48000"])
    profile = EncodingProfile(profile_id="t", name="t", container=ContainerFormat.WEBM, video_codec="vp9", audio_codec="opus",
                              video_bitrate="2Mbps", audio_bitrate="128kbps", resolution="640x360", frame_rate="30")
    cam = CameraSource(source_id="mic_file", name="file", location="virtual", description="", transport=CameraTransport.FILE, endpoint=str(media))

    async def run():
        engine = FFmpegCaptureEngine()
        out = await engine.start("mic_test", [cam], profile, tmp_path, audio_input=mic)
        await asyncio.sleep(6)
        await engine.stop("mic_test")
        return out
    out = asyncio.run(run())
    info = probe(out[0])
    assert info["video"] and info["audio"], "picture and sound must both be in the file"
    assert abs(dominant_hz(out[0]) - 880) < 15, "the microphone's tone, not the camera's, must be what was recorded"


# --- the host: routes and real pushes --------------------------------------------------------------------------
def test_listener_route_validation(host):
    c = host.client
    assert c.post("/api/live-sources/listener", json={"kind": "ftp"}).status_code == 400
    assert c.post("/api/live-sources/listener", json={"port": 80}).status_code == 400
    assert c.post("/api/live-sources/listener", json={"port": "abc"}).status_code == 400
    assert c.post("/api/live-sources/listener", json={"name": "bad/name"}).status_code == 400
    assert c.post("/api/live-sources/listener", json={"key": "short"}).status_code == 400
    port = _free_port()
    first = c.post("/api/live-sources/listener", json={"name": "Phone A", "port": port})
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["push_urls"] and all(u.endswith(f":{port}/live/{body['key']}") for u in body["push_urls"]), body
    assert len(body["key"]) >= 8
    assert c.post("/api/live-sources/listener", json={"name": "Phone B", "port": port}).status_code == 409, "two cameras on one port"
    cams = {x["source_id"] for x in c.get("/api/cameras").json().get("cameras", c.get("/api/cameras").json())} if c.get("/api/cameras").status_code == 200 else set()
    assert not cams or body["source_id"] in cams


def test_listener_route_needs_a_login(host):
    import httpx
    assert httpx.post(host.base + "/api/live-sources/listener", json={}, timeout=10).status_code in (401, 403)
    assert httpx.get(host.base + "/api/live-sources/audio-inputs", timeout=10).status_code in (401, 403)


def test_audio_inputs_route_and_bad_audio_input(host):
    listed = host.client.get("/api/live-sources/audio-inputs")
    assert listed.status_code == 200 and isinstance(listed.json()["inputs"], list)
    for item in listed.json()["inputs"]:       # whatever this machine has, each entry is usable as audio_input as-is
        assert {"input", "label", "backend"} <= set(item)
    cam = host.client.post("/api/cameras/register", json={"source_id": "bad_audio_cam", "name": "x", "transport": "virtual", "endpoint": "testsrc", "location": "virtual"})
    assert cam.status_code == 200, cam.text
    for bad in ["alsa:-f lavfi", "nokind", "dshow:" + "x" * 400]:
        r = host.client.post("/api/recording/start", json={"sources": ["bad_audio_cam"], "profile_id": PROFILE, "countdown_seconds": 0,
                                                            "session_id": "bad_audio", "audio_input": bad})
        assert r.status_code == 400, (bad, r.status_code, r.text)


def _listen_and_record(host, kind, port, session, push_cmd_for, hold=6, connect=True):
    reg = host.client.post("/api/live-sources/listener", json={"name": f"{kind} test", "kind": kind, "port": port})
    assert reg.status_code == 200, reg.text
    info = reg.json()
    start = host.client.post("/api/recording/start", json={"sources": [info["source_id"]], "profile_id": PROFILE,
                                                            "countdown_seconds": 0, "session_id": session})
    assert start.status_code == 200, start.text
    pusher = None
    if connect:
        time.sleep(1.5)                                  # let the host open its listening socket first
        pusher = subprocess.Popen(push_cmd_for(info), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(hold)
        stop = host.client.post(f"/api/recording/{session}/stop")
    finally:
        if pusher:
            pusher.terminate(); pusher.wait(10)
    assert stop.status_code == 200, stop.text
    return start.json(), stop.json()


def _av_push(url, muxer):
    return ["ffmpeg", "-v", "error", "-nostdin", "-re", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=30",
            "-f", "lavfi", "-i", f"sine=frequency={TONE_HZ}:sample_rate=48000:duration=30", "-c:v", "libx264", "-preset", "veryfast",
            "-pix_fmt", "yuv420p", "-g", "30", "-c:a", "aac", "-f", muxer, url]


def test_rtmp_push_is_recorded_with_picture_and_sound(host):
    port = _free_port()
    _, stopped = _listen_and_record(host, "rtmp", port, "rtmp_push",
                                    lambda i: _av_push(f"rtmp://127.0.0.1:{port}/live/{i['key']}", "flv"))
    assert len(stopped["artifacts"]) == 1, stopped
    path = stopped["artifacts"][0]["file_path"]
    info = probe(path)
    assert info["video"] and info["audio"], info
    assert info["seconds"] > 3, f"only {info['seconds']} s of a 6 s push was kept"
    assert abs(dominant_hz(path) - TONE_HZ) < 15


def test_srt_push_is_recorded_with_picture_and_sound(host):
    port = _free_port()
    _, stopped = _listen_and_record(host, "srt", port, "srt_push",
                                    lambda i: _av_push(f"srt://127.0.0.1:{port}?mode=caller", "mpegts"))
    assert len(stopped["artifacts"]) == 1, stopped
    path = stopped["artifacts"][0]["file_path"]
    info = probe(path)
    assert info["video"] and info["audio"], info
    assert info["seconds"] > 3
    assert abs(dominant_hz(path) - TONE_HZ) < 15


def test_a_listener_nobody_connects_to_is_reported(host):
    port = _free_port()
    started, stopped = _listen_and_record(host, "rtmp", port, "nobody_home", lambda i: [], hold=3, connect=False)
    said = " ".join(str(w) for r in stopped.get("capture_report", []) for w in r.get("warnings", [])) + " " + str(started.get("capture_errors")) \
        + " " + str(stopped.get("capture_warnings"))
    assert "never opened" in said.lower(), f"a recording of nothing must say so; got {stopped}"


def test_a_missing_webcam_is_a_reported_failure(host):
    reg = host.client.post("/api/cameras/register", json={"source_id": "ghost_cam", "name": "ghost", "transport": "webcam",
                                                            "endpoint": "/dev/video97", "location": "host"})
    assert reg.status_code == 200, reg.text
    start = host.client.post("/api/recording/start", json={"sources": ["ghost_cam"], "profile_id": PROFILE, "countdown_seconds": 0, "session_id": "ghost"})
    assert start.status_code == 200, start.text
    errors = start.json().get("capture_errors")
    host.client.post("/api/recording/ghost/stop")
    assert errors, "starting a recording from a device that does not exist must be reported"
