"""A microphone (USB, or XLR through an audio interface) as an FFmpeg audio input for the main recorder.

``modules/audio_devices.py`` lists and tests input devices through PortAudio for the browser/server mic pipeline, but
FFmpeg opens audio devices by its own names, and the recorder in ``modules/capture.py`` had no way to take any. This
module is the bridge: a small, strictly validated text form that a recording request can carry.

    dshow:<DirectShow device name>      Windows, e.g.  dshow:Microphone (Focusrite USB Audio)
    alsa:<ALSA device>                  Linux,   e.g.  alsa:hw:1,0   or   alsa:plughw:CARD=USB,DEV=0
    pulse:<source name>                 Linux,   e.g.  pulse:default  or  pulse:alsa_input.usb-Focusrite...
    avfoundation:<index or name>        macOS,   e.g.  avfoundation:0

Nothing else is accepted: the value is never a free-form FFmpeg input, so a request cannot make the host read a file
or open a network address. Names are passed to FFmpeg as one argument each, never through a shell.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import List, Tuple

_KINDS = ("dshow", "alsa", "pulse", "avfoundation")
_MAX_NAME = 200
_ALSA_PULSE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.:,=@+-]*$")
_AVF = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_ .()-]*$")


class AudioInputError(ValueError):
    """The audio input text is not one of the accepted forms."""


@dataclass(frozen=True)
class AudioInput:
    kind: str
    device: str

    def ffmpeg_args(self) -> List[str]:
        if self.kind == "dshow":
            return ["-f", "dshow", "-i", f"audio={self.device}"]
        if self.kind == "avfoundation":
            return ["-f", "avfoundation", "-i", f":{self.device}"]
        return ["-f", self.kind, "-i", self.device]

    def describe(self) -> str:
        return f"{self.kind}:{self.device}"


def parse_audio_input(value: str) -> AudioInput:
    text = str(value or "").strip()
    kind, sep, device = text.partition(":")
    kind = kind.lower()
    if not sep or kind not in _KINDS:
        raise AudioInputError(f"audio input must start with one of {', '.join(k + ':' for k in _KINDS)}")
    device = device.strip()
    if not device or len(device) > _MAX_NAME:
        raise AudioInputError(f"audio device name must be 1-{_MAX_NAME} characters")
    if any(ord(c) < 32 or ord(c) == 127 for c in device):
        raise AudioInputError("audio device name contains control characters")
    if kind == "dshow":
        if '"' in device or device.startswith("-"):
            raise AudioInputError("DirectShow device name has characters FFmpeg cannot take")
    elif kind in ("alsa", "pulse"):
        if not _ALSA_PULSE.fullmatch(device):
            raise AudioInputError(f"{kind} device may use letters, digits and _ . : , = @ + - only, and cannot start with '-'")
    elif not _AVF.fullmatch(device):
        raise AudioInputError("avfoundation device must be an index or a plain device name")
    return AudioInput(kind, device)


# --- what the host can offer -----------------------------------------------------------------------------------
def parse_arecord_list(text: str) -> List[Tuple[str, str]]:
    """``arecord -l`` -> [(``alsa:hw:CARD,DEV``, label)]."""
    found = []
    for match in re.finditer(r"^card (\d+): ([^\[]*)\[([^\]]*)\], device (\d+): ([^\[]*)\[([^\]]*)\]", text, re.M):
        card, _, card_name, dev, _, dev_name = match.groups()
        found.append((f"alsa:hw:{card},{dev}", f"{card_name.strip()} - {dev_name.strip()}"))
    return found


def parse_pactl_sources(text: str) -> List[Tuple[str, str]]:
    """``pactl list short sources`` -> [(``pulse:name``, name)]; monitors of outputs are not microphones and are skipped."""
    found = []
    for line in text.splitlines():
        cols = line.split("\t")
        if len(cols) >= 2 and not cols[1].endswith(".monitor"):
            found.append((f"pulse:{cols[1]}", cols[1]))
    return found


def _run(cmd: List[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def list_audio_inputs() -> List[dict]:
    """Audio inputs this host can name to FFmpeg. Empty where the OS tools are absent (an honest answer, not a guess)."""
    items: List[dict] = []
    if shutil.which("pactl"):
        items += [{"input": i, "label": label, "backend": "pulse"} for i, label in parse_pactl_sources(_run(["pactl", "list", "short", "sources"]))]
    if shutil.which("arecord"):
        items += [{"input": i, "label": label, "backend": "alsa"} for i, label in parse_arecord_list(_run(["arecord", "-l"]))]
    try:
        from modules.segmented_capture import list_dshow_audio_devices
        items += [{"input": f"dshow:{name}", "label": name, "backend": "dshow"} for name in list_dshow_audio_devices()]
    except Exception:  # not Windows, or FFmpeg cannot list DirectShow here
        pass
    return items
