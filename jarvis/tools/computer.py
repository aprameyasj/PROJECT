"""Computer-control tools: keyboard, mouse, screen, windows, apps, audio.

Every function here is a *tool*: the model chooses one and supplies typed
arguments; the safety gate decides whether it may run; the action backend
performs it (for real on a desktop, or simulated in a sandbox).
"""

from __future__ import annotations

import time
from typing import Literal

from ..backends.apps import canonicalise, describe_applications, known_applications
from ..core.types import Permission, ToolCategory, ToolResult
from .registry import ToolContext, tool

# ---------------------------------------------------------------------------
# applications
# ---------------------------------------------------------------------------


@tool(
    permission=Permission.SAFE,
    category=ToolCategory.COMPUTER,
    examples=["Jarvis, open VS Code.", "Open Chrome", "Launch the terminal"],
)
def open_application(ctx: ToolContext, application: str, args: str = "") -> ToolResult:
    """Open (launch) an application on this computer.

    Args:
        application: App name or alias, e.g. "vscode", "chrome", "terminal", "spotify", "calculator".
        args: Optional extra arguments passed to the app (a folder path for an editor, a URL for a browser).
    """
    canonical = canonicalise(application)
    info = ctx.backend.launch_app(f"{application} {args}".strip())
    if info.get("ok") is False:
        return ToolResult(ok=False, message=f"I couldn't open {canonical}: {info.get('error', 'unknown reason')}",
                          data=info, simulated=ctx.backend.simulated)
    speech = f"Opening {canonical.replace('_', ' ')}."
    return ToolResult(
        ok=True,
        message=f"Launched {canonical}" + (f" with args `{args}`" if args else ""),
        speech=speech,
        data=info,
        simulated=ctx.backend.simulated,
    )


@tool(
    permission=Permission.CONFIRM,
    category=ToolCategory.COMPUTER,
    examples=["Close Chrome", "Quit VS Code"],
)
def close_application(ctx: ToolContext, application: str, force: bool = False) -> ToolResult:
    """Close a running application. Unsaved work may be lost.

    Args:
        application: App name or alias to close.
        force: Force-kill the process instead of asking it to quit gracefully.
    """
    canonical = canonicalise(application)
    info = ctx.backend.terminate_app(canonical, force=force)
    if info.get("ok") is False and "error" in info:
        return ToolResult(ok=False, message=f"Could not close {canonical}: {info['error']}", data=info)
    return ToolResult(
        ok=True,
        message=f"Closed {canonical}" + (" (force-killed)" if force else ""),
        speech=f"Closed {canonical.replace('_', ' ')}.",
        data=info,
        simulated=ctx.backend.simulated,
    )


@tool(permission=Permission.SAFE, category=ToolCategory.COMPUTER)
def list_applications() -> ToolResult:
    """List the applications JARVIS knows how to open on this machine."""
    apps = describe_applications()
    return ToolResult(
        ok=True,
        message=f"{len(apps)} known applications",
        speech=f"I can open {len(apps)} applications, including VS Code, Chrome, the terminal, Spotify and more.",
        data={"applications": apps, "ids": known_applications()},
    )


# ---------------------------------------------------------------------------
# browser tabs (keyboard-driven, works with whatever window is focused)
# ---------------------------------------------------------------------------


@tool(
    permission=Permission.SAFE,
    category=ToolCategory.COMPUTER,
    examples=["Jarvis, close this tab.", "Close the current tab"],
)
def close_current_tab(ctx: ToolContext) -> ToolResult:
    """Close the currently focused browser tab (sends Ctrl/Cmd+W)."""
    modifier = "cmd" if ctx.settings.platform == "darwin" else "ctrl"
    message = ctx.backend.hotkey(modifier, "w")
    return ToolResult(ok=True, message=message, speech="Done.", simulated=ctx.backend.simulated,
                      data={"shortcut": f"{modifier}+w"})


