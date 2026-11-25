"""A 3D avatar in front of a camera, moving live.

Two layers:

* quick checks of the host route that serves a camera's authoritative pose and the avatar's motion, and
* one end-to-end proof that starts the real host (``run_reach.py``, throwaway data folder), registers a studio 2.5D
  camera through the real API, opens ``static/camera_avatar_live.html`` in real Chromium (WebGL) with Josie's
  ``assets/avatar/manny.glb``, and judges the *pixels the camera produced*, fetched back from the host's own frame
  endpoint as JPEG: the avatar is on screen, the frames change over time (it moves), and moving the camera through
  ``/api/switcher/ptz`` changes the avatar's size and position in frame the way a real camera move would.

Set CAMERA_EVIDENCE_DIR to keep the captured frames. Skips without Playwright/Chromium.
"""
from __future__ import annotations

import io
import math
import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from modules import camera_view_routes
from modules.cameras import CameraManager, CameraSource, CameraTransport

ROOT = Path(__file__).resolve().parents[1]


# --- host route and motion ------------------------------------------------------------------------------
def test_demo_motion_is_deterministic_finite_and_moves_over_time():
    a = camera_view_routes.demo_motion("avatar", 3.0)
    assert a == camera_view_routes.demo_motion("avatar", 3.0)
    assert a["avatar_id"] == "avatar"
    later = camera_view_routes.demo_motion("avatar", 3.4)
    assert later["bones"] != a["bones"]
    for command in (a, later):
        for bone in command["bones"].values():
            assert len(bone["rotation"]) == 3 and all(math.isfinite(v) for v in bone["rotation"])
            assert all(abs(v) <= 35 for v in bone["rotation"]), "stays inside the consumer's 35 degree default limit"


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_demo_motion_rejects_non_finite_time(bad):
    with pytest.raises(ValueError):
        camera_view_routes.demo_motion("avatar", bad)


@pytest.fixture
def view_client(monkeypatch):
    async def allowed():
        return {"authenticated": True, "role": "crew"}
    monkeypatch.setattr(camera_view_routes, "require_role", lambda role: allowed)
    cameras = CameraManager()
    cameras.register(CameraSource(source_id="cam_a", name="A", location="virtual", description="test camera", transport=CameraTransport.VIRTUAL,
                                  endpoint="virtual://cam_a", position=(0.0, 1.5, 4.0), rotation=(5.0, 10.0, 0.0), fov=40.0))
    clock = {"t": 100.0}
    app = FastAPI()
    app.include_router(camera_view_routes.create_camera_view_router(cameras, clock=lambda: clock["t"]))
    return TestClient(app), cameras, clock


def test_state_reports_the_registry_pose_and_changes_with_it(view_client):
    client, cameras, clock = view_client
    body = client.get("/api/camera-view/state/cam_a?avatar=manny").json()
    assert body["pose"]["position"] == [0.0, 1.5, 4.0] and body["pose"]["rotation"] == [5.0, 10.0, 0.0] and body["pose"]["fov"] == 40.0
    assert body["motion"]["source"] == "demo_performer" and body["motion"]["avatar_id"] == "manny"
    assert body["program"] is False
    assert cameras.update_pose("cam_a", position=[1.0, 2.0, 3.0], fov=60.0)
    moved = client.get("/api/camera-view/state/cam_a?avatar=manny").json()
    assert moved["pose"]["position"] == [1.0, 2.0, 3.0] and moved["pose"]["fov"] == 60.0
    clock["t"] = 100.5
    assert client.get("/api/camera-view/state/cam_a?avatar=manny").json()["motion"]["bones"] != body["motion"]["bones"]


def test_state_marks_the_program_camera(view_client):
    client, cameras, _ = view_client
    assert cameras.set_program_source("cam_a")
    assert client.get("/api/camera-view/state/cam_a").json()["program"] is True


def test_state_rejects_unknown_camera_and_bad_avatar_ids(view_client):
    client, _, _ = view_client
    assert client.get("/api/camera-view/state/nope").status_code == 404
    assert client.get("/api/camera-view/state/cam_a?avatar=../x").status_code == 400
    assert client.get("/api/camera-view/state/cam_a?avatar=" + "a" * 65).status_code == 400


# --- the real thing -------------------------------------------------------------------------------------
sync_api = pytest.importorskip("playwright.sync_api")
np = pytest.importorskip("numpy")
PIL_Image = pytest.importorskip("PIL.Image")

