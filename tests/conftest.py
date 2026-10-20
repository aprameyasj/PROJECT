"""Shared fixtures: an isolated JARVIS home per test, sandbox backend, no network."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, Iterator

import pytest


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Every test gets its own database, screenshot folder and settings object."""
    home = tmp_path / "jarvis-home"
    monkeypatch.setenv("JARVIS_HOME", str(home))
    monkeypatch.setenv("JARVIS_BACKEND", "sandbox")
    monkeypatch.setenv("JARVIS_LLM_PROVIDER", "offline")
    monkeypatch.setenv("JARVIS_SCHEDULER", "1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("JARVIS_LLM_API_KEY", raising=False)

    from jarvis.config.settings import reload_settings

    settings = reload_settings()
    settings.ensure_dirs()
    yield home
    settings.db_path.unlink(missing_ok=True)


@pytest.fixture()
def settings() -> Any:
    from jarvis.config.settings import get_settings

    return get_settings()


@pytest.fixture()
def agent(settings: Any) -> Iterator[Any]:
    from jarvis.core.agent import Jarvis

    jarvis = Jarvis(settings=settings)
    try:
        yield jarvis
    finally:
        jarvis.close()


@pytest.fixture()
def event_loop_policy() -> Any:
    return asyncio.DefaultEventLoopPolicy()


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Tests must never hit the internet — the search tools degrade instead."""
    import httpx

    def blocked(*_args: Any, **_kwargs: Any) -> Any:
        raise httpx.ConnectError("network access is disabled in tests")

    monkeypatch.setattr(httpx.AsyncClient, "get", blocked)
    monkeypatch.setattr(httpx.AsyncClient, "post", blocked)
    yield
