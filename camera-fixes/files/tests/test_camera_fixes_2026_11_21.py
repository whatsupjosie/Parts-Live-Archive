"""Regression tests for the 2026-11-21 camera fixes.

Each test checks observable state (files on disk, commands that would be run,
payloads that would be sent) rather than that a function merely returned.
Every manager test runs against both camera managers, which carry parallel code.
"""
import asyncio
import importlib
import json
import math

import pytest

from modules import camera_config_io
from modules.cameras import CameraManager, CameraSource as SimpleSource, CameraTransport as SimpleTransport
from modules.rust_camera_runtime import PROTOCOL, RustCameraRuntime

MODULES = ["modules.cameras_advanced", "modules.evo.camera_manager"]


@pytest.fixture(params=MODULES)
def cam(request):
    return importlib.import_module(request.param)


def _unreadable_copies(path):
    return sorted(path.parent.glob(path.name + ".unreadable-*"))


# --- configuration round trip -------------------------------------------------

def test_full_round_trip_restores_types_and_values(cam, tmp_path):
    path = tmp_path / "cameras.json"
    first = cam.AdvancedCameraManager(path)
    first.sources["cam2"].metadata.name = "Close-up"
    first.sources["cam2"].metadata.position = (1.0, 2.0, 3.0)
    first.sources["cam2"].metadata.effects_enabled = ["blur"]
    first.sources["cam2"].metadata.role = cam.CameraRole.BACKUP
    first.program_source = "cam2"
    first.save_configuration()

    again = cam.AdvancedCameraManager(path)
    meta = again.sources["cam2"].metadata
    assert meta.name == "Close-up"
    assert meta.position == (1.0, 2.0, 3.0) and isinstance(meta.position, tuple)
    assert meta.effects_enabled == ["blur"]
    assert meta.role is cam.CameraRole.BACKUP
    assert isinstance(meta.transport, cam.CameraTransport)
    assert again.program_source == "cam2"
    assert set(again.sources) == set(first.sources)
    assert not _unreadable_copies(path), "a clean load must not create backups"


@pytest.mark.parametrize("saved_status", ["connecting", "online", "recording"])
def test_restart_does_not_resurrect_a_live_status(cam, tmp_path, saved_status):
    path = tmp_path / "cameras.json"
    first = cam.AdvancedCameraManager(path)
    first.sources["cam1"].metadata.status = cam.CameraStatus(saved_status)
    first.save_configuration()

    again = cam.AdvancedCameraManager(path)
    assert again.sources["cam1"].metadata.status is cam.CameraStatus.OFFLINE
    # cut_to_camera only accepts ONLINE cameras, so an unconnected one is refused.
    assert asyncio.run(again.cut_to_camera("cam1")) is False


@pytest.mark.parametrize("kept", ["error", "maintenance", "offline"])
def test_restart_keeps_statuses_that_are_not_live_connections(cam, tmp_path, kept):
    path = tmp_path / "cameras.json"
    first = cam.AdvancedCameraManager(path)
    first.sources["cam1"].metadata.status = cam.CameraStatus(kept)
    first.save_configuration()
    assert cam.AdvancedCameraManager(path).sources["cam1"].metadata.status.value == kept


def test_from_dict_ignores_unknown_keys_and_accepts_enum_values(cam):
    meta = cam.CameraMetadata.from_dict(
        {"id": "x", "future_field": 1, "name": "N", "transport": "usb_webcam", "role": "backup"}
    )
    assert meta.name == "N"
    assert meta.transport is cam.CameraTransport.USB_WEBCAM
    assert meta.role is cam.CameraRole.BACKUP


@pytest.mark.parametrize(
    "record",
    [
        {"transport": "usb_webcam"},                                    # no name
        {"name": "N"},                                                  # no transport
        {"name": "N", "transport": "not_a_transport"},                  # bad enum
        {"name": "N", "transport": "usb_webcam", "position": "1,2,3"},  # not a list
        {"name": "N", "transport": "usb_webcam", "position": [1, "x", 3]},
        "not an object",
    ],
)
def test_from_dict_rejects_records_that_cannot_describe_a_camera(cam, record):
    with pytest.raises(ValueError):
        cam.CameraMetadata.from_dict(record)