@tool(permission=Permission.SAFE, category=ToolCategory.COMPUTER)
def new_tab(ctx: ToolContext, url: str = "") -> ToolResult:
    """Open a new browser tab, optionally at a URL.

    Args:
        url: Optional URL to open in the new tab.
    """
    modifier = "cmd" if ctx.settings.platform == "darwin" else "ctrl"
    message = ctx.backend.hotkey(modifier, "t")
    if url:
        time.sleep(0.3)
        ctx.backend.type_text(url)
        ctx.backend.press("enter")
        message += f" Navigated to {url}."
        return ToolResult(ok=True, message=message, speech=f"Opened {url}.", simulated=ctx.backend.simulated)
    return ToolResult(ok=True, message=message, speech="New tab.", simulated=ctx.backend.simulated)


@tool(permission=Permission.SAFE, category=ToolCategory.COMPUTER)
def reopen_closed_tab(ctx: ToolContext) -> ToolResult:
    """Re-open the most recently closed browser tab (Ctrl/Cmd+Shift+T)."""
    modifier = "cmd" if ctx.settings.platform == "darwin" else "ctrl"
    message = ctx.backend.hotkey(modifier, "shift", "t")
    return ToolResult(ok=True, message=message, speech="Reopened the last tab.", simulated=ctx.backend.simulated)


@tool(permission=Permission.SAFE, category=ToolCategory.COMPUTER)
def next_tab(ctx: ToolContext, direction: Literal["next", "previous"] = "next") -> ToolResult:
    """Switch to the next or previous browser tab.

    Args:
        direction: "next" (Ctrl+Tab) or "previous" (Ctrl+Shift+Tab).
    """
    modifier = "cmd" if ctx.settings.platform == "darwin" else "ctrl"
    keys = [modifier, "tab"] if direction == "next" else [modifier, "shift", "tab"]
    message = ctx.backend.hotkey(*keys)
    return ToolResult(ok=True, message=message, speech="Switched tabs.", simulated=ctx.backend.simulated)


# ---------------------------------------------------------------------------
# keyboard & mouse
# ---------------------------------------------------------------------------


@tool(permission=Permission.SAFE, category=ToolCategory.COMPUTER, examples=["Type hello world"])
def type_text(ctx: ToolContext, text: str, interval: float = 0.0, press_enter: bool = False) -> ToolResult:
    """Type text into whatever currently has keyboard focus.

    Args:
        text: The exact characters to type.
        interval: Delay in seconds between keystrokes (0 = as fast as possible).
        press_enter: Press Enter after typing (useful for searches and forms).
    """
    if not text:
        return ToolResult(ok=False, message="Nothing to type.")
    message = ctx.backend.type_text(text, interval=interval)
    if press_enter:
        ctx.backend.press("enter")
        message += " Pressed Enter."
    return ToolResult(ok=True, message=message, speech="Typed it.", simulated=ctx.backend.simulated)


@tool(permission=Permission.SAFE, category=ToolCategory.COMPUTER)
def press_key(ctx: ToolContext, key: str, times: int = 1) -> ToolResult:
    """Press a single keyboard key.

    Args:
        key: Key name, e.g. "enter", "esc", "tab", "space", "backspace", "up", "down", "f5".
        times: How many times to press it (1-50).
    """
    message = ctx.backend.press(key, times=max(1, min(int(times), 50)))
    return ToolResult(ok=True, message=message, speech="Done.", simulated=ctx.backend.simulated)


@tool(permission=Permission.SAFE, category=ToolCategory.COMPUTER)
def press_hotkey(ctx: ToolContext, keys: list[str]) -> ToolResult:
    """Press a key combination such as Ctrl+S, Alt+Tab or Ctrl+Shift+N.

    Args:
        keys: Keys to hold together, e.g. ["ctrl", "s"] or ["alt", "tab"].
    """
    if not keys:
        return ToolResult(ok=False, message="No keys supplied.")
    message = ctx.backend.hotkey(*[str(k).lower() for k in keys])
    return ToolResult(ok=True, message=message, speech="Done.", simulated=ctx.backend.simulated,
                      data={"combo": "+".join(keys)})