CAM = "p25d_avatar_cam"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def host(tmp_path_factory):
    data = tmp_path_factory.mktemp("camera_view_userdata")
    port = _free_port()
    log = open(data / "host.log", "w", encoding="utf-8")
    proc = subprocess.Popen([sys.executable, "run_reach.py", "--port", str(port), "--data-dir", str(data),
                             "--no-browser", "--no-legacy-2i"], cwd=str(ROOT), stdout=log, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.time() + 120
        while time.time() < deadline:
            if proc.poll() is not None:
                log.flush()
                raise AssertionError("run_reach.py exited during startup:\n" + (data / "host.log").read_text(encoding="utf-8")[-4000:])
            try:
                if httpx.get(base + "/health", timeout=0.5).status_code == 200:
                    break
            except httpx.RequestError:
                pass
            time.sleep(0.3)
        else:
            raise AssertionError("PubCast did not start within 120 s")
        password = secrets.token_urlsafe(20)
        user = "camera-proof"
        assert httpx.post(base + "/api/auth/setup", json={"username": user, "password": password, "confirm_password": password}, timeout=10).status_code in (200, 201)
        login = httpx.post(base + "/api/auth/login", json={"username": user, "password": password}, timeout=10)
        assert login.status_code == 200, login.text
        yield {"base": base, "user": user, "password": password, "auth": {"Authorization": "Bearer " + login.json()["access_token"]}}
    finally:
        proc.terminate()
        try:
            proc.wait(20)
        except subprocess.TimeoutExpired:
            proc.kill(); proc.wait(5)
        log.close()


def _avatar_mask(img) -> "np.ndarray":
    """The avatar is Manny's emissive cyan; the floor, grid and backdrop are dark and neutral."""
    a = np.asarray(img.convert("RGB")).astype(int)
    return (a[..., 1] > 170) & (a[..., 2] > 170) & (a[..., 0] < 110)


def _bbox(mask):
    ys, xs = np.where(mask)
    assert len(xs) > 50, "no avatar pixels in the camera's frame"
    return xs.min(), ys.min(), xs.max(), ys.max()


def _fetch_frame(host, sequence_after=0, timeout=10.0):
    """The newest JPEG the host holds for the camera, newer than ``sequence_after``. Returns (sequence, image, raw bytes)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = httpx.get(host["base"] + f"/api/studio25d/media/frames/{CAM}", headers=host["auth"], timeout=5)
        if r.status_code == 200 and int(r.headers["X-Studio-Sequence"]) > sequence_after:
            assert r.headers["content-type"] == "image/jpeg"
            return int(r.headers["X-Studio-Sequence"]), PIL_Image.open(io.BytesIO(r.content)).convert("RGB"), r.content
        time.sleep(0.1)
    raise AssertionError(f"no new frame for {CAM} after sequence {sequence_after}: last status {r.status_code} {r.text[:120]}")


def _wait_for_pose(page, position, fov=None):
    page.wait_for_function("""([pos, fov]) => { const p = window.__camView.lastPose; return p && Math.abs(p.position[0]-pos[0])<1e-6 && Math.abs(p.position[1]-pos[1])<1e-6 && Math.abs(p.position[2]-pos[2])<1e-6 && (fov===null || Math.abs(p.fov-fov)<1e-6); }""",
                           arg=[list(position), fov], timeout=10000)


def test_avatar_moves_live_in_front_of_a_registered_camera(host):
    base, auth = host["base"], host["auth"]
    reg = httpx.post(base + "/api/cameras/register", headers=auth, timeout=10, json={
        "source_id": CAM, "name": "Avatar cam", "location": "studio25d", "tags": ["studio25d"],
        "position": [0, 1.3, 4.0], "rotation": [0, 0, 0], "fov": 45})
    assert reg.status_code == 200, reg.text
    evidence = os.environ.get("CAMERA_EVIDENCE_DIR")
    if evidence:
        Path(evidence).mkdir(parents=True, exist_ok=True)

    def keep(name, raw):
        if evidence:
            (Path(evidence) / name).write_bytes(raw)

    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch(args=["--no-sandbox"])
        except Exception as exc:
            pytest.skip(f"no Chromium available: {exc}")
        try:
            page = browser.new_page(viewport={"width": 900, "height": 560})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(base + f"/login?next=%2Fstatic%2Fcamera_avatar_live.html%3Fcamera%3D{CAM}%26publish%3D1")
            page.fill("#loginUser", host["user"])
            page.fill("#loginPass", host["password"])
            page.keyboard.press("Enter")
            page.wait_for_url("**/camera_avatar_live**", timeout=20000)
            try:
                page.wait_for_function("window.__camView.loaded && window.__camView.published > 3", timeout=45000)
            except Exception:
                raise AssertionError("avatar never started publishing frames: " + str(page.evaluate("window.__camView")))
            view = page.evaluate("window.__camView")
            assert view["error"] == "" and view["publishErrors"] == 0
            assert view["repairedBufferViews"] == 7, "manny.glb is spec-invalid on disk and is repaired in memory only"
            assert {"head", "right_arm", "right_forearm"} <= set(view["mapped"]), "the avatar's bones are bound to the motion consumer"

            # 1. a real avatar is in the camera's frame
            seq, first, raw = _fetch_frame(host)
            keep("01_wide.jpg", raw)
            x0, y0, x1, y1 = _bbox(_avatar_mask(first))
            assert (y1 - y0) > 120, "the standing avatar fills a good part of the 360 px frame"
            assert 200 < (x0 + x1) / 2 < 440, "and is near the middle, because the camera looks straight at the origin"

            # 2. it moves: frames from different times differ, and the difference is where the avatar is
            frames = [first]
            for _ in range(4):
                time.sleep(0.35)
                seq, img, raw = _fetch_frame(host, sequence_after=seq)
                frames.append(img)
                keep(f"02_motion_{len(frames)}.jpg", raw)
            diffs = []
            for later in frames[1:]:
                d = np.abs(np.asarray(first).astype(int) - np.asarray(later).astype(int)).sum(axis=2)
                diffs.append(int((d > 60).sum()))
            assert max(diffs) > 150, f"live motion changes the picture: changed pixels {diffs}"
            ever_changed = np.zeros(first.size[::-1], dtype=bool)
            for later in frames[1:]:
                ever_changed |= np.abs(np.asarray(first).astype(int) - np.asarray(later).astype(int)).sum(axis=2) > 60
            ys, xs = np.where(ever_changed)
            assert x0 - 10 <= xs.min() and xs.max() <= x1 + 40, "the changes are on the avatar, not on the empty set"
            assert page.evaluate("window.__camView.applied") > 20, "the host's motion commands reached the avatar's bones"

            # 3. moving the camera changes the framing: dolly in makes the avatar bigger
            moved = httpx.post(base + f"/api/switcher/ptz/{CAM}", headers=auth, timeout=10, json={"position": [0, 1.3, 3.0]})
            assert moved.status_code == 200, moved.text
            _wait_for_pose(page, (0, 1.3, 3.0))
            seq, near, raw = _fetch_frame(host, sequence_after=seq + 1)
            keep("03_dolly_in.jpg", raw)
            near_height = _bbox(_avatar_mask(near))[3] - _bbox(_avatar_mask(near))[1]
            assert near_height > 1.2 * (y1 - y0), f"dolly in from 4 m to 3 m should grow the avatar by about a third: {y1 - y0}px -> {near_height}px tall"

            # 4. pan right: the avatar slides left in the frame
            reset = httpx.post(base + f"/api/switcher/ptz/{CAM}", headers=auth, timeout=10, json={"position": [0, 1.3, 4.0], "rotation": [0, 0, 0]})
            assert reset.status_code == 200
            _wait_for_pose(page, (0, 1.3, 4.0))
            seq, straight, _ = _fetch_frame(host, sequence_after=seq + 1)
            sx = (lambda b: (b[0] + b[2]) / 2)(_bbox(_avatar_mask(straight)))
            panned = httpx.post(base + f"/api/switcher/ptz/{CAM}", headers=auth, timeout=10, json={"rotation": [0, 15, 0]})
            assert panned.status_code == 200
            page.wait_for_function("window.__camView.lastPose.rotation[1] === 15", timeout=10000)
            seq, right, raw = _fetch_frame(host, sequence_after=seq + 1)
            keep("04_pan_right.jpg", raw)
            assert (lambda b: (b[0] + b[2]) / 2)(_bbox(_avatar_mask(right))) < sx - 60, "pan right moves the subject left in frame"

            # 5. tilt up: the subject slides down in frame
            httpx.post(base + f"/api/switcher/ptz/{CAM}", headers=auth, timeout=10, json={"rotation": [0, 0, 0]})
            page.wait_for_function("window.__camView.lastPose.rotation[1] === 0", timeout=10000)
            seq, level, _ = _fetch_frame(host, sequence_after=seq + 1)
            ly = _bbox(_avatar_mask(level))[1]
            httpx.post(base + f"/api/switcher/ptz/{CAM}", headers=auth, timeout=10, json={"rotation": [10, 0, 0]})
            page.wait_for_function("window.__camView.lastPose.rotation[0] === 10", timeout=10000)
            seq, up, raw = _fetch_frame(host, sequence_after=seq + 1)
            keep("05_tilt_up.jpg", raw)
            assert _bbox(_avatar_mask(up))[1] > ly + 30, "tilt up moves the subject down in frame"
            assert errors == []
        finally:
            browser.close()