def test_one_bad_camera_entry_does_not_discard_the_others(cam, tmp_path):
    path = tmp_path / "cameras.json"
    first = cam.AdvancedCameraManager(path)
    first.sources["cam2"].metadata.name = "Keep me"
    first.program_source = "cam1"
    first.preview_source = "cam2"
    first.recording_sources = ["cam1", "cam2"]
    first.save_configuration()

    data = json.loads(path.read_text(encoding="utf-8"))
    data["cameras"][0]["transport"] = "garbage"            # cam1 becomes unreadable
    data["cameras"].append({"name": "no id", "transport": "usb_webcam"})
    path.write_text(json.dumps(data), encoding="utf-8")
    original_bytes = path.read_bytes()

    again = cam.AdvancedCameraManager(path)
    assert set(again.sources) == set(first.sources) - {"cam1"}
    assert again.sources["cam2"].metadata.name == "Keep me"
    # Production state may not point at a camera that was not loaded.
    assert again.program_source is None
    assert again.preview_source == "cam2"
    assert again.recording_sources == ["cam2"]
    copies = _unreadable_copies(path)
    assert len(copies) == 1 and copies[0].read_bytes() == original_bytes
    assert path.read_bytes() == original_bytes, "loading must not rewrite the file"


def test_corrupt_file_is_kept_aside_and_defaults_are_used(cam, tmp_path):
    path = tmp_path / "cameras.json"
    path.write_text("{ this is not json", encoding="utf-8")
    manager = cam.AdvancedCameraManager(path)
    assert {"cam1", "cam2"} <= set(manager.sources)
    copies = _unreadable_copies(path)
    assert len(copies) == 1 and copies[0].read_text(encoding="utf-8") == "{ this is not json"
    # A later save replaces the live file but the copy of the original survives.
    manager.save_configuration()
    assert json.loads(path.read_text(encoding="utf-8"))["cameras"]
    assert copies[0].read_text(encoding="utf-8") == "{ this is not json"


def test_backup_never_overwrites_an_earlier_backup(tmp_path):
    path = tmp_path / "cameras.json"
    path.write_text("one", encoding="utf-8")
    first = camera_config_io.preserve_unreadable_config(path)
    path.write_text("two", encoding="utf-8")
    second = camera_config_io.preserve_unreadable_config(path)
    assert first != second
    assert first.read_text(encoding="utf-8") == "one" and second.read_text(encoding="utf-8") == "two"
    assert camera_config_io.preserve_unreadable_config(tmp_path / "missing.json") is None


# --- device and URL handling --------------------------------------------------

class _Probe:
    """Stands in for asyncio.create_subprocess_exec and records the command."""
    def __init__(self):
        self.commands = []

    async def __call__(self, *cmd, **_kwargs):
        self.commands.append(list(cmd))

        class _Proc:
            returncode = 1

            async def communicate(self):
                return b"", b"probe stub"
        return _Proc()


def _source(cam, **meta):
    base = dict(name="n", transport=cam.CameraTransport.USB_WEBCAM)
    base.update(meta)
    return cam.CameraSource("c1", cam.CameraMetadata(**base))


def test_usb_probe_uses_video4linux2_for_dev_paths(cam, monkeypatch):
    probe = _Probe()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", probe)
    asyncio.run(_source(cam, connection_string="/dev/video0")._connect_usb_webcam())
    cmd = probe.commands[0]
    assert cmd[cmd.index("-f") + 1] == "video4linux2"


def test_usb_probe_uses_dshow_for_named_windows_devices(cam, monkeypatch):
    probe = _Probe()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", probe)
    asyncio.run(_source(cam, connection_string="video=Integrated Camera")._connect_usb_webcam())
    cmd = probe.commands[0]
    assert cmd[cmd.index("-f") + 1] == "dshow"


def test_ip_probe_encodes_credentials_that_would_break_the_url(cam, monkeypatch):
    probe = _Probe()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", probe)
    source = _source(cam, transport=cam.CameraTransport.IP_CAMERA, connection_string="10.0.0.5/stream",
                     username="op", password="p@ss:w/rd", port=554)
    asyncio.run(source._connect_ip_camera())
    url = probe.commands[0][probe.commands[0].index("-i") + 1]
    assert url == "rtsp://op:p%40ss%3Aw%2Frd@10.0.0.5/stream:554"
    assert url.count("@") == 1


def test_rtsp_url_leaves_plain_credentials_and_anonymous_urls_unchanged():
    assert camera_config_io.rtsp_url("h", "user", "pass", 554) == "rtsp://user:pass@h:554"
    assert camera_config_io.rtsp_url("h") == "rtsp://h"
    assert camera_config_io.rtsp_url("h", "user", "") == "rtsp://h"


def test_advanced_capture_args_use_the_same_encoded_url():
    cam = importlib.import_module("modules.cameras_advanced")
    source = _source(cam, transport=cam.CameraTransport.IP_CAMERA, connection_string="h",
                     username="u", password="a@b", port=554)
    args = source._capture_input_args()
    assert args[-1] == "rtsp://u:a%40b@h:554"


# --- remove_camera outside an event loop --------------------------------------

