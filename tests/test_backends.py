"""Action backends: the simulated desktop must behave like a real one."""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.backends.apps import canonicalise, launch_command
from jarvis.backends.sandbox import SandboxBackend


@pytest.fixture()
def backend(settings) -> SandboxBackend:
    return SandboxBackend(settings)


def test_hotkeys_model_a_real_browser(backend: SandboxBackend) -> None:
    backend.state["tabs"] = ["GitHub", "Docs", "Mail"]
    backend.state["active_tab"] = 1

    assert "Docs" in backend.hotkey("ctrl", "w")
    assert backend.state["tabs"] == ["GitHub", "Mail"]

    backend.hotkey("ctrl", "t")
    assert backend.state["tabs"][-1] == "New Tab"

    backend.hotkey("ctrl", "shift", "t")
    assert len(backend.state["tabs"]) == 4


def test_launch_and_terminate_track_state(backend: SandboxBackend) -> None:
    info = backend.launch_app("VS Code")
    assert info["application"] == "vscode"
    assert "vscode" in backend.state["running_apps"]
    assert backend.focus_window("vscode").lower().startswith("focused")

    closed = backend.terminate_app("vscode")
    assert closed["closed_windows"] == 1
    assert "vscode" not in backend.state["running_apps"]


def test_open_url_adds_a_tab_in_the_right_browser(backend: SandboxBackend) -> None:
    info = backend.open_url("http://localhost:3000", "chrome")
    assert info["url"] == "http://localhost:3000"
    assert any("localhost:3000" in str(tab) for tab in backend.state["tabs"])


def test_mouse_keyboard_and_clipboard(backend: SandboxBackend) -> None:
    assert "100" in backend.move_mouse(100, 200)
    assert backend.mouse_position() == (100, 200)
    assert "clicked" in backend.click(x=10, y=20, clicks=2).lower()
    assert "5 characters" in backend.type_text("hello")
    backend.clipboard_set("copy me")
    assert backend.clipboard_get() == "copy me"


def test_screenshot_writes_a_real_image(backend: SandboxBackend, tmp_path: Path) -> None:
    info = backend.screenshot(str(tmp_path / "shot.png"))
    assert info["simulated"] is True
    assert Path(info["path"]).exists()
    assert Path(info["path"]).stat().st_size > 100


def test_volume_brightness_and_power(backend: SandboxBackend) -> None:
    assert "30%" in backend.set_volume(30)
    assert backend.state["volume"] == 30
    assert backend.set_volume(150).endswith("100%.")
    assert "70%" in backend.set_brightness(70)
    assert "shutdown" in backend.power("shutdown").lower()
    assert backend.state["power"] == "shutdown"


def test_system_status_is_complete(backend: SandboxBackend) -> None:
    status = backend.system_status()
    for key in ("cpu_percent", "memory_percent", "battery_percent", "running_apps", "open_tabs", "time"):
        assert key in status


def test_shell_is_simulated_except_for_harmless_probes(backend: SandboxBackend) -> None:
    simulated = backend.shell("npm run dev")
    assert simulated["simulated"] is True
    real = backend.shell("echo jarvis")
    assert real["simulated"] is False
    assert "jarvis" in real["stdout"]


def test_action_log_records_everything(backend: SandboxBackend) -> None:
    backend.launch_app("chrome")
    backend.hotkey("ctrl", "w")
    backend.screenshot()
    actions = [entry["action"] for entry in backend.action_log]
    assert "launch_app" in actions and "hotkey" in actions and "screenshot" in actions


def test_backend_factory_falls_back_without_a_display(settings) -> None:
    from jarvis.backends.base import get_backend

    settings.backend = "auto"
    settings.dry_run = False
    chosen = get_backend(settings)
    assert chosen.name in {"sandbox", "desktop"}
    assert chosen.describe()["name"] == chosen.name


def test_desktop_backend_reports_unavailable_headlessly(settings) -> None:
    from jarvis.backends.desktop import DesktopBackend

    desktop = DesktopBackend(settings)
    if not desktop.available:
        assert "DISPLAY" in desktop.unavailable_reason or "headless" in desktop.unavailable_reason


@pytest.mark.parametrize(
    "spoken,expected",
    [
        ("VS Code", "vscode"),
        ("vs code", "vscode"),
        ("Google Chrome", "chrome"),
        ("the browser", "chrome"),
        ("Command Prompt", "terminal"),
        ("File Explorer", "file_manager"),
        ("Calculator", "calculator"),
        ("Android Studio", "android_studio"),
    ],
)
def test_application_aliases(spoken: str, expected: str) -> None:
    assert canonicalise(spoken) == expected


def test_launch_command_resolves_per_platform() -> None:
    command, canonical = launch_command("vscode")
    assert canonical == "vscode"
    assert command  # some command is always produced, even if not installed


def test_known_applications_are_documented() -> None:
    from jarvis.backends.apps import APPLICATIONS, describe_applications

    assert "chrome" in APPLICATIONS
    assert all(entry["id"] for entry in describe_applications())