@tool(permission=Permission.SAFE, category=ToolCategory.COMPUTER)
def click(
    ctx: ToolContext,
    x: int | None = None,
    y: int | None = None,
    button: Literal["left", "right", "middle"] = "left",
    clicks: Literal[1, 2] = 1,
) -> ToolResult:
    """Click the mouse at screen coordinates (or wherever it currently is).

    Args:
        x: Horizontal pixel coordinate. Omit to click at the current mouse position.
        y: Vertical pixel coordinate.
        button: Mouse button: "left", "right" or "middle".
        clicks: 1 for a single click, 2 for a double click.
    """
    message = ctx.backend.click(x=x, y=y, button=button, clicks=int(clicks))
    return ToolResult(ok=True, message=message, speech="Clicked.", data={"x": x, "y": y},
                      simulated=ctx.backend.simulated)


@tool(permission=Permission.SAFE, category=ToolCategory.COMPUTER)
def move_mouse(ctx: ToolContext, x: int, y: int, duration: float = 0.2) -> ToolResult:
    """Move the mouse pointer to absolute screen coordinates.

    Args:
        x: Horizontal pixel coordinate.
        y: Vertical pixel coordinate.
        duration: Seconds the movement should take (0 = instant).
    """
    message = ctx.backend.move_mouse(x, y, duration=duration)
    return ToolResult(ok=True, message=message, speech="Done.", simulated=ctx.backend.simulated)


@tool(permission=Permission.SAFE, category=ToolCategory.COMPUTER)
def scroll(ctx: ToolContext, amount: int = -3, direction: Literal["up", "down"] | None = None) -> ToolResult:
    """Scroll the mouse wheel.

    Args:
        amount: Number of scroll clicks. Positive scrolls up, negative scrolls down.
        direction: Optional explicit "up" or "down" (overrides the sign of amount).
    """
    clicks = int(amount)
    if direction == "up":
        clicks = abs(clicks) or 3
    elif direction == "down":
        clicks = -(abs(clicks) or 3)
    message = ctx.backend.scroll(clicks)
    return ToolResult(ok=True, message=message, speech="Scrolled.", simulated=ctx.backend.simulated)


@tool(permission=Permission.SAFE, category=ToolCategory.COMPUTER)
def drag(ctx: ToolContext, x1: int, y1: int, x2: int, y2: int, duration: float = 0.4) -> ToolResult:
    """Drag the mouse from one point to another (selection, moving windows, sliders).

    Args:
        x1: Starting X coordinate.
        y1: Starting Y coordinate.
        x2: Ending X coordinate.
        y2: Ending Y coordinate.
        duration: Seconds the drag should take.
    """
    message = ctx.backend.drag(x1, y1, x2, y2, duration=duration)
    return ToolResult(ok=True, message=message, speech="Dragged.", simulated=ctx.backend.simulated)


@tool(permission=Permission.SAFE, category=ToolCategory.COMPUTER)
def select_all_and_copy(ctx: ToolContext) -> ToolResult:
    """Select everything in the focused window and copy it to the clipboard."""
    modifier = "cmd" if ctx.settings.platform == "darwin" else "ctrl"
    ctx.backend.hotkey(modifier, "a")
    time.sleep(0.1)
    ctx.backend.hotkey(modifier, "c")
    time.sleep(0.1)
    text = ctx.backend.clipboard_get()
    return ToolResult(ok=True, message=f"Copied {len(text)} characters.", speech="Copied.",
                      data={"preview": text[:500], "length": len(text)}, simulated=ctx.backend.simulated)