def test_remove_camera_works_from_synchronous_code(cam, tmp_path):
    manager = cam.AdvancedCameraManager(tmp_path / "cameras.json")
    manager.program_source, manager.preview_source = "cam1", "cam2"
    manager.recording_sources = ["cam1"]
    assert manager.remove_camera("cam1") is True
    assert "cam1" not in manager.sources
    assert manager.program_source is None and manager.recording_sources == []
    saved = json.loads((tmp_path / "cameras.json").read_text(encoding="utf-8"))
    assert "cam1" not in [c["id"] for c in saved["cameras"]]
    assert {c["id"] for c in saved["cameras"]} == set(manager.sources)


def test_remove_camera_still_disconnects_when_a_loop_is_running(cam, tmp_path):
    async def scenario():
        manager = cam.AdvancedCameraManager(tmp_path / "cameras.json")
        called = []

        async def fake_disconnect(camera_id):
            called.append(camera_id)
        manager.disconnect_camera = fake_disconnect
        assert manager.remove_camera("cam2") is True
        await asyncio.sleep(0)
        return called
    assert asyncio.run(scenario()) == ["cam2"]


# --- simple camera registry (modules/cameras.py) --------------------------------

def _registry():
    manager = CameraManager()
    for cid in ("a", "b"):
        manager.register(SimpleSource(source_id=cid, name=cid, location="x", description="", transport=SimpleTransport.VIRTUAL,
                                      endpoint=f"virtual://{cid}"))
    manager.set_program_source("a")
    manager.set_preview_source("b")
    return manager


def test_removing_program_or_preview_clears_it_and_take_refuses():
    manager = _registry()
    events = []
    manager.subscribe(events.append)
    manager.remove("a")
    assert manager.get_program_source() is None and manager._program_source is None
    assert manager.take() is False
    assert events == [], "take() must not emit switch events for a removed camera"
    manager.remove("b")
    assert manager._preview_source is None


def test_removing_an_unrelated_camera_leaves_program_and_preview():
    manager = _registry()
    manager.register(SimpleSource(source_id="c", name="c", location="x", description="", transport=SimpleTransport.VIRTUAL, endpoint="virtual://c"))
    manager.remove("c")
    assert manager._program_source == "a" and manager._preview_source == "b"


@pytest.mark.parametrize("field", ["position", "rotation"])
@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_update_pose_rejects_non_finite_values_without_changing_state(field, bad):
    manager = _registry()
    source = manager.get("a")
    before = (list(source.position), list(source.rotation))
    events = []
    manager.subscribe(events.append)
    with pytest.raises(ValueError):
        manager.update_pose("a", **{field: [0.0, bad, 0.0]})
    assert (source.position, source.rotation) == before and events == []


def test_update_pose_still_accepts_valid_values():
    manager = _registry()
    assert manager.update_pose("a", position=[1, 2, 3], rotation=[0, 90, 0], fov=50, focus=4) is True
    assert manager.get("a").position == [1.0, 2.0, 3.0]


# --- Rust camera runtime --------------------------------------------------------

class _FakeSocket:
    def __init__(self):
        self.sent = []
        self._last = None

    async def send(self, text):
        self._last = json.loads(text)
        self.sent.append(self._last)

    async def recv(self):
        return json.dumps({"protocol": PROTOCOL, "request_id": self._last["request_id"], "ok": True,
                           "stage": 1, "frame_path_proven": False})

    async def close(self):
        pass


def _runtime():
    cameras = CameraManager()
    runtime = RustCameraRuntime(cameras, enabled=True, token="t")
    runtime._sub = cameras.subscribe(runtime._on_camera_event)   # what start() does
    return cameras, runtime


def test_pose_switch_and_register_all_carry_focus():
    async def scenario():
        cameras, runtime = _runtime()
        runtime._ws = _FakeSocket()
        cameras.update_pose("rust_cam_1", focus=7.5)
        cameras.set_program_source("rust_cam_1")
        await runtime._register(cameras.get("rust_cam_1"))
        for _ in range(3):
            await asyncio.sleep(0)
        await asyncio.gather(*list(runtime._tasks))
        return runtime._ws.sent
    sent = asyncio.run(scenario())
    by_action = {m["action"]: m for m in sent}
    assert {"update_pose", "set_mode", "register"} <= set(by_action)
    assert all(m["focus"] == 7.5 for m in sent)


def test_mirror_failure_is_recorded_not_lost():
    async def scenario():
        cameras, runtime = _runtime()           # no socket connected
        cameras.set_program_source("rust_cam_1")
        await asyncio.gather(*list(runtime._tasks))
        return runtime
    runtime = asyncio.run(scenario())
    assert runtime.last_error and "camera mirror failed" in runtime.last_error
    assert not runtime._tasks, "finished tasks must be released"


