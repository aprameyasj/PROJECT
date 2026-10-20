"""JARVIS configuration.

Everything is driven by environment variables (or a local `.env` file) so the
agent can run on a laptop, in a container, or in CI without code changes.

Run `python main.py doctor` to see the resolved configuration.
"""

from __future__ import annotations

import os
import platform
import sys
from functools import lru_cache
from pathlib import Path
from typing import Literal

try:  # python-dotenv is optional but recommended
    from dotenv import load_dotenv

    _DOTENV_AVAILABLE = True
except Exception:  # pragma: no cover - dotenv missing
    _DOTENV_AVAILABLE = False

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Where JARVIS keeps its brain: SQLite DB, screenshots, logs.
#: Override with JARVIS_HOME (useful for tests / multiple profiles).
DEFAULT_HOME = Path(os.environ.get("JARVIS_HOME", REPO_ROOT / ".jarvis"))


def _bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Settings container
# ---------------------------------------------------------------------------


class Settings:
    """Plain-Python settings object (no pydantic dependency required)."""

    def __init__(self) -> None:
        if _DOTENV_AVAILABLE:
            load_dotenv(REPO_ROOT / ".env", override=False)

        # --- identity -----------------------------------------------------
        self.agent_name: str = os.environ.get("JARVIS_NAME", "JARVIS")
        self.wake_words: list[str] = [
            w.strip().lower()
            for w in os.environ.get("JARVIS_WAKE_WORDS", "jarvis, hey jarvis, hi jarvis").split(",")
            if w.strip()
        ]
        self.user_name: str = os.environ.get("JARVIS_USER_NAME", "")

        # --- storage ------------------------------------------------------
        self.home: Path = Path(os.environ.get("JARVIS_HOME", DEFAULT_HOME))
        self.data_dir: Path = self.home / "data"
        self.screenshots_dir: Path = self.home / "screenshots"
        self.logs_dir: Path = self.home / "logs"
        self.audio_dir: Path = self.home / "audio"
        self.db_path: Path = self.home / "jarvis.db"

        # --- LLM ----------------------------------------------------------
        self.llm_provider: Literal["openai", "offline"] = os.environ.get(  # type: ignore[assignment]
            "JARVIS_LLM_PROVIDER", "auto"
        ).lower()  # resolved in `_resolve_llm`
        self.llm_api_key: str = os.environ.get("OPENAI_API_KEY", "") or os.environ.get(
            "JARVIS_LLM_API_KEY", ""
        )
        self.llm_base_url: str = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
        self.llm_model: str = os.environ.get("JARVIS_LLM_MODEL", "gpt-4o-mini")
        self.llm_temperature: float = _float("JARVIS_LLM_TEMPERATURE", 0.2)
        self.llm_timeout: float = _float("JARVIS_LLM_TIMEOUT", 45.0)
        self.max_steps: int = _int("JARVIS_MAX_STEPS", 8)

        # --- action backend ------------------------------------------------
        #: "auto" | "desktop" | "sandbox"
        self.backend: str = os.environ.get("JARVIS_BACKEND", "auto").lower()
        #: Dry-run: never touch the real machine, even if a desktop is detected.
        self.dry_run: bool = _bool("JARVIS_DRY_RUN", False)
        self.screenshot_backend: str = os.environ.get("JARVIS_SCREENSHOT_BACKEND", "auto").lower()

        # --- safety --------------------------------------------------------
        #: "strict" (confirm anything above level 1) | "normal" | "yolo" (never confirm)
        self.safety_mode: str = os.environ.get("JARVIS_SAFETY", "normal").lower()
        self.require_confirmation_for_shell: bool = _bool("JARVIS_CONFIRM_SHELL", True)
        self.max_shell_seconds: int = _int("JARVIS_MAX_SHELL_SECONDS", 60)
        self.confirmation_ttl_seconds: int = _int("JARVIS_CONFIRM_TTL", 300)
        self.audit_everything: bool = _bool("JARVIS_AUDIT", True)

        # --- voice ---------------------------------------------------------
        #: "auto" | "openai" | "faster_whisper" | "browser" | "off"
        self.stt_provider: str = os.environ.get("JARVIS_STT", "auto").lower()
        self.stt_model: str = os.environ.get("JARVIS_STT_MODEL", "whisper-1")
        #: "auto" | "openai" | "system" | "browser" | "off"
        self.tts_provider: str = os.environ.get("JARVIS_TTS", "auto").lower()
        self.tts_voice: str = os.environ.get("JARVIS_TTS_VOICE", "onyx")
        self.tts_model: str = os.environ.get("JARVIS_TTS_MODEL", "gpt-4o-mini-tts")
        self.speak_responses: bool = _bool("JARVIS_SPEAK", True)
        self.listen_on_start: bool = _bool("JARVIS_LISTEN", False)

        # --- web tools -----------------------------------------------------
        self.search_provider: str = os.environ.get("JARVIS_SEARCH_PROVIDER", "duckduckgo").lower()
        self.search_api_key: str = os.environ.get("SERPER_API_KEY", "") or os.environ.get(
            "BRAVE_SEARCH_API_KEY", ""
        )
        self.search_max_results: int = _int("JARVIS_SEARCH_RESULTS", 5)
        self.web_timeout: float = _float("JARVIS_WEB_TIMEOUT", 12.0)
        self.playwright_headless: bool = _bool("JARVIS_BROWSER_HEADLESS", True)

        # --- API server ----------------------------------------------------
        self.api_host: str = os.environ.get("JARVIS_API_HOST", "0.0.0.0")
        self.api_port: int = _int("JARVIS_API_PORT", 8000)
        self.api_token: str = os.environ.get("JARVIS_API_TOKEN", "")
        self.cors_origins: list[str] = [
            o.strip() for o in os.environ.get("JARVIS_CORS_ORIGINS", "*").split(",") if o.strip()
        ]

        # --- scheduler -----------------------------------------------------
        self.timezone: str = os.environ.get("JARVIS_TIMEZONE", "") or _detect_timezone()
        self.scheduler_enabled: bool = _bool("JARVIS_SCHEDULER", True)

        self._resolve_llm()
        self._resolve_backend()

    # ------------------------------------------------------------------
    # resolution helpers
    # ------------------------------------------------------------------
    def _resolve_llm(self) -> None:
        if self.llm_provider not in {"openai", "offline"}:
            # "auto": use the API when a key exists, otherwise fall back to the
            # built-in offline intent engine so JARVIS always works.
            self.llm_provider = "openai" if self.llm_api_key else "offline"
        self.llm_available = self.llm_provider == "openai" and bool(self.llm_api_key)

    def _resolve_backend(self) -> None:
        requested = self.backend
        if requested == "auto":
            requested = "desktop" if _has_desktop() else "sandbox"
        if self.dry_run:
            requested = "sandbox"
        self.backend = requested
        self.is_sandboxed = requested != "desktop"

    # ------------------------------------------------------------------
    def ensure_dirs(self) -> None:
        for path in (self.home, self.data_dir, self.screenshots_dir, self.logs_dir, self.audio_dir):
            path.mkdir(parents=True, exist_ok=True)

    @property
    def platform(self) -> str:
        return platform.system().lower()  # windows / darwin / linux

    def describe(self) -> dict:
        """Serialisable snapshot used by the dashboard and `doctor`."""
        return {
            "agent_name": self.agent_name,
            "user_name": self.user_name,
            "platform": self.platform,
            "python": sys.version.split()[0],
            "home": str(self.home),
            "db_path": str(self.db_path),
            "llm_provider": self.llm_provider,
            "llm_model": self.llm_model if self.llm_available else None,
            "llm_available": self.llm_available,
            "backend": self.backend,
            "dry_run": self.dry_run,
            "safety_mode": self.safety_mode,
            "max_steps": self.max_steps,
            "stt_provider": self.stt_provider,
            "tts_provider": self.tts_provider,
            "timezone": self.timezone,
            "scheduler_enabled": self.scheduler_enabled,
            "api": {"host": self.api_host, "port": self.api_port, "token_required": bool(self.api_token)},
            "wake_words": self.wake_words,
        }


def _has_desktop() -> bool:
    """Heuristic: can we realistically drive a GUI on this machine?"""
    system = platform.system()
    if system in {"Windows", "Darwin"}:
        return True
    if system == "Linux":
        if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
            return True
    return False


def _detect_timezone() -> str:
    try:
        from zoneinfo import ZoneInfo  # noqa: F401
        import time

        return time.tzname[0] or "UTC"
    except Exception:
        return "UTC"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings singleton. Call `reload_settings()` after env changes."""
    settings = Settings()
    settings.ensure_dirs()
    return settings


def reload_settings() -> Settings:
    get_settings.cache_clear()
    return get_settings()
