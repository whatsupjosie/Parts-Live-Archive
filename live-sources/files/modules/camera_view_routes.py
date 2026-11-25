"""Live camera view: the host side of "put a 3D avatar in front of a camera and watch it move".

Two things the browser needs each tick, from one authoritative place:

* the camera's pose, read from the canonical ``CameraManager`` (``modules/cameras.py``) so a pose change made through
  ``/api/switcher/ptz`` (or any other writer) is exactly what the view renders; and
* the avatar's motion, as bone rotations in the command format ``static/avatar_glb_motion_consumer.js`` already consumes.

Pose convention (the one the registry defaults imply: ``wide_shot`` stands at z=+5 and must face the stage at the
origin, so a camera with rotation ``[0, 0, 0]`` looks along -z): ``rotation`` is ``[tilt, pan, roll]`` degrees, tilt
positive looks up, pan positive turns to the right, roll positive rolls clockwise on screen. ``static/camera_rig.js``
documents pan 0 as +z instead; that disagreement is recorded in the handoff ledger and not silently resolved here.

The motion source is a deterministic host-clock performance (breathing, a head turn, a waving arm). It is a demo
performer standing in for a real mocap/performer feed, and every response says so in ``motion.source``.
"""
from __future__ import annotations

import math
import re
import time
from typing import Any, Callable, Dict

from fastapi import APIRouter, Depends, HTTPException

from modules.route_security import require_role

_AVATAR_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def demo_motion(avatar_id: str, t: float) -> Dict[str, Any]:
    """Bone rotations (degrees) for host time ``t`` seconds, in the consumer's ``{"bones": {name: {"rotation": [x, y, z]}}}`` shape."""
    if not math.isfinite(t):
        raise ValueError("t must be finite")
    tau = 2.0 * math.pi
    breath = math.sin(tau * 0.25 * t)
    wave = math.sin(tau * 0.9 * t)
    look = math.sin(tau * 0.2 * t)
    bones = {
        "spine_02": {"rotation": [3.0 * breath, 6.0 * look, 0.0]},
        "head": {"rotation": [4.0 * breath, 30.0 * look, 0.0]},
        "right_arm": {"rotation": [0.0, 0.0, -25.0 - 8.0 * wave]},
        "right_forearm": {"rotation": [0.0, 0.0, -20.0 + 15.0 * wave]},
        "left_arm": {"rotation": [0.0, 0.0, 12.0 + 5.0 * breath]},
    }
    # kind "motion" is applied 1:1; the consumer scales "procedural_animation" by 0.55 and clamps every angle to 35 degrees.
    return {"avatar_id": avatar_id, "kind": "motion", "weight": 1.0, "bones": bones}


def create_camera_view_router(cameras: Any, clock: Callable[[], float] = time.monotonic) -> APIRouter:
    router = APIRouter(prefix="/api/camera-view", tags=["Camera View"])

    @router.get("/state/{camera_id}")
    async def state(camera_id: str, avatar: str = "avatar", identity: Dict[str, Any] = Depends(require_role("crew"))):
        if not _AVATAR_ID.fullmatch(avatar):
            raise HTTPException(400, "avatar must be 1-64 letters, digits, '_' or '-'")
        source = cameras.get(camera_id)
        if source is None:
            raise HTTPException(404, "camera not found")
        now = clock()
        return {
            "camera_id": camera_id,
            "pose": {
                "position": list(source.position), "rotation": list(source.rotation),
                "fov": source.fov, "focus": getattr(source, "focus", None),
            },
            "program": getattr(cameras.get_program_source(), "source_id", None) == camera_id,
            "server_time": now,
            "motion": {"source": "demo_performer", **demo_motion(avatar, now)},
        }

    return router
