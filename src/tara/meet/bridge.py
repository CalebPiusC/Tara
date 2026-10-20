"""Python side of the in-page audio bridge (bridge.js).

Incoming meeting audio arrives as 16 kHz mono PCM16 through an exposed function; Tara's speech goes the other way with
`play`, and `stop` cancels what is queued (barge-in). Nothing is written to disk and nothing is kept: audio is passed on and
forgotten. Any page-like object with `evaluate` works, so the bridge can be tested without a real browser.
"""
from __future__ import annotations

import base64
import binascii
from collections.abc import Callable
from importlib.resources import files
from typing import Any

IN_RATE = 16000
MAX_PLAY_BYTES = 480_000          # about 10 s at 24 kHz; callers send chunks far smaller than this


def bridge_js() -> str:
    return files("tara.meet").joinpath("bridge.js").read_text(encoding="utf-8")


class AudioBridge:
    def __init__(self, on_audio: Callable[[bytes], None]):
        self._on_audio = on_audio
        self.frames_in = 0
        self.bytes_in = 0
        self.bad_frames = 0

    async def attach(self, context: Any) -> None:
        """Install on a browser context BEFORE any page loads: the init script wraps WebRTC and getUserMedia first."""
        await context.expose_function("__taraAudioIn", self._receive)
        await context.add_init_script(script=bridge_js())

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
    async def status(page: Any) -> dict[str, Any]:
        out = await page.evaluate("() => (window.__taraStatus ? window.__taraStatus() : null)")
        return dict(out) if out else {"ready": False, "state": "missing"}
