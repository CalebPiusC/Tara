"""Python side of the in-page audio bridge (bridge.js).

Incoming meeting audio arrives as 16 kHz mono PCM16 through an exposed function; Tara's speech goes the other way with
`play`, and `stop` cancels what is queued (barge-in). Nothing is written to disk and nothing is kept: audio is passed on and
forgotten. Any page-like object with `evaluate` works, so the bridge can be tested without a real browser.
"""
from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Callable
from importlib.resources import files
from pathlib import Path
from typing import Any

IN_RATE = 16000
MAX_PLAY_BYTES = 480_000          # about 10 s at 24 kHz; callers send chunks far smaller than this


def make_picture(image: Path, caption: str, size: tuple[int, int] = (640, 360)) -> str:
    """Tara's picture with the caption written on it, as a small JPEG data URL (shown as the camera). Needs Pillow."""
    import io

    from PIL import Image, ImageDraw, ImageFont
    w, h = size
    frame = Image.new("RGB", size, (16, 18, 24))
    with Image.open(image) as opened:
        src = opened.convert("RGB")
        bar = int(h * 0.2)
        src.thumbnail((w, h - bar))
        frame.paste(src, ((w - src.width) // 2, (h - bar - src.height) // 2))
    d = ImageDraw.Draw(frame)
    fsize = max(12, h // 17)
    font = ImageFont.load_default(size=fsize)
    lines, line = [], ""
    for word in caption.split():                                        # wrap the caption to the frame width
        trial = f"{line} {word}".strip()
        if d.textlength(trial, font=font) > w - 24 and line:
            lines.append(line)
            line = word
        else:
            line = trial
    lines.append(line)
    y: float = h - int(h * 0.2) + 6
    for ln in lines[:2]:
        d.text(((w - d.textlength(ln, font=font)) // 2, y), ln, fill=(255, 255, 255), font=font)
        y += fsize + 4
    out = io.BytesIO()
    frame.save(out, "JPEG", quality=80)
    return "data:image/jpeg;base64," + base64.b64encode(out.getvalue()).decode("ascii")


def bridge_js(mode: str = "page", devices: dict[str, str] | None = None, picture: str = "") -> str:
    """The bridge script. `mode="devices"` points the page at two virtual audio cables (names given in `devices`: mic, speaker)."""
    body = files("tara.meet").joinpath("bridge.js").read_text(encoding="utf-8")
    if picture:
        body = f"window.__taraPicture = {json.dumps(picture)};\n" + body
    if mode != "devices":
        return body
    cfg = json.dumps({"mic": (devices or {}).get("mic", ""), "speaker": (devices or {}).get("speaker", "")})
    return f'window.__taraMode = "devices"; window.__taraDevices = {cfg};\n' + body


class AudioBridge:
    def __init__(self, on_audio: Callable[[bytes], None], mode: str = "page", devices: dict[str, str] | None = None,
                 picture: str = ""):
        self._on_audio = on_audio
        self.picture = picture
        self.mode, self.devices = mode, devices
        self.frames_in = 0
        self.bytes_in = 0
        self.bad_frames = 0

    async def attach(self, context: Any) -> None:
        """Install on a browser context BEFORE any page loads: the init script wraps WebRTC and getUserMedia first."""
        await context.expose_function("__taraAudioIn", self._receive)
        await context.add_init_script(script=bridge_js(self.mode, self.devices, self.picture))

    def _receive(self, data: str) -> None:
        try:
            pcm = base64.b64decode(data, validate=True)
        except (binascii.Error, ValueError, TypeError):
            self.bad_frames += 1
            return
        if not pcm or len(pcm) % 2:
            self.bad_frames += 1
            return
        self.frames_in += 1
        self.bytes_in += len(pcm)
        self._on_audio(pcm)

    @staticmethod
    async def play(page: Any, pcm: bytes, sample_rate: int) -> int:
        """Queue PCM16 mono for the microphone. Returns how many milliseconds are queued after this chunk."""
        if not pcm:
            return 0
        if len(pcm) % 2 or len(pcm) > MAX_PLAY_BYTES or sample_rate < 8000 or sample_rate > 96000:
            raise ValueError("audio chunk is not valid PCM16 mono")
        return int(await page.evaluate("([d, r]) => window.__taraPlay(d, r)", [base64.b64encode(pcm).decode("ascii"), sample_rate]))

    @staticmethod
    async def stop(page: Any) -> int:
        """Cancel everything queued. Returns the milliseconds that were actually played."""
        return int(await page.evaluate("() => window.__taraStop()"))

    @staticmethod
    async def stats(page: Any) -> dict[str, Any]:
        """WebRTC's own view of the meeting audio (packets, energy, level), independent of the Web Audio tap."""
        out = await page.evaluate("() => (window.__taraStats ? window.__taraStats() : null)")
        return dict(out) if out else {}

    @staticmethod
    async def debug(page: Any) -> dict[str, Any]:
        """What Meet did with its audio (new events since the last call) and WebRTC's per-stream counters, for the run log."""
        out = await page.evaluate("() => (window.__taraDebug ? window.__taraDebug() : null)")
        return dict(out) if out else {}

    @staticmethod
    async def status(page: Any) -> dict[str, Any]:
        out = await page.evaluate("() => (window.__taraStatus ? window.__taraStatus() : null)")
        return dict(out) if out else {"ready": False, "state": "missing"}