@tool(permission=Permission.SAFE, category=ToolCategory.COMPUTER)
def paste_text(ctx: ToolContext, text: str) -> ToolResult:
    """Put text on the clipboard and paste it into the focused window.

    Args:
        text: Text to paste.
    """
    ctx.backend.clipboard_set(text)
    modifier = "cmd" if ctx.settings.platform == "darwin" else "ctrl"
    ctx.backend.hotkey(modifier, "v")
    return ToolResult(ok=True, message=f"Pasted {len(text)} characters.", speech="Pasted.",
                      simulated=ctx.backend.simulated)


# ---------------------------------------------------------------------------
# windows
# ---------------------------------------------------------------------------


@tool(permission=Permission.SAFE, category=ToolCategory.COMPUTER)
def list_windows(ctx: ToolContext) -> ToolResult:
    """List the windows currently open on the desktop."""
    windows = ctx.backend.list_windows()
    if not windows:
        return ToolResult(ok=True, message="No window information available.",
                          data={"windows": [], "apps": ctx.backend.describe().get("running_apps", [])},
                          speech="I can't see any windows from here.")
    titles = [w.get("title") or w.get("app") or "?" for w in windows][:8]
    return ToolResult(
        ok=True,
        message=f"{len(windows)} windows open",
        speech="You have " + ", ".join(titles[:4]) + (" and more." if len(titles) > 4 else " open."),
        data={"windows": windows},
        simulated=ctx.backend.simulated,
    )


@tool(permission=Permission.SAFE, category=ToolCategory.COMPUTER)
def switch_window(ctx: ToolContext, title: str = "") -> ToolResult:
    """Bring a window to the front, by title, or cycle to the next window.

    Args:
        title: Part of the window or app title to focus. Empty = cycle (Alt+Tab).
    """
    if not title:
        modifier = "cmd" if ctx.settings.platform == "darwin" else "alt"
        message = ctx.backend.hotkey(modifier, "tab")
        return ToolResult(ok=True, message=message, speech="Switched.", simulated=ctx.backend.simulated)
    message = ctx.backend.focus_window(title)
    found = "No window matching" not in message
    return ToolResult(ok=found, message=message, speech="Switched." if found else "I couldn't find that window.",
                      simulated=ctx.backend.simulated)


@tool(permission=Permission.SAFE, category=ToolCategory.COMPUTER)
def minimise_window(ctx: ToolContext, all_windows: bool = False) -> ToolResult:
    """Minimise the current window, or all windows (show desktop).

    Args:
        all_windows: When true, minimise everything and show the desktop.
    """
    modifier = "cmd" if ctx.settings.platform == "darwin" else "ctrl"
    if all_windows:
        if ctx.settings.platform == "darwin":
            ctx.backend.hotkey("f11")
        else:
            ctx.backend.hotkey("super", "d")
        return ToolResult(ok=True, message="Showed the desktop.", speech="Showing the desktop.",
                          simulated=ctx.backend.simulated)
    if ctx.settings.platform == "darwin":
        ctx.backend.hotkey("cmd", "m")
    else:
        ctx.backend.hotkey("super", "down")
    return ToolResult(ok=True, message="Minimised the active window.", speech="Minimised.",
                      simulated=ctx.backend.simulated)


# ---------------------------------------------------------------------------
# screen
# ---------------------------------------------------------------------------


@tool(
    permission=Permission.SAFE,
    category=ToolCategory.COMPUTER,
    examples=["Jarvis, take a screenshot."],
)
def take_screenshot(ctx: ToolContext, note: str = "") -> ToolResult:
    """Capture the whole screen and save it as a PNG.

    Args:
        note: Optional label saved with the screenshot so it is easy to find later.
    """
    info = ctx.backend.screenshot()
    path = info.get("path", "")
    if ctx.db is not None and path:
        ctx.db.add_artifact("screenshot", path, {"note": note, "simulated": info.get("simulated", False),
                                                "width": info.get("width"), "height": info.get("height")})
    return ToolResult(
        ok=True,
        message=f"Screenshot saved to {path}",
        speech="Screenshot taken." if not info.get("simulated") else "Screenshot captured.",
        data=info,
        artifacts=[{"type": "image", "path": path, "note": note}],
        simulated=bool(info.get("simulated")),
    )


