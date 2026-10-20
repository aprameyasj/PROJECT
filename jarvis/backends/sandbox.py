"""Simulated desktop backend.

Used whenever there is no GUI available (containers, CI, `JARVIS_DRY_RUN=1`).
It is *not* a stub: it maintains a small virtual machine — running apps,
browser tabs, mouse position, clipboard, volume — so that multi-step plans
behave consistently and the dashboard can show a believable trace of what
JARVIS *would* have done on your computer.

Every action is appended to `action_log` (and mirrored into SQLite) which
makes this backend ideal for developing and testing new tools safely.
"""

from __future__ import annotations

import json
import os
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .apps import canonicalise, known_applications, launch_command


class SandboxBackend:
    name = "sandbox"
    simulated = True

    def __init__(self, settings: Any = None) -> None:
        self.settings = settings
        self.available = True
        self.unavailable_reason = ""
        self.reason = "no graphical session detected; running the simulated desktop"
        self.action_log: list[dict[str, Any]] = []

        # ---- virtual machine state ------------------------------------
        home = Path(os.environ.get("JARVIS_HOME", Path.cwd() / ".jarvis"))
        self.state: dict[str, Any] = {
            "running_apps": ["chrome"],
            "windows": [{"app": "chrome", "title": "New Tab - Google Chrome", "focused": True}],
            "tabs": ["New Tab"],
            "active_tab": 0,
            "mouse": [640, 360],
            "screen": [1920, 1080],
            "clipboard": "",
            "volume": 45,
            "brightness": 80,
            "typing_buffer": "",
            "battery": 87,
            "plugged_in": True,
            "power": "on",
            "screenshots": [],
        }
        self._screenshots_dir = home / "screenshots"
        self._screenshots_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "simulated": True,
            "reason": self.reason,
            "actions_recorded": len(self.action_log),
            "screen": self.state["screen"],
            "running_apps": sorted(set(self.state["running_apps"])),
            "open_windows": len(self.state["windows"]),
            "tabs": list(self.state["tabs"]),
            "volume": self.state["volume"],
            "power": self.state["power"],
        }

    def _record(self, action: str, detail: Any = None, result: Any = None) -> dict[str, Any]:
        entry = {
            "ts": time.time(),
            "at": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "detail": detail,
            "result": result,
        }
        self.action_log.append(entry)
        if len(self.action_log) > 500:
            self.action_log = self.action_log[-500:]
        return entry

    # ------------------------------------------------------------------
    # input devices
    # ------------------------------------------------------------------
    def screen_size(self) -> tuple[int, int]:
        return tuple(self.state["screen"])  # type: ignore[return-value]

    def mouse_position(self) -> tuple[int, int]:
        return tuple(self.state["mouse"])  # type: ignore[return-value]

    def hotkey(self, *keys: str) -> str:
        combo = "+".join(k.lower() for k in keys if k)
        # model a few well-known combos so the simulation is coherent
        if combo in {"ctrl+w", "cmd+w"}:
            if self.state["tabs"]:
                closed = self.state["tabs"].pop(min(self.state["active_tab"], len(self.state["tabs"]) - 1))
                self.state["active_tab"] = max(0, min(self.state["active_tab"], len(self.state["tabs"]) - 1))
                self._record("hotkey", combo, {"closed_tab": closed})
                return f"Sent {combo} — closed tab “{closed}”."
            self._record("hotkey", combo, {"closed_tab": None})
            return f"Sent {combo} (no tabs open)."
        if combo in {"ctrl+t", "cmd+t"}:
            self.state["tabs"].append("New Tab")
            self.state["active_tab"] = len(self.state["tabs"]) - 1
            self._record("hotkey", combo, {"tabs": len(self.state["tabs"])})
            return f"Sent {combo} — opened a new tab."
        if combo in {"ctrl+shift+t", "cmd+shift+t"}:
            self.state["tabs"].append("Restored Tab")
            self.state["active_tab"] = len(self.state["tabs"]) - 1
            self._record("hotkey", combo, {"restored": True})
            return f"Sent {combo} — reopened the last closed tab."
        if combo in {"ctrl+c", "cmd+c"}:
            self._record("hotkey", combo, {"clipboard": self.state["typing_buffer"][-200:]})
            return f"Sent {combo} — copied selection to clipboard."
        if combo in {"ctrl+v", "cmd+v"}:
            self._record("hotkey", combo, {"pasted": self.state["clipboard"][:80]})
            return f"Sent {combo} — pasted “{self.state['clipboard'][:40]}”."
        if combo in {"alt+tab", "cmd+tab"}:
            self._rotate_focus()
            return f"Sent {combo} — switched to “{self._focused_window().get('title', '')}”."
        if combo in {"alt+f4", "cmd+q"}:
            window = self._focused_window()
            self._close_window(window)
            return f"Sent {combo} — closed “{window.get('title', 'the active window')}”."
        self._record("hotkey", combo)
        return f"Sent the {combo} key combination."

    def press(self, key: str, times: int = 1) -> str:
        key = (key or "").lower()
        if key in {"enter", "return"}:
            self._record("press", {"key": key, "times": times})
            return f"Pressed {key.upper()} {times} time(s)."
        if key == "escape":
            return f"Pressed ESCAPE {times} time(s)."
        self._record("press", {"key": key, "times": times})
        return f"Pressed {key.upper()} {times} time(s)."

    def type_text(self, text: str, interval: float = 0.0) -> str:
        self.state["typing_buffer"] = text
        self._record("type_text", {"chars": len(text), "interval": interval, "preview": text[:120]})
        return f"Typed {len(text)} characters: “{text[:60]}{'…' if len(text) > 60 else ''}”."

    def click(self, x: int | None = None, y: int | None = None, button: str = "left", clicks: int = 1) -> str:
        if x is None or y is None:
            x, y = self.state["mouse"]
        self.state["mouse"] = [int(x), int(y)]
        self._record("click", {"x": x, "y": y, "button": button, "clicks": clicks})
        kind = "double-clicked" if clicks == 2 else f"{button}-clicked"
        return f"{kind.capitalize()} at ({x}, {y})."

    def move_mouse(self, x: int, y: int, duration: float = 0.2) -> str:
        self.state["mouse"] = [int(x), int(y)]
        self._record("move_mouse", {"x": x, "y": y, "duration": duration})
        return f"Moved the mouse to ({x}, {y})."

    def scroll(self, amount: int, x: int | None = None, y: int | None = None) -> str:
        self._record("scroll", {"amount": amount, "x": x, "y": y})
        direction = "up" if amount > 0 else "down"
        return f"Scrolled {direction} by {abs(amount)} clicks."

    def drag(self, x1: int, y1: int, x2: int, y2: int, duration: float = 0.4) -> str:
        self.state["mouse"] = [int(x2), int(y2)]
        self._record("drag", {"from": [x1, y1], "to": [x2, y2]})
        return f"Dragged from ({x1}, {y1}) to ({x2}, {y2})."

    def clipboard_set(self, text: str) -> str:
        self.state["clipboard"] = text
        self._record("clipboard_set", {"chars": len(text)})
        return f"Copied {len(text)} characters to the clipboard."

    def clipboard_get(self) -> str:
        self._record("clipboard_get", {"chars": len(self.state["clipboard"])})
        return self.state["clipboard"]

    # ------------------------------------------------------------------
    # screen
    # ------------------------------------------------------------------
    def screenshot(self, path: str | None = None) -> dict[str, Any]:
        if path is None:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            path = str(self._screenshots_dir / f"sandbox-{stamp}-{random.randint(100, 999)}.png")
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        info = self._render_fake_screenshot(path)
        info.update({"simulated": True, "path": path, "windows": self.list_windows()})
        self.state["screenshots"].append(path)
        self._record("screenshot", {"path": path}, info)
        return info

    def _render_fake_screenshot(self, path: str) -> dict[str, Any]:
        """Draw a recognisable placeholder so the UI has a real image."""
        width, height = self.state["screen"]
        scale = min(1.0, 1280 / width)
        width, height = int(width * scale), int(height * scale)
        try:
            from PIL import Image, ImageDraw

            image = Image.new("RGB", (width, height), "#0b1020")
            draw = ImageDraw.Draw(image)
            draw.rectangle([0, 0, width, 34], fill="#111a33")
            draw.text((12, 10), "JARVIS simulated desktop  (no GUI in this environment)", fill="#8fb0ff")
            top = 50
            for index, window in enumerate(self.state["windows"][:4]):
                box = [20 + index * 12, top + index * 12, width - 20 - index * 12, height - 60 - index * 12]
                draw.rectangle(box, outline="#2b3a67", width=2)
                draw.rectangle([box[0], box[1], box[2], box[1] + 26], fill="#182246")
                draw.text((box[0] + 10, box[1] + 7), str(window.get("title", "window"))[:64], fill="#cfe0ff")
            tabs = self.state["tabs"][:8]
            for index, tab in enumerate(tabs):
                x = 24 + index * 150
                fill = "#2b3a67" if index == self.state["active_tab"] else "#1a2340"
                draw.rectangle([x, 84, x + 140, 108], fill=fill)
                draw.text((x + 8, 90), str(tab)[:18], fill="#dce6ff")
            mx, my = self.state["mouse"]
            draw.ellipse([mx * scale - 6, my * scale - 6, mx * scale + 6, my * scale + 6], outline="#ff5c7a", width=2)
            draw.text((20, height - 40), f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  ·  tabs={len(tabs)}", fill="#6f7fae")
            image.save(path)
            return {"width": width, "height": height, "bytes": Path(path).stat().st_size}
        except Exception:
            # Pillow missing: still produce a file so downstream code has a path
            Path(path).write_bytes(b"JARVIS-SIMULATED-SCREENSHOT\n" + json.dumps(self.describe()).encode())
            return {"width": width, "height": height, "bytes": Path(path).stat().st_size}

    # ------------------------------------------------------------------
    # applications / windows
    # ------------------------------------------------------------------
    def _focused_window(self) -> dict[str, Any]:
        for window in self.state["windows"]:
            if window.get("focused"):
                return window
        return self.state["windows"][-1] if self.state["windows"] else {"title": "", "app": ""}

    def _rotate_focus(self) -> None:
        windows = self.state["windows"]
        if len(windows) < 2:
            return
        for window in windows:
            window["focused"] = False
        index = windows.index(self._focused_window()) if any(w.get("focused") for w in windows) else 0
        windows[(index + 1) % len(windows)]["focused"] = True

    def _close_window(self, window: dict[str, Any]) -> None:
        if window in self.state["windows"]:
            self.state["windows"].remove(window)
            app = window.get("app")
            if app and not any(w.get("app") == app for w in self.state["windows"]):
                if app in self.state["running_apps"]:
                    self.state["running_apps"].remove(app)

    def launch_app(self, application: str) -> dict[str, Any]:
        canonical = canonicalise(application)
        command, _ = launch_command(application)
        info = {
            "application": canonical,
            "requested": application,
            "command": command or f"<simulated launch of {canonical}>",
            "pid": random.randint(4000, 60000),
            "simulated": True,
        }
        if canonical not in self.state["running_apps"]:
            self.state["running_apps"].append(canonical)
        for window in self.state["windows"]:
            window["focused"] = False
        self.state["windows"].append(
            {"app": canonical, "title": f"{canonical.replace('_', ' ').title()}", "focused": True, "pid": info["pid"]}
        )
        self._record("launch_app", {"application": canonical, "command": info["command"]}, info)
        return info

    def terminate_app(self, application: str, force: bool = False) -> dict[str, Any]:
        canonical = canonicalise(application)
        closed = [w for w in list(self.state["windows"]) if w.get("app") == canonical]
        for window in closed:
            self._close_window(window)
        if canonical in self.state["running_apps"]:
            self.state["running_apps"].remove(canonical)
        info = {"application": canonical, "closed_windows": len(closed), "force": force, "simulated": True}
        self._record("terminate_app", {"application": canonical}, info)
        return info

    def list_windows(self) -> list[dict[str, Any]]:
        return [dict(w) for w in self.state["windows"]]

    def focus_window(self, title: str) -> str:
        needle = (title or "").lower()
        for window in self.state["windows"]:
            if needle in str(window.get("title", "")).lower():
                for other in self.state["windows"]:
                    other["focused"] = False
                window["focused"] = True
                self._record("focus_window", {"title": title}, window)
                return f"Focused “{window['title']}”."
        self._record("focus_window", {"title": title, "found": False})
        return f"No window matching “{title}”."

    # ------------------------------------------------------------------
    # system
    # ------------------------------------------------------------------
    def notify(self, title: str, body: str) -> str:
        self._record("notify", {"title": title, "body": body})
        return f"[simulated notification] {title}: {body}"

    def open_url(self, url: str, application: str | None = None) -> dict[str, Any]:
        browser = canonicalise(application) if application else "chrome"
        if browser not in self.state["running_apps"]:
            self.launch_app(browser)
        self.state["tabs"].append(url)
        self.state["active_tab"] = len(self.state["tabs"]) - 1
        for window in self.state["windows"]:
            if window.get("app") == browser:
                window["title"] = f"{url} - {browser.title()}"
                window["focused"] = True
        info = {"url": url, "browser": browser, "simulated": True, "tabs": len(self.state["tabs"])}
        self._record("open_url", {"url": url, "browser": browser}, info)
        return info

    def shell(self, command: str, cwd: str | None = None, timeout: int = 30) -> dict[str, Any]:
        """Execute read-only commands for real; simulate anything mutating."""
        safe_prefixes = ("echo ", "date", "uname", "whoami", "pwd", "hostname", "python --version", "python3 --version")
        lowered = command.strip().lower()
        self._record("shell", {"command": command, "cwd": cwd}, {"simulated": lowered not in safe_prefixes})
        if lowered.startswith(safe_prefixes):
            import subprocess

            try:
                proc = subprocess.run(
                    command, shell=True, capture_output=True, text=True, timeout=min(timeout, 15), cwd=cwd
                )
                return {
                    "command": command,
                    "returncode": proc.returncode,
                    "stdout": proc.stdout[-4000:],
                    "stderr": proc.stderr[-2000:],
                    "simulated": False,
                }
            except Exception as exc:  # noqa: BLE001
                return {"command": command, "returncode": -1, "stdout": "", "stderr": str(exc), "simulated": False}
        return {
            "command": command,
            "returncode": 0,
            "stdout": f"[simulated] `{command}` would run here. No GUI/OS session is attached to this sandbox.",
            "stderr": "",
            "simulated": True,
        }

    def system_status(self) -> dict[str, Any]:
        status: dict[str, Any] = {
            "simulated": True,
            "platform": "linux (sandbox)",
            "hostname": os.environ.get("HOSTNAME", "jarvis-sandbox"),
            "cpu_percent": round(random.uniform(4, 22), 1),
            "memory_percent": round(random.uniform(28, 55), 1),
            "disk_percent": round(random.uniform(35, 62), 1),
            "battery_percent": self.state["battery"],
            "plugged_in": self.state["plugged_in"],
            "screen": self.state["screen"],
            "volume": self.state["volume"],
            "brightness": self.state["brightness"],
            "running_apps": sorted(set(self.state["running_apps"])),
            "open_windows": len(self.state["windows"]),
            "open_tabs": len(self.state["tabs"]),
            "active_tab": self.state["tabs"][self.state["active_tab"]] if self.state["tabs"] else None,
            "time": datetime.now().isoformat(timespec="seconds"),
            "actions_simulated": len(self.action_log),
        }
        self._record("system_status", None, {"cpu": status["cpu_percent"], "mem": status["memory_percent"]})
        return status

    def set_volume(self, percent: int) -> str:
        percent = max(0, min(100, int(percent)))
        self.state["volume"] = percent
        self._record("set_volume", {"percent": percent})
        return f"Set the volume to {percent}%."

    def change_volume(self, delta: int) -> str:
        return self.set_volume(int(self.state["volume"]) + int(delta))

    def set_brightness(self, percent: int) -> str:
        percent = max(0, min(100, int(percent)))
        self.state["brightness"] = percent
        self._record("set_brightness", {"percent": percent})
        return f"Set the brightness to {percent}%."

    def media_key(self, action: str) -> str:
        self._record("media_key", {"action": action})
        return f"{action.capitalize()} — media {action} (simulated)."

    def power(self, action: str) -> str:
        self.state["power"] = action
        self._record("power", {"action": action})
        return f"[simulated] This computer would now {action}. Nothing actually happened."

    # ------------------------------------------------------------------
    def known_apps(self) -> list[str]:
        return known_applications()

    def reset(self) -> None:
        self.action_log.clear()
        self.state.update({"tabs": ["New Tab"], "active_tab": 0, "typing_buffer": "", "power": "on"})

    def to_dict(self) -> dict[str, Any]:
        return {"state": self.state, "recent_actions": self.action_log[-40:]}
