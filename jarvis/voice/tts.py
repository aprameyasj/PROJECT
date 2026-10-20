"""Text → speech.

Three delivery paths, and the dashboard uses whichever is available:

* `browser` — the React client speaks with `speechSynthesis` (zero setup, works
  everywhere, and is what the live preview uses).
* `system`  — `say` (macOS), PowerShell `System.Speech` (Windows) or
  `espeak-ng`/`spd-say` (Linux) so a headless JARVIS can still talk.
* `openai`  — server-side neural TTS written to an mp3 the client plays.

`speak()` is fire-and-forget and never raises: a missing speaker must not stop
an action from completing.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class SpeechResult:
    text: str
    provider: str
    audio_path: str = ""
    ok: bool = True
    duration_ms: int = 0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text, "provider": self.provider, "audio_path": self.audio_path,
                "ok": self.ok, "duration_ms": self.duration_ms, "error": self.error}


class TextToSpeech:
    def __init__(self, settings: Any) -> None:
        self.settings = settings
        self.provider = self._select_provider()
        self.last_error: str | None = None
        self.spoken: list[str] = []

    def _select_provider(self) -> str:
        requested = str(getattr(self.settings, "tts_provider", "auto")).lower()
        if requested in {"off", "none", "browser"}:
            return requested
        if requested == "openai" and getattr(self.settings, "llm_available", False):
            return "openai"
        if requested == "system" or requested == "auto":
            if _system_speaker():
                return "system"
            if getattr(self.settings, "llm_available", False):
                return "openai"
        return "browser"

    def status(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "configured": str(getattr(self.settings, "tts_provider", "auto")),
            "available": self.provider in {"system", "openai"},
            "browser_supported": True,
            "voice": getattr(self.settings, "tts_voice", ""),
            "last_error": self.last_error,
        }

    # ------------------------------------------------------------------
    async def speak(self, text: str, *, session_id: str = "", block: bool = False) -> SpeechResult:
        text = (text or "").strip()
        if not text:
            return SpeechResult("", self.provider, ok=False, error="nothing to say")
        self.spoken.append(text)
        started = time.perf_counter()

        if self.provider == "browser" or self.provider == "off":
            # the client speaks it; nothing to do here
            return SpeechResult(text, self.provider, ok=True, duration_ms=_ms(started))

        try:
            if self.provider == "openai":
                path = await self.synthesise(text)
                if path and block:
                    _play(path)
                return SpeechResult(text, self.provider, audio_path=path or "", ok=bool(path),
                                    duration_ms=_ms(started), error=None if path else self.last_error)
            if block:
                await asyncio.get_running_loop().run_in_executor(None, _say_system, text)
            else:
                _say_system_async(text)
            return SpeechResult(text, "system", ok=True, duration_ms=_ms(started))
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"{type(exc).__name__}: {exc}"
            return SpeechResult(text, self.provider, ok=False, error=self.last_error, duration_ms=_ms(started))

    # ------------------------------------------------------------------
    async def synthesise(self, text: str, filename: str | None = None) -> str | None:
        """Neural TTS via the OpenAI-compatible API -> mp3 path."""
        import httpx

        if not getattr(self.settings, "llm_available", False):
            self.last_error = "no API key for neural TTS"
            return None
        directory = Path(self.settings.audio_dir)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / (filename or f"speech-{int(time.time() * 1000)}.mp3")
        payload = {
            "model": getattr(self.settings, "tts_model", "gpt-4o-mini-tts"),
            "voice": getattr(self.settings, "tts_voice", "onyx"),
            "input": text[:4000],
            "response_format": "mp3",
        }
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.post(
                    f"{str(self.settings.llm_base_url).rstrip('/')}/audio/speech",
                    headers={"Authorization": f"Bearer {self.settings.llm_api_key}"},
                    json=payload,
                )
                response.raise_for_status()
                path.write_bytes(response.content)
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"{type(exc).__name__}: {exc}"
            return None
        self.last_error = None
        return str(path)


# ---------------------------------------------------------------------------
# system speakers
# ---------------------------------------------------------------------------


def _system_speaker() -> str | None:
    import platform

    system = platform.system()
    if system == "Darwin" and shutil.which("say"):
        return "say"
    if system == "Windows":
        return "powershell"
    for candidate in ("espeak-ng", "espeak", "spd-say", "festival", "flite"):
        if shutil.which(candidate):
            return candidate
    return None


def _say_system(text: str) -> None:
    speaker = _system_speaker()
    if not speaker:
        return
    try:
        if speaker == "say":
            subprocess.run(["say", text], check=False, timeout=60)
        elif speaker == "powershell":
            escaped = text.replace("'", "''")
            script = (
                "Add-Type -AssemblyName System.Speech;"
                "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
                f"$s.Speak('{escaped}')"
            )
            subprocess.run(["powershell", "-NoProfile", "-Command", script], check=False, timeout=60,
                           stdout=subprocess.DEVNULL)
        elif speaker == "spd-say":
            subprocess.run(["spd-say", text], check=False, timeout=30)
        else:
            subprocess.run([speaker, text], check=False, timeout=60, stdout=subprocess.DEVNULL)
    except Exception:  # noqa: BLE001
        pass


def _say_system_async(text: str) -> None:
    try:
        subprocess.Popen(
            _speaker_command(text), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
    except Exception:  # noqa: BLE001
        pass


def _speaker_command(text: str) -> list[str]:
    speaker = _system_speaker()
    if speaker == "say":
        return ["say", text]
    if speaker == "spd-say":
        return ["spd-say", text]
    if speaker == "powershell":
        escaped = text.replace("'", "''")
        return ["powershell", "-NoProfile", "-Command",
                "Add-Type -AssemblyName System.Speech;"
                "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
                f"$s.Speak('{escaped}')"]
    return [speaker or "espeak-ng", text]


def _play(path: str) -> None:
    player = shutil.which("afplay") or shutil.which("mpv") or shutil.which("ffplay") or shutil.which("aplay")
    if not player:
        return
    args = [player, path] if player != "ffplay" else [player, "-nodisp", "-autoexit", path]
    try:
        subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:  # noqa: BLE001
        pass


def _ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


__all__ = ["TextToSpeech", "SpeechResult"]
