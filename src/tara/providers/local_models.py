"""Adapters for local models. UNTESTED: no models are downloaded in this phase.
Heavy imports are lazy so importing tara never needs them. Phase 2 will test
and benchmark each one (docs/LOCAL_DEVELOPMENT.md)."""
from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator

from ..contracts import AudioSegment, PCMChunk, Transcript
from ..errors import ProviderError


def _need(module: str, hint: str) -> Any:
    try:
        return __import__(module)
    except ImportError as e:
        raise ProviderError(f"{module} not installed. {hint}") from e


class MlxWhisperRecognizer:
    """Segment-based STT via mlx-whisper (S10). Model repo from config."""

    def __init__(self, model_repo: str):
        if not model_repo:
            raise ValueError("TARA_STT_MODEL is empty")
        self.model_repo = model_repo

    def _run(self, seg: AudioSegment) -> dict[str, Any]:
        mlx_whisper = _need("mlx_whisper", "Install in Phase 2: pip install mlx-whisper (needs ffmpeg).")
        np = _need("numpy", "numpy is a dependency of mlx-whisper.")
        if seg.sample_rate != 16000:
            raise ProviderError("STT expects 16 kHz audio")
        audio = np.frombuffer(seg.pcm, dtype=np.int16).astype(np.float32) / 32768.0
        return mlx_whisper.transcribe(audio, path_or_hf_repo=self.model_repo)

    async def transcribe(self, seg: AudioSegment) -> Transcript:
        out = await asyncio.to_thread(self._run, seg)
        return Transcript(str(out.get("text", "")).strip(), seg.start_ms, seg.end_ms, None, out.get("language"))


class KokoroSynthesizer:
    """Sentence-segment TTS via Kokoro (S8). 24 kHz output. Cancellable between segments."""

    sample_rate = 24000

    def __init__(self, voice: str, lang_code: str = "a"):
        if not voice:
            raise ValueError("TARA_TTS_VOICE is empty")
        self.voice, self.lang_code = voice, lang_code
        self._pipe: Any = None

    def _pipeline(self) -> Any:
        if self._pipe is None:
            kokoro = _need("kokoro", "Install in Phase 2: pip install kokoro; brew install espeak-ng.")
            self._pipe = kokoro.KPipeline(lang_code=self.lang_code)
        return self._pipe

    def synthesize(self, text: str) -> AsyncIterator[PCMChunk]:
        async def gen() -> AsyncIterator[PCMChunk]:
            gen_it = await asyncio.to_thread(lambda: iter(self._pipeline()(text, voice=self.voice, split_pattern=r"(?<=[.!?])\s+")))
            t = 0
            while True:
                item = await asyncio.to_thread(next, gen_it, None)
                if item is None:
                    return
                audio = item[2]
                pcm = (audio.numpy() * 32767).astype("int16").tobytes() if hasattr(audio, "numpy") else bytes(audio)
                chunk = PCMChunk(pcm, self.sample_rate, t, "tara")
                t += chunk.duration_ms
                yield chunk
        return gen()


class SileroFrameVad:
    """Frame VAD via the silero-vad package. Frame size 512 @16 kHz is the
    package's documented requirement as remembered: UNVERIFIED, check in Phase 2."""

    sample_rate = 16000
    frame_ms = 32

    def __init__(self, threshold: float = 0.5):
        self.threshold = threshold
        self._model: Any = None

    def is_speech(self, frame: bytes) -> bool:
        if self._model is None:
            sv = _need("silero_vad", "Install in Phase 2: pip install silero-vad.")
            self._torch = _need("torch", "torch is a dependency of silero-vad.")
            self._model = sv.load_silero_vad()
        t = self._torch
        x = t.frombuffer(bytearray(frame), dtype=t.int16).float() / 32768.0
        return float(self._model(x, self.sample_rate).item()) >= self.threshold