def test_runtime_keeps_a_reference_to_in_flight_mirror_tasks_and_stop_cancels_them():
    async def scenario():
        cameras, runtime = _runtime()

        class Slow(_FakeSocket):
            async def recv(self):
                await asyncio.sleep(30)
        runtime._ws = Slow()
        cameras.set_program_source("rust_cam_1")
        await asyncio.sleep(0.05)
        in_flight = len(runtime._tasks)
        await asyncio.wait_for(runtime.stop(), timeout=5)
        return in_flight, len(runtime._tasks)
    in_flight, after_stop = asyncio.run(scenario())
    assert in_flight == 1 and after_stop == 0


def test_status_still_never_claims_frame_proof():
    _, runtime = _runtime()
    assert runtime.status()["frame_path_proven"] is False


# --- capture engine: test-pattern recordings are labeled ------------------------

def _need_ffmpeg():
    import shutil
    import subprocess
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    encoders = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    if "libx264" not in encoders:
        pytest.skip("ffmpeg has no libx264")


def _profile():
    from types import SimpleNamespace
    return SimpleNamespace(container=SimpleNamespace(value="mp4"), video_codec="h264", audio_codec=None,
                           video_bitrate="500k", audio_bitrate=None, resolution="320x180", frame_rate="10",
                           is_audio_only=False)


def test_virtual_recordings_are_marked_synthetic_and_real_media_is_not(tmp_path):
    _need_ffmpeg()
    import subprocess
    from modules.capture import FFmpegCaptureEngine

    real_clip = tmp_path / "real.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=c=blue:size=160x90:rate=10",
                    "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(real_clip)], check=True)
    virtual = SimpleSource(source_id="virt", name="v", location="x", description="", transport=SimpleTransport.VIRTUAL,
                           endpoint="virtual://virt")
    from_file = SimpleSource(source_id="clip", name="c", location="x", description="", transport=SimpleTransport.FILE,
                             endpoint=str(real_clip))

    async def scenario():
        engine = FFmpegCaptureEngine()
        out_dir = tmp_path / "session"
        out_dir.mkdir()
        started = await engine.start("s1", [virtual, from_file], _profile(), out_dir)
        await asyncio.sleep(2.0)
        stopped = await engine.stop("s1")
        return engine, started, stopped

    engine, started, stopped = asyncio.run(scenario())
    names = {p.name for p in started}
    assert names == {"virt.mp4", "clip.mp4"}
    assert [p.name for p in engine.synthetic_outputs("s1")] == ["virt.mp4"]
    assert engine.synthetic_outputs("unknown-session") == []
    assert any(p.name == "virt.mp4" for p in stopped), "the test-pattern file was still recorded"



def test_stop_route_marks_the_saved_artifact_of_a_virtual_camera_as_synthetic(tmp_path):
    """Through the real production router: start, record a moment, stop, reload from disk."""
    _need_ffmpeg()
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from modules.auth import create_access_token
    from modules.cameras import create_default_cameras
    from modules.production_routes import create_production_router
    from modules.recording import create_recording_service

    headers = {"Authorization": "Bearer " + create_access_token({"sub": "producer", "role": "owner"})}
    cameras = create_default_cameras()                       # wide_shot is a virtual camera
    recording = create_recording_service(tmp_path, cameras)
    app = FastAPI()
    app.include_router(create_production_router(cameras, recording, None))
    with TestClient(app) as client:
        started = client.post("/api/recording/start", headers=headers,
                              json={"sources": ["wide_shot"], "countdown_seconds": 0, "session_id": "synthetic-demo"})
        started.raise_for_status()
        import time
        time.sleep(2.0)
        stopped = client.post("/api/recording/synthetic-demo/stop", headers=headers)
        stopped.raise_for_status()

    artifacts = stopped.json()["artifacts"]
    assert artifacts and artifacts[0]["name"].endswith(".mp4") and artifacts[0]["size_bytes"] > 0
    assert artifacts[0]["synthetic"] is True
    # It must survive a restart of the recording service (metadata on disk).
    reopened = create_recording_service(tmp_path, cameras)
    assert reopened.get_session("synthetic-demo").artifacts[0].synthetic is True
    assert reopened.get_session("synthetic-demo").to_dict()["artifacts"][0]["synthetic"] is True


def test_real_artifacts_keep_the_exact_old_record_shape(tmp_path):
    from modules.cameras import create_default_cameras
    from modules.recording import create_recording_service
    cameras = create_default_cameras()
    recording = create_recording_service(tmp_path, cameras)
    session = recording.start_session(session_id="plain", sources=["wide_shot"], profile_id="broadcast_mp4",
                                      operator="o", countdown_seconds=0)
    clip = recording._session_dir(session.session_id) / "clip.mp4"
    clip.write_bytes(b"not really media")
    recording.register_artifact("plain", clip)
    record = recording.get_session("plain").to_dict()["artifacts"][0]
    assert set(record) == {"name", "format", "file_path", "size_bytes"}, "no new key unless synthetic"
