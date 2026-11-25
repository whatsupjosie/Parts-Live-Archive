"""Connecting real cameras and microphones: what the host can offer a phone, a pro camera or a microphone.

* ``POST /api/live-sources/listener`` registers a camera that PUSHES to this host (a phone streaming app, a camera
  with RTMP/SRT output, OBS on another machine) and returns the exact address to type into that device.
* ``GET /api/live-sources/audio-inputs`` lists the microphones FFmpeg can open on this host (USB, or XLR through an
  audio interface), in the form a recording request's ``audio_input`` takes.

The listener is only open while a recording of that camera is running (FFmpeg is the server while it records); the
stream key in the address is the secret that stops anyone else pushing into it.
"""
from __future__ import annotations

import re
import secrets
import socket
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException

from modules.audio_input_spec import list_audio_inputs
from modules.cameras import CameraSource, CameraTransport
from modules.route_security import require_role

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,63}$")
_KEY = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
_DEFAULT_PORT = {"rtmp": 1935, "srt": 9000}


def lan_addresses() -> List[str]:
    """This machine's non-loopback IPv4 addresses, best first. Empty when it has none (offline)."""
    found: List[str] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))        # no packet is sent; this only asks the OS which address it would use
            found.append(probe.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.append(info[4][0])
    except OSError:
        pass
    seen: List[str] = []
    for address in found:
        if address and not address.startswith("127.") and address != "0.0.0.0" and address not in seen:
            seen.append(address)
    return seen


def _port_is_free(kind: str, port: int) -> bool:
    sock_type = socket.SOCK_STREAM if kind == "rtmp" else socket.SOCK_DGRAM
    with socket.socket(socket.AF_INET, sock_type) as probe:
        try:
            probe.bind(("0.0.0.0", port))
            return True
        except OSError:
            return False


def create_live_source_router(cameras: Any) -> APIRouter:
    router = APIRouter(prefix="/api/live-sources", tags=["Live Sources"])

    @router.get("/audio-inputs")
    async def audio_inputs(identity: Dict[str, Any] = Depends(require_role("crew"))):
        return {"inputs": list_audio_inputs(),
                "use": "send one as audio_input in POST /api/recording/start; it is recorded with the first source"}

    @router.post("/listener")
    async def register_listener(body: Dict[str, Any], identity: Dict[str, Any] = Depends(require_role("mod"))):
        kind = str(body.get("kind", "rtmp")).lower()
        if kind not in _DEFAULT_PORT:
            raise HTTPException(400, "kind must be rtmp or srt")
        name = str(body.get("name", "")).strip() or ("Phone" if kind == "rtmp" else "Camera")
        if not _NAME.fullmatch(name):
            raise HTTPException(400, "name must be 1-64 letters, digits, spaces, '.', '_' or '-'")
        try:
            port = int(body.get("port", _DEFAULT_PORT[kind]))
        except (TypeError, ValueError):
            raise HTTPException(400, "port must be a number")
        if not 1024 <= port <= 65535:
            raise HTTPException(400, "port must be between 1024 and 65535")
        key = str(body.get("key") or secrets.token_urlsafe(9))
        if not _KEY.fullmatch(key):
            raise HTTPException(400, "key must be 8-64 letters, digits, '_' or '-'")
        for existing in cameras.list_sources():
            endpoint = getattr(existing, "endpoint", "") or ""
            if endpoint.startswith(f"{kind}://0.0.0.0:{port}"):
                raise HTTPException(409, f"port {port} is already used by camera {existing.source_id}")
        if not _port_is_free(kind, port):
            raise HTTPException(409, f"port {port} is in use on this machine")
        source_id = "live_" + re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") + "_" + secrets.token_hex(2)
        endpoint = f"rtmp://0.0.0.0:{port}/live/{key}" if kind == "rtmp" else f"srt://0.0.0.0:{port}"
        cameras.register(CameraSource(
            source_id=source_id, name=name, location="live", description=f"{kind.upper()} feed pushed to this host",
            transport=CameraTransport.RTMP if kind == "rtmp" else CameraTransport.SRT, endpoint=endpoint, tags=["live-source"]))
        hosts = lan_addresses() or ["<this-computer's-address>"]
        push = [f"rtmp://{h}:{port}/live/{key}" if kind == "rtmp" else f"srt://{h}:{port}" for h in hosts]
        return {"source_id": source_id, "kind": kind, "port": port, "key": key, "push_urls": push,
                "note": "The host listens only while a recording of this camera is running. "
                        "Start streaming from the device after you start the recording." +
                        (" The address contains the stream key: share it only with the device." if kind == "rtmp" else
                         " SRT here has no key or passphrase: use it on a trusted network.")}

    return router
