"""Speech → text.

Providers, tried in order depending on `JARVIS_STT` (default `auto`):

* `browser`       — the React dashboard captures audio with the Web Speech API
                    and sends text; nothing to do server-side.
* `openai`        — Whisper via the OpenAI-compatible `/audio/transcriptions`.
* `faster_whisper`— fully local, offline transcription (pip install faster-whisper).
* `vosk`          — tiny local model, good for a wake word loop.

Every provider returns the same `Transcript`, and `SpeechToText.status()` tells
the dashboard exactly what is available so the UI can adapt.
"""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class Transcript:
    text: str
    provider: str = "unknown"
    language: str = "en"
    duration_ms: int = 0
    confidence: float = 0.0
    audio_path: str = ""
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text, "provider": self.provider, "language": self.language,
            "duration_ms": self.duration_ms, "confidence": self.confidence,
            "audio_path": self.audio_path, "error": self.error,
        }


class SpeechToText:
    def __init__(self, settings: Any) -> None:
        self.settings = settings
        self.provider = self._select_provider()
        self._model: Any = None
        self.last_error: str | None = None

    # ------------------------------------------------------------------
    def _select_provider(self) -> str:
        requested = str(getattr(self.settings, "stt_provider", "auto")).lower()
        if requested in {"browser", "off", "none"}:
            return requested
        if requested == "openai" or (requested == "auto" and getattr(self.settings, "llm_available", False)):
            return "openai"
        if requested in {"faster_whisper", "faster-whisper"} or (requested == "auto" and _has("faster_whisper")):
            if _has("faster_whisper"):
                return "faster_whisper"
        if requested == "vosk" or (requested == "auto" and _has("vosk")):
            if _has("vosk"):
                return "vosk"
        return "browser"

    def status(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "configured": str(getattr(self.settings, "stt_provider", "auto")),
            "available": self.provider in {"openai", "faster_whisper", "vosk"},
            "microphone": _microphone_present(),
            "browser_supported": True,
            "last_error": self.last_error,
            "model": getattr(self.settings, "stt_model", "") if self.provider == "openai" else self.provider,
        }

    # ------------------------------------------------------------------
    async def transcribe_file(self, path: str | Path) -> Transcript:
        return await self.transcribe_bytes(Path(path).read_bytes(), filename=Path(path).name,
                                           audio_path=str(path))

    async def transcribe_bytes(self, data: bytes, *, filename: str = "audio.webm",
                               audio_path: str = "") -> Transcript:
        started = time.perf_counter()
        if not data:
            return Transcript("", self.provider, error="empty audio", audio_path=audio_path)
        try:
            if self.provider == "openai":
                from ..llm.openai_provider import OpenAIBrain

                brain = OpenAIBrain(self.settings)
                text = await brain.transcribe(data, filename=filename)
            elif self.provider == "faster_whisper":
                text = self._faster_whisper(data, filename)
            elif self.provider == "vosk":
                text = self._vosk(data)
            else:
                return Transcript("", "browser", error="No server-side STT provider; the browser should transcribe.",
                                  audio_path=audio_path)
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"{type(exc).__name__}: {exc}"
            return Transcript("", self.provider, error=self.last_error, audio_path=audio_path,
                              duration_ms=_ms(started))

        self.last_error = None
        return Transcript(text.strip(), self.provider, duration_ms=_ms(started), audio_path=audio_path)

    # ------------------------------------------------------------------
    def _faster_whisper(self, data: bytes, filename: str) -> str:
        import io
        import tempfile

        from faster_whisper import WhisperModel  # type: ignore

        if self._model is None:
            size = "base.en" if filename.endswith((".wav", ".webm")) else "base.en"
            self._model = WhisperModel(size, device="cpu", compute_type="int8")
        with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as handle:
            handle.write(data)
            tmp = handle.name
        segments, _info = self._model.transcribe(tmp, beam_size=1, vad_filter=True)
        text = " ".join(segment.text.strip() for segment in segments)
        Path(tmp).unlink(missing_ok=True)
        _ = io
        return text

    def _vosk(self, data: bytes) -> str:
        import json
        import wave

        from vosk import KaldiRecognizer, Model, SetLogLevel  # type: ignore

        SetLogLevel(-1)
        model_path = getattr(self.settings, "vosk_model_path", "")
        if self._model is None:
            if not model_path or not Path(model_path).exists():
                raise RuntimeError("Set JARVIS_VOSK_MODEL to a downloaded Vosk model directory.")
            self._model = Model(model_path)

        with wave.open(_bytes_to_wav_file(data)) as wav:
            recognizer = KaldiRecognizer(self._model, wav.getframerate())
            text_parts: list[str] = []
            while True:
                frames = wav.readframes(4000)
                if not frames:
                    break
                if recognizer.AcceptWaveform(frames):
                    text_parts.append(json.loads(recognizer.Result()).get("text", ""))
            text_parts.append(json.loads(recognizer.FinalResult()).get("text", ""))
        return " ".join(part for part in text_parts if part)


def _bytes_to_wav_file(data: bytes) -> str:
    import tempfile

    handle = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    handle.write(data)
    handle.close()
    return handle.name


def _has(module: str) -> bool:
    import importlib.util

    return importlib.util.find_spec(module) is not None


def _microphone_present() -> bool:
    """Cheap check: is there any audio capture device / helper binary?"""
    if shutil.which("arecord") or shutil.which("ffmpeg"):
        return True
    try:
        import sounddevice  # type: ignore

        return any(d.get("max_input_channels", 0) > 0 for d in sounddevice.query_devices())
    except Exception:  # noqa: BLE001
        return False


def _ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


__all__ = ["SpeechToText", "Transcript"]
