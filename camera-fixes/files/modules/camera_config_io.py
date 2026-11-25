# PubCast AI - camera_config_io.py
# Rearview Foresight - Feic Mo Chroi
"""Shared, tolerant helpers for persisted camera configuration.

Added 2026-11-21 so ``modules.cameras_advanced`` and
``modules.evo.camera_manager`` read their saved configuration the same way.

Why this exists: ``save_configuration`` writes each camera as its metadata plus
an ``id`` key, but ``CameraMetadata.from_dict`` was ``cls(**data)``. The ``id``
key made construction raise ``TypeError``; the manager caught that, logged it
and silently rebuilt the two default cameras, so every restart discarded the
saved names, roles, program/preview picks and recording assignments. Enums and
tuples also come back from JSON as plain strings and lists.

Nothing here deletes or overwrites user data. An unreadable configuration is
copied aside before the manager falls back to defaults.
"""
from __future__ import annotations

import dataclasses
import enum
import logging
import time
import typing
from pathlib import Path
from typing import Any, Mapping, Optional
from urllib.parse import quote

logger = logging.getLogger(__name__)

# A camera that was connecting, online or recording when the process stopped is
# not connected after a restart. Loading those values verbatim would let
# cut_to_camera() treat an unconnected camera as live.
RUNTIME_ONLY_STATUS_VALUES = frozenset({"connecting", "online", "recording"})


def metadata_from_record(cls, record: Mapping[str, Any], *, reset_runtime_state: bool = False):
    """Build a ``CameraMetadata`` dataclass from a saved or API record.

    * unknown keys (such as the ``id`` written by ``save_configuration``) are
      ignored, so records stay readable when fields are added or removed;
    * enum fields accept the enum member or its value;
    * ``Tuple[...]`` fields come back as tuples and ``List[...]`` as lists;
    * with ``reset_runtime_state`` a persisted connecting/online/recording
      status becomes ``offline``.

    Raises ``ValueError`` for a record that cannot describe a camera (missing
    required fields or an invalid enum value).
    """
    if not isinstance(record, Mapping):
        raise ValueError(f"camera record must be an object, not {type(record).__name__}")
    fields = {f.name: f for f in dataclasses.fields(cls) if f.init}
    hints = typing.get_type_hints(cls)
    values = {}
    for key, raw in record.items():
        if key not in fields:
            continue
        values[key] = _coerce(hints.get(key), raw, key)
    missing = [
        name for name, f in fields.items()
        if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING and name not in values
    ]
    if missing:
        raise ValueError("camera record is missing required field(s): " + ", ".join(sorted(missing)))
    if reset_runtime_state and "status" in values:
        status_type = hints.get("status")
        current = getattr(values["status"], "value", values["status"])
        if current in RUNTIME_ONLY_STATUS_VALUES and isinstance(status_type, type) and issubclass(status_type, enum.Enum):
            values["status"] = status_type("offline")
    return cls(**values)


def _coerce(hint, raw, key):
    if isinstance(hint, type) and issubclass(hint, enum.Enum):
        try:
            return raw if isinstance(raw, hint) else hint(raw)
        except ValueError as exc:
            raise ValueError(f"{key}: {raw!r} is not a valid {hint.__name__}") from exc
    origin = typing.get_origin(hint)
    if origin is tuple:
        if not isinstance(raw, (list, tuple)):
            raise ValueError(f"{key}: expected a list of numbers")
        return tuple(_number(item, key) for item in raw)
    if origin is list:
        if not isinstance(raw, (list, tuple)):
            raise ValueError(f"{key}: expected a list")
        return list(raw)
    return raw


def _number(value, key):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{key}: expected numbers, got {value!r}")
    return float(value)


def preserve_unreadable_config(path: Path) -> Optional[Path]:
    """Copy a configuration the manager could not fully load to a sibling file.

    The next ``save_configuration`` would otherwise overwrite it with defaults.
    The original is left in place; an existing backup is never overwritten.
    Returns the backup path, or ``None`` when nothing could be copied.
    """
    path = Path(path)
    try:
        if not path.is_file():
            return None
        stamp = time.strftime("%Y%m%d-%H%M%S")
        target = path.with_name(f"{path.name}.unreadable-{stamp}")
        counter = 1
        while target.exists():
            counter += 1
            target = path.with_name(f"{path.name}.unreadable-{stamp}-{counter}")
        target.write_bytes(path.read_bytes())
        logger.warning("Camera configuration could not be fully loaded; a copy was kept at %s", target)
        return target
    except OSError:
        logger.exception("Could not preserve unreadable camera configuration %s", path)
        return None


def usb_ffmpeg_format(device: str) -> str:
    """Input format for a USB/virtual webcam device string.

    Matches the rule ``CameraSource._capture_input_args`` already uses: a
    ``/dev/...`` path is Video4Linux2, anything else is DirectShow. The probe
    previously tested ``"linux" in device_path``, which is false for
    ``/dev/video0`` and sent Linux cameras down the DirectShow path.
    """
    return "video4linux2" if str(device).startswith("/dev/") else "dshow"


def rtsp_url(host: str, username: str = "", password: str = "", port: int = 0) -> str:
    """Build an RTSP URL, percent-encoding credentials.

    An unencoded ``@``, ``:`` or ``/`` in a password changes which part of the
    URL is the host. Credentials made only of unreserved characters are
    unchanged, so existing configurations keep working.
    """
    if username and password:
        url = f"rtsp://{quote(str(username), safe='')}:{quote(str(password), safe='')}@{host}"
    else:
        url = f"rtsp://{host}"
    if port:
        url += f":{port}"
    return url
