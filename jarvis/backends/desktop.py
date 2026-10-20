"""Real desktop backend — this is the one that actually drives your computer.

It is deliberately lazy: `pyautogui`, `psutil`, `mss` and friends are imported
only when they are first needed, so JARVIS boots (and its tests run) on
machines where those optional dependencies are missing.

Enable it with `JARVIS_BACKEND=desktop` (the default when a display exists).
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .apps import canonicalise, launch_command, process_name


class DesktopBackend:
    name = "desktop"
    simulated = False

    def __init__(self, settings: Any = None) -> None:
        self.settings = settings
        self.system = platform.system().lower()
        self.action_log: list[dict[str, Any]] = []
        self._gui: Any = None
        self._gui_error: str = ""
        self.available, self.unavailable_reason = self._probe()
        self.reason = self.unavailable_reason
        home = Path(getattr(settings, "screenshots_dir", Path.cwd() / ".jarvis" / "screenshots"))
        home.mkdir(parents=True, exist_ok=True)
        self._screenshots_dir = home

    # ------------------------------------------------------------------
    def _probe(self) -> tuple[bool, str]:
        if self.system == "linux" and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            return False, "no DISPLAY / WAYLAND_DISPLAY — headless environment"
        if self.system == "linux" and os.environ.get("XDG_SESSION_TYPE") == "wayland":
            return True, "Wayland session: pyautogui input may need XWayland"
        return True, ""

    @property
    def gui(self) -> Any:
        """Lazily import pyautogui with fail-safes enabled."""
        if self._gui is not None:
            return self._gui
        try:
            import pyautogui  # type: ignore

            pyautogui.FAILSAFE = True       # slam the mouse into a corner to abort
            pyautogui.PAUSE = 0.05
            self._gui = pyautogui
        except Exception as exc:  # noqa: BLE001
            self._gui_error = f"{type(exc).__name__}: {exc}"
            raise RuntimeError(
                f"pyautogui is not usable here ({self._gui_error}). "
                "Install it with `pip install pyautogui` (Linux also needs "
                "`python3-xlib scrot`) or set JARVIS_BACKEND=sandbox."
            ) from exc
        return self._gui

    def describe(self) -> dict[str, Any]:
        info: dict[str, Any] = {
            "name": self.name,
            "simulated": False,
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "available": self.available,
            "reason": self.unavailable_reason,
            "gui_ready": self._gui is not None,
            "actions_recorded": len(self.action_log),
        }
        try:
            info["screen"] = list(self.screen_size())
        except Exception as exc:  # noqa: BLE001
            info["screen_error"] = str(exc)
        return info

    def _record(self, action: str, detail: Any = None, result: Any = None) -> None:
        self.action_log.append(
            {"ts": time.time(), "at": datetime.now().isoformat(timespec="seconds"),
             "action": action, "detail": detail, "result": result}
        )
        if len(self.action_log) > 500:
            self.action_log = self.action_log[-500:]

    # ------------------------------------------------------------------
    # input
    # ------------------------------------------------------------------
    def screen_size(self) -> tuple[int, int]:
        gui = self.gui
        width, height = gui.size()
        return int(width), int(height)

    def mouse_position(self) -> tuple[int, int]:
        x, y = self.gui.position()
        return int(x), int(y)

    def hotkey(self, *keys: str) -> str:
        keys = tuple(k.lower() for k in keys if k)
        if not keys:
            return "No keys supplied."
        if self.system == "darwin":
            keys = tuple("command" if k in {"ctrl", "control"} else k for k in keys)
        self.gui.hotkey(*keys)
        self._record("hotkey", list(keys))
        return f"Sent {'+'.join(keys)}."

    def press(self, key: str, times: int = 1) -> str:
        key = (key or "").lower().strip()
        if not key:
            return "No key supplied."
        self.gui.press(key, presses=max(1, min(int(times), 50)))
        self._record("press", {"key": key, "times": times})
        return f"Pressed {key.upper()} {times} time(s)."

    def type_text(self, text: str, interval: float = 0.02) -> str:
        if not text:
            return "Nothing to type."
        self.gui.typewrite(text, interval=max(0.0, min(float(interval), 0.5)))
        self._record("type_text", {"chars": len(text)})
        return f"Typed {len(text)} characters."

    def click(self, x: int | None = None, y: int | None = None, button: str = "left", clicks: int = 1) -> str:
        gui = self.gui
        if x is None or y is None:
            x, y = gui.position()
        gui.click(x=int(x), y=int(y), button=button, clicks=int(clicks))
        self._record("click", {"x": x, "y": y, "button": button, "clicks": clicks})
        return f"Clicked at ({int(x)}, {int(y)}) with the {button} button."

    def move_mouse(self, x: int, y: int, duration: float = 0.2) -> str:
        self.gui.moveTo(int(x), int(y), duration=max(0.0, float(duration)))
        self._record("move_mouse", {"x": x, "y": y})
        return f"Moved the mouse to ({int(x)}, {int(y)})."

    def scroll(self, amount: int, x: int | None = None, y: int | None = None) -> str:
        if x is not None and y is not None:
            self.gui.moveTo(int(x), int(y))
        self.gui.scroll(int(amount))
        self._record("scroll", {"amount": amount})
        return f"Scrolled by {int(amount)}."

    def drag(self, x1: int, y1: int, x2: int, y2: int, duration: float = 0.4) -> str:
        self.gui.moveTo(int(x1), int(y1))
        self.gui.dragTo(int(x2), int(y2), duration=max(0.05, float(duration)), button="left")
        self._record("drag", {"from": [x1, y1], "to": [x2, y2]})
        return f"Dragged from ({x1}, {y1}) to ({x2}, {y2})."

    def clipboard_set(self, text: str) -> str:
        try:
            import pyperclip  # type: ignore

            pyperclip.copy(text)
        except Exception:
            if self.system == "darwin":
                subprocess.run(["pbcopy"], input=text, text=True, check=False)
            elif self.system == "windows":
                subprocess.run(["clip"], input=text, text=True, check=False, shell=True)
            else:
                subprocess.run(["xclip", "-selection", "clipboard"], input=text, text=True, check=False)
        self._record("clipboard_set", {"chars": len(text)})
        return f"Copied {len(text)} characters to the clipboard."

    def clipboard_get(self) -> str:
        try:
            import pyperclip  # type: ignore

            return pyperclip.paste()
        except Exception:
            if self.system == "darwin":
                return subprocess.run(["pbpaste"], capture_output=True, text=True, check=False).stdout
            if self.system == "windows":
                return subprocess.run("powershell -command Get-Clipboard", capture_output=True, text=True,
                                      shell=True, check=False).stdout
            return subprocess.run(["xclip", "-selection", "clipboard", "-o"], capture_output=True, text=True,
                                  check=False).stdout

    # ------------------------------------------------------------------
    # screen capture
    # ------------------------------------------------------------------
    def screenshot(self, path: str | None = None) -> dict[str, Any]:
        if path is None:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            path = str(self._screenshots_dir / f"screen-{stamp}.png")
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        gui = self.gui
        image = gui.screenshot()
        image.save(path)
        info = {
            "path": path,
            "width": image.width,
            "height": image.height,
            "bytes": Path(path).stat().st_size,
            "simulated": False,
        }
        self._record("screenshot", {"path": path}, info)
        return info

    # ------------------------------------------------------------------
    # applications & windows
    # ------------------------------------------------------------------
    def launch_app(self, application: str) -> dict[str, Any]:
        command, canonical = launch_command(application)
        if not command:
            return {"application": canonical, "ok": False, "error": f"No launch command known for `{application}`."}

        try:
            if self.system == "windows":
                if command.lower().startswith("start "):
                    proc = subprocess.Popen(command, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                else:
                    proc = subprocess.Popen(shlex_split_windows(command))
            elif command.startswith("open "):
                proc = subprocess.Popen(["/bin/sh", "-c", command])
            else:
                proc = subprocess.Popen(
                    shlex_split_posix(command), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
            time.sleep(0.4)
            alive = proc.poll() is None
            info = {
                "application": canonical,
                "requested": application,
                "command": command,
                "pid": proc.pid,
                "ok": True,
                "running": alive,
                "simulated": False,
            }
            self._record("launch_app", {"application": canonical, "command": command}, info)
            return info
        except FileNotFoundError:
            return {"application": canonical, "ok": False,
                    "error": f"`{command.split()[0]}` is not installed or not on PATH."}
        except Exception as exc:  # noqa: BLE001
            return {"application": canonical, "ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def terminate_app(self, application: str, force: bool = False) -> dict[str, Any]:
        canonical = canonicalise(application)
        target = process_name(application) or canonical
        try:
            if self.system == "windows":
                args = ["taskkill", "/IM", target] + (["/F"] if force else [])
            elif self.system == "darwin":
                args = ["pkill", "-9" if force else "-TERM", target]
            else:
                args = ["pkill", "-9" if force else "-TERM", "-f", target]
            proc = subprocess.run(args, capture_output=True, text=True, timeout=15)
            info = {
                "application": canonical,
                "target": target,
                "returncode": proc.returncode,
                "ok": proc.returncode == 0,
                "detail": (proc.stderr or proc.stdout or "").strip()[:300] or ("terminated" if proc.returncode == 0
                                                                                else "no matching process"),
                "simulated": False,
            }
        except Exception as exc:  # noqa: BLE001
            info = {"application": canonical, "ok": False, "error": f"{type(exc).__name__}: {exc}", "simulated": False}
        self._record("terminate_app", {"application": canonical}, info)
        return info

    def list_windows(self) -> list[dict[str, Any]]:
        try:
            if self.system == "windows":
                script = (
                    "Get-Process | Where-Object {$_.MainWindowTitle -ne ''} | "
                    "Select-Object Id,ProcessName,MainWindowTitle | ConvertTo-Json -Compress"
                )
                proc = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                                      capture_output=True, text=True, timeout=15)
                import json

                raw = json.loads(proc.stdout or "[]")
                raw = raw if isinstance(raw, list) else [raw]
                return [{"pid": r.get("Id"), "app": r.get("ProcessName"), "title": r.get("MainWindowTitle")}
                        for r in raw]
            if self.system == "darwin":
                script = (
                    'tell application "System Events" to get name of every process '
                    "whose background only is false"
                )
                proc = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=15)
                return [{"app": name.strip(), "title": name.strip()} for name in proc.stdout.split(",") if name.strip()]
            # Linux: wmctrl if present, else xdotool, else /proc scan
            if shutil.which("wmctrl"):
                proc = subprocess.run(["wmctrl", "-l"], capture_output=True, text=True, timeout=10)
                out = []
                for line in proc.stdout.splitlines():
                    parts = line.split(None, 3)
                    if len(parts) >= 4:
                        out.append({"id": parts[0], "title": parts[3]})
                return out
            if shutil.which("xdotool"):
                proc = subprocess.run(["xdotool", "search", "--onlyvisible", "--name", ""],
                                      capture_output=True, text=True, timeout=10)
                out = []
                for wid in proc.stdout.split():
                    name = subprocess.run(["xdotool", "getwindowname", wid], capture_output=True, text=True).stdout
                    out.append({"id": wid, "title": name.strip()})
                return out
        except Exception:  # noqa: BLE001
            return []
        return []

    def focus_window(self, title: str) -> str:
        windows = self.list_windows()
        needle = (title or "").lower()
        match = next((w for w in windows if needle in str(w.get("title", "")).lower()), None)
        if not match:
            return f"No window matching “{title}”."
        try:
            if self.system == "windows":
                script = f'(New-Object -ComObject WScript.Shell).AppActivate({match.get("pid")})'
                subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, timeout=10)
            elif self.system == "darwin":
                subprocess.run(["osascript", "-e", f'tell application "{match.get("app")}" to activate'],
                               capture_output=True, timeout=10)
            elif match.get("id") and shutil.which("wmctrl"):
                subprocess.run(["wmctrl", "-i", "-a", str(match["id"])], capture_output=True, timeout=10)
            elif match.get("id") and shutil.which("xdotool"):
                subprocess.run(["xdotool", "windowactivate", str(match["id"])], capture_output=True, timeout=10)
        except Exception as exc:  # noqa: BLE001
            return f"Could not focus “{title}”: {exc}"
        self._record("focus_window", {"title": title}, match)
        return f"Focused “{match.get('title')}”."

    # ------------------------------------------------------------------
    # system
    # ------------------------------------------------------------------
    def notify(self, title: str, body: str) -> str:
        title = title or "JARVIS"
        try:
            if self.system == "darwin":
                script = f'display notification "{body}" with title "{title}"'
                subprocess.run(["osascript", "-e", script], capture_output=True, timeout=10)
            elif self.system == "windows":
                ps = (
                    "[reflection.assembly]::loadwithpartialname('System.Windows.Forms') > $null;"
                    "$n=New-Object System.Windows.Forms.NotifyIcon;"
                    "$n.Icon=[System.Drawing.SystemIcons]::Information;$n.Visible=$true;"
                    f"$n.ShowBalloonTip(6000,'{title}','{body}',[System.Windows.Forms.ToolTipIcon]::Info)"
                )
                subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, timeout=15)
            elif shutil.which("notify-send"):
                subprocess.run(["notify-send", title, body], capture_output=True, timeout=10)
            else:
                return f"[{title}] {body}"
        except Exception as exc:  # noqa: BLE001
            return f"Notification failed: {exc}"
        self._record("notify", {"title": title, "body": body})
        return f"Sent notification: {title}"

    def open_url(self, url: str, application: str | None = None) -> dict[str, Any]:
        import webbrowser

        try:
            if application:
                command, canonical = launch_command(application)
                if command:
                    if self.system == "darwin":
                        subprocess.Popen(["open", "-a", canonical, url])
                    elif self.system == "windows":
                        subprocess.Popen(f'start "" "{url}"', shell=True)
                    else:
                        subprocess.Popen(shlex_split_posix(command) + [url])
                    self._record("open_url", {"url": url, "browser": canonical})
                    return {"url": url, "browser": canonical, "ok": True, "simulated": False}
            if self.system == "windows":
                os.startfile(url)  # type: ignore[attr-defined]
            elif self.system == "darwin":
                subprocess.Popen(["open", url])
            else:
                webbrowser.open(url)
            self._record("open_url", {"url": url})
            return {"url": url, "ok": True, "simulated": False}
        except Exception as exc:  # noqa: BLE001
            return {"url": url, "ok": False, "error": f"{type(exc).__name__}: {exc}", "simulated": False}

    def shell(self, command: str, cwd: str | None = None, timeout: int = 30) -> dict[str, Any]:
        started = time.time()
        try:
            proc = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                cwd=cwd or None,
                timeout=max(1, min(int(timeout), 600)),
            )
            info = {
                "command": command,
                "returncode": proc.returncode,
                "stdout": proc.stdout[-8000:],
                "stderr": proc.stderr[-4000:],
                "duration_ms": int((time.time() - started) * 1000),
                "ok": proc.returncode == 0,
                "simulated": False,
            }
        except subprocess.TimeoutExpired:
            info = {"command": command, "ok": False, "error": f"timed out after {timeout}s", "simulated": False}
        except Exception as exc:  # noqa: BLE001
            info = {"command": command, "ok": False, "error": f"{type(exc).__name__}: {exc}", "simulated": False}
        self._record("shell", {"command": command}, {"ok": info.get("ok")})
        return info

    def system_status(self) -> dict[str, Any]:
        status: dict[str, Any] = {
            "platform": f"{platform.system()} {platform.release()}",
            "machine": platform.machine(),
            "hostname": platform.node(),
            "python": sys.version.split()[0],
            "time": datetime.now().isoformat(timespec="seconds"),
            "simulated": False,
        }
        try:
            import psutil  # type: ignore

            status["cpu_percent"] = psutil.cpu_percent(interval=0.4)
            memory = psutil.virtual_memory()
            status["memory_percent"] = memory.percent
            status["memory_available_gb"] = round(memory.available / 1e9, 2)
            disk = psutil.disk_usage(str(Path.home()))
            status["disk_percent"] = disk.percent
            status["disk_free_gb"] = round(disk.free / 1e9, 2)
            battery = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
            if battery:
                status["battery_percent"] = round(battery.percent, 1)
                status["plugged_in"] = battery.power_plugged
            status["process_count"] = len(psutil.pids())
        except Exception as exc:  # noqa: BLE001
            status["psutil_error"] = f"{type(exc).__name__}: {exc}"
        try:
            status["screen"] = list(self.screen_size())
        except Exception:
            pass
        self._record("system_status", None, {"cpu": status.get("cpu_percent")})
        return status

    def set_volume(self, percent: int) -> str:
        percent = max(0, min(100, int(percent)))
        try:
            if self.system == "darwin":
                subprocess.run(["osascript", "-e", f"set volume output volume {percent}"], capture_output=True)
            elif self.system == "windows":
                # nircmd if available, otherwise key presses
                if shutil.which("nircmd"):
                    subprocess.run(["nircmd", "setsysvolume", str(int(percent / 100 * 65535))], capture_output=True)
                else:
                    return self._windows_volume_keys(percent)
            else:
                if shutil.which("pactl"):
                    subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{percent}%"], capture_output=True)
                elif shutil.which("amixer"):
                    subprocess.run(["amixer", "-q", "sset", "Master", f"{percent}%"], capture_output=True)
                else:
                    return "No volume control utility found (install pactl or amixer)."
        except Exception as exc:  # noqa: BLE001
            return f"Could not set the volume: {exc}"
        self._record("set_volume", {"percent": percent})
        return f"Set the volume to {percent}%."

    def _windows_volume_keys(self, percent: int) -> str:
        gui = self.gui
        gui.press("volumemute")
        gui.press("volumemute")
        presses = max(0, min(50, percent // 2))
        for _ in range(presses):
            gui.press("volumeup")
        return f"Set the volume to about {percent}% using media keys."

    def change_volume(self, delta: int) -> str:
        key = "volumeup" if delta > 0 else "volumedown"
        for _ in range(min(abs(int(delta)) // 2 or 1, 25)):
            self.gui.press(key)
        self._record("change_volume", {"delta": delta})
        return f"Adjusted the volume by {delta:+d}."

    def set_brightness(self, percent: int) -> str:
        percent = max(0, min(100, int(percent)))
        try:
            if self.system == "darwin":
                subprocess.run(["brightness", "-l", str(percent / 100)], capture_output=True)
                return f"Set the brightness to {percent}%."
            import screen_brightness_control as sbc  # type: ignore

            sbc.set_brightness(percent)
        except Exception:
            if self.system == "linux" and shutil.which("brightnessctl"):
                subprocess.run(["brightnessctl", "set", f"{percent}%"], capture_output=True)
                return f"Set the brightness to {percent}%."
            return "Brightness control is unavailable (install `screen-brightness-control`)."
        self._record("set_brightness", {"percent": percent})
        return f"Set the brightness to {percent}%."

    def media_key(self, action: str) -> str:
        mapping = {
            "play": "playpause", "pause": "playpause", "playpause": "playpause",
            "next": "nexttrack", "previous": "prevtrack", "prev": "prevtrack",
            "stop": "stop", "mute": "volumemute",
        }
        key = mapping.get((action or "").lower())
        if not key:
            return f"Unknown media action `{action}`."
        self.gui.press(key)
        self._record("media_key", {"action": action, "key": key})
        return f"Pressed the media {action} key."

    def power(self, action: str) -> str:
        action = (action or "").lower().strip()
        commands: dict[str, list[str]] = {
            "lock": {
                "windows": ["rundll32.exe", "user32.dll,LockWorkStation"],
                "darwin": ["pmset", "displaysleepnow"],
                "linux": ["loginctl", "lock-session"],
            },
            "sleep": {
                "windows": ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"],
                "darwin": ["pmset", "sleepnow"],
                "linux": ["systemctl", "suspend"],
            },
            "shutdown": {
                "windows": ["shutdown", "/s", "/t", "5"],
                "darwin": ["osascript", "-e", 'tell app "System Events" to shut down'],
                "linux": ["shutdown", "-h", "+0"],
            },
            "restart": {
                "windows": ["shutdown", "/r", "/t", "5"],
                "darwin": ["osascript", "-e", 'tell app "System Events" to restart'],
                "linux": ["shutdown", "-r", "+0"],
            },
            "cancel_shutdown": {"windows": ["shutdown", "/a"]},
        }
        entry = commands.get(action, {}).get(self.system)
        if not entry:
            return f"No {action} command for {platform.system()}."
        if action in {"shutdown", "restart"}:
            self.notify("JARVIS", f"This computer will {action} in 5 seconds. Say “cancel shutdown” to stop it.")
            time.sleep(1.5)
        subprocess.Popen(entry)
        self._record("power", {"action": action, "command": entry})
        return f"{action.capitalize()} command issued."


def shlex_split_posix(command: str) -> list[str]:
    import shlex

    return shlex.split(command)


def shlex_split_windows(command: str) -> list[str]:
    import shlex

    try:
        return shlex.split(command, posix=False)
    except ValueError:
        return command.split()