@tool(permission=Permission.SAFE, category=ToolCategory.COMPUTER)
def get_screen_info(ctx: ToolContext) -> ToolResult:
    """Report the screen resolution, mouse position and focused window."""
    width, height = ctx.backend.screen_size()
    mouse = ctx.backend.mouse_position()
    data = {
        "resolution": {"width": width, "height": height},
        "mouse": {"x": mouse[0], "y": mouse[1]},
        "windows": ctx.backend.list_windows()[:10],
    }
    return ToolResult(ok=True, message=f"Screen is {width}x{height}; mouse at {mouse}", data=data,
                      speech=f"The screen is {width} by {height} pixels.", simulated=ctx.backend.simulated)


# ---------------------------------------------------------------------------
# audio / display
# ---------------------------------------------------------------------------


@tool(permission=Permission.SAFE, category=ToolCategory.COMPUTER, examples=["Set the volume to 30%"])
def set_volume(ctx: ToolContext, percent: int | None = None, muted: bool | None = None,
               delta: int | None = None) -> ToolResult:
    """Set, mute or adjust the system volume.

    Args:
        percent: Absolute volume 0-100.
        muted: true to mute, false to unmute.
        delta: Relative change, e.g. +10 or -20.
    """
    if muted is not None:
        message = ctx.backend.set_volume(0 if muted else 50)
        speech = "Muted." if muted else "Unmuted."
        return ToolResult(ok=True, message=message, speech=speech, simulated=ctx.backend.simulated)
    if delta is not None:
        if hasattr(ctx.backend, "change_volume"):
            message = ctx.backend.change_volume(int(delta))
        else:
            message = ctx.backend.set_volume(int(delta))
        return ToolResult(ok=True, message=message, speech="Volume adjusted.", simulated=ctx.backend.simulated)
    if percent is None:
        return ToolResult(ok=False, message="Tell me a volume level, for example “set the volume to 30”.")
    message = ctx.backend.set_volume(int(percent))
    return ToolResult(ok=True, message=message, speech=f"Volume at {int(percent)} percent.",
                      simulated=ctx.backend.simulated)


@tool(permission=Permission.SAFE, category=ToolCategory.COMPUTER)
def media_control(ctx: ToolContext, action: Literal["play", "pause", "playpause", "next", "previous", "stop", "mute"] = "playpause") -> ToolResult:
    """Control media playback with the hardware media keys.

    Args:
        action: One of play, pause, playpause, next, previous, stop, mute.
    """
    message = ctx.backend.media_key(action)
    return ToolResult(ok=True, message=message, speech=f"{action.title()}.", simulated=ctx.backend.simulated)


@tool(permission=Permission.SAFE, category=ToolCategory.COMPUTER)
def set_brightness(ctx: ToolContext, percent: int) -> ToolResult:
    """Set the screen brightness.

    Args:
        percent: Brightness 0-100.
    """
    message = ctx.backend.set_brightness(int(percent))
    ok = "unavailable" not in message.lower()
    return ToolResult(ok=ok, message=message, speech=f"Brightness at {int(percent)} percent." if ok else message,
                      simulated=ctx.backend.simulated)


# ---------------------------------------------------------------------------
# waits (used inside plans)
# ---------------------------------------------------------------------------


@tool(permission=Permission.SAFE, category=ToolCategory.COMPUTER)
async def wait(seconds: float = 1.0) -> ToolResult:
    """Wait for a number of seconds — useful between steps of a plan.

    Args:
        seconds: How long to wait (max 30).
    """
    import asyncio

    delay = max(0.0, min(float(seconds), 30.0))
    await asyncio.sleep(delay)
    return ToolResult(ok=True, message=f"Waited {delay:.1f}s.", speech="")
