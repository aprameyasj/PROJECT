"""Cross-platform application launcher map.

Keep this data-only so it is trivial to extend: add an alias, and
"Jarvis, open <alias>" starts working immediately.
"""

from __future__ import annotations

import platform
import shutil
from typing import Any

#: canonical app id -> {platform: [candidate commands]}
APPLICATIONS: dict[str, dict[str, list[str]]] = {
    "vscode": {
        "windows": ["code", "code.cmd"],
        "darwin": ["open -a 'Visual Studio Code'", "code"],
        "linux": ["code", "codium", "code-insiders"],
    },
    "cursor": {
        "windows": ["cursor"],
        "darwin": ["open -a Cursor", "cursor"],
        "linux": ["cursor"],
    },
    "chrome": {
        "windows": ["start chrome", "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"],
        "darwin": ["open -a 'Google Chrome'"],
        "linux": ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser"],
    },
    "firefox": {
        "windows": ["start firefox"],
        "darwin": ["open -a Firefox"],
        "linux": ["firefox"],
    },
    "edge": {
        "windows": ["start msedge"],
        "darwin": ["open -a 'Microsoft Edge'"],
        "linux": ["microsoft-edge"],
    },
    "safari": {
        "darwin": ["open -a Safari"],
    },
    "terminal": {
        "windows": ["wt", "start cmd"],
        "darwin": ["open -a Terminal"],
        "linux": ["gnome-terminal", "konsole", "xterm", "alacritty", "kitty"],
    },
    "file_manager": {
        "windows": ["explorer"],
        "darwin": ["open -a Finder"],
        "linux": ["nautilus", "dolphin", "thunar", "nemo"],
    },
    "spotify": {
        "windows": ["start spotify:"],
        "darwin": ["open -a Spotify"],
        "linux": ["spotify"],
    },
    "slack": {
        "windows": ["start slack:"],
        "darwin": ["open -a Slack"],
        "linux": ["slack"],
    },
    "discord": {
        "windows": ["start discord"],
        "darwin": ["open -a Discord"],
        "linux": ["discord"],
    },
    "notion": {
        "darwin": ["open -a Notion"],
        "windows": ["start notion:"],
        "linux": ["notion-desktop"],
    },
    "word": {"windows": ["start winword"], "darwin": ["open -a 'Microsoft Word'"]},
    "excel": {"windows": ["start excel"], "darwin": ["open -a 'Microsoft Excel'"]},
    "powerpoint": {"windows": ["start powerpnt"], "darwin": ["open -a 'Microsoft PowerPoint'"]},
    "calculator": {
        "windows": ["calc"],
        "darwin": ["open -a Calculator"],
        "linux": ["gnome-calculator", "kcalc"],
    },
    "notepad": {
        "windows": ["notepad"],
        "darwin": ["open -a TextEdit"],
        "linux": ["gedit", "kate", "mousepad", "xed"],
    },
    "paint": {"windows": ["mspaint"], "darwin": ["open -a Preview"]},
    "task_manager": {"windows": ["taskmgr"], "darwin": ["open -a 'Activity Monitor'"], "linux": ["gnome-system-monitor"]},
    "settings": {
        "windows": ["start ms-settings:"],
        "darwin": ["open -a 'System Settings'"],
        "linux": ["gnome-control-center"],
    },
    "photos": {"windows": ["start ms-photos:"], "darwin": ["open -a Photos"], "linux": ["eog", "gwenview"]},
    "whatsapp": {"windows": ["start whatsapp:"], "darwin": ["open -a WhatsApp"], "linux": ["whatsapp-for-linux"]},
    "zoom": {"windows": ["start zoom"], "darwin": ["open -a zoom.us"], "linux": ["zoom"]},
    "obs": {"windows": ["start obs64"], "darwin": ["open -a OBS"], "linux": ["obs"]},
    "blender": {"windows": ["start blender"], "darwin": ["open -a Blender"], "linux": ["blender"]},
    "postman": {"windows": ["start postman"], "darwin": ["open -a Postman"], "linux": ["postman"]},
    "docker": {"windows": ["start docker-desktop"], "darwin": ["open -a Docker"], "linux": ["docker-desktop"]},
    "android_studio": {"windows": ["start studio64"], "darwin": ["open -a 'Android Studio'"], "linux": ["android-studio"]},
    "pycharm": {"windows": ["start pycharm64"], "darwin": ["open -a PyCharm"], "linux": ["pycharm"]},
    "intellij": {"windows": ["start idea64"], "darwin": ["open -a IntelliJ IDEA"], "linux": ["idea"]},
    "vlc": {"windows": ["start vlc"], "darwin": ["open -a VLC"], "linux": ["vlc"]},
    "steam": {"windows": ["start steam:"], "darwin": ["open -a Steam"], "linux": ["steam"]},
}

#: spoken / typed aliases -> canonical id
ALIASES: dict[str, str] = {
    "vs code": "vscode",
    "vscode": "vscode",
    "visual studio code": "vscode",
    "code": "vscode",
    "editor": "vscode",
    "ide": "vscode",
    "chrome": "chrome",
    "google chrome": "chrome",
    "browser": "chrome",
    "firefox": "firefox",
    "mozilla": "firefox",
    "edge": "edge",
    "microsoft edge": "edge",
    "safari": "safari",
    "terminal": "terminal",
    "command prompt": "terminal",
    "cmd": "terminal",
    "powershell": "terminal",
    "windows terminal": "terminal",
    "wt": "terminal",
    "shell": "terminal",
    "console": "terminal",
    "explorer": "file_manager",
    "file explorer": "file_manager",
    "files": "file_manager",
    "finder": "file_manager",
    "nautilus": "file_manager",
    "calculator": "calculator",
    "calc": "calculator",
    "notepad": "notepad",
    "text editor": "notepad",
    "settings": "settings",
    "system settings": "settings",
    "preferences": "settings",
    "task manager": "task_manager",
    "activity monitor": "task_manager",
    "word": "word",
    "ms word": "word",
    "excel": "excel",
    "spreadsheets": "excel",
    "powerpoint": "powerpoint",
    "slides": "powerpoint",
    "spotify": "spotify",
    "music": "spotify",
    "slack": "slack",
    "discord": "discord",
    "notion": "notion",
    "zoom": "zoom",
    "postman": "postman",
    "docker": "docker",
    "docker desktop": "docker",
    "pycharm": "pycharm",
    "intellij": "intellij",
    "idea": "intellij",
    "android studio": "android_studio",
    "blender": "blender",
    "obs": "obs",
    "vlc": "vlc",
    "media player": "vlc",
    "paint": "paint",
    "photos": "photos",
    "gallery": "photos",
    "whatsapp": "whatsapp",
    "steam": "steam",
    "cursor": "cursor",
}

#: process names used when terminating an app (best-effort per platform)
PROCESS_NAMES: dict[str, dict[str, str]] = {
    "vscode": {"windows": "Code.exe", "darwin": "Visual Studio Code", "linux": "code"},
    "chrome": {"windows": "chrome.exe", "darwin": "Google Chrome", "linux": "chrome"},
    "firefox": {"windows": "firefox.exe", "darwin": "firefox", "linux": "firefox"},
    "edge": {"windows": "msedge.exe", "darwin": "Microsoft Edge", "linux": "msedge"},
    "terminal": {"windows": "WindowsTerminal.exe", "darwin": "Terminal", "linux": "gnome-terminal-server"},
    "spotify": {"windows": "Spotify.exe", "darwin": "Spotify", "linux": "spotify"},
    "slack": {"windows": "slack.exe", "darwin": "Slack", "linux": "slack"},
    "discord": {"windows": "Discord.exe", "darwin": "Discord", "linux": "discord"},
    "notepad": {"windows": "notepad.exe", "darwin": "TextEdit", "linux": "gedit"},
    "calculator": {"windows": "CalculatorApp.exe", "darwin": "Calculator", "linux": "gnome-calculator"},
}


def _system() -> str:
    return platform.system().lower()


def canonicalise(application: str) -> str:
    """`"VS Code"` -> `"vscode"`; unknown names are slugified."""
    if not application:
        return ""
    text = application.strip().lower().replace("_", " ").replace("-", " ").replace(".app", "")
    text = text.replace("the ", "").strip()
    if text in ALIASES:
        return ALIASES[text]
    compact = text.replace(" ", "")
    if compact in ALIASES:
        return ALIASES[compact]
    if compact in APPLICATIONS:
        return compact
    for alias, canonical in ALIASES.items():
        if alias in text or text in alias:
            return canonical
    return compact or text


def known_applications() -> list[str]:
    return sorted(APPLICATIONS)


def launch_command(application: str) -> tuple[str | None, str]:
    """Resolve the best command for `application` on this OS.

    Returns (command_or_None, canonical_name).
    """
    canonical = canonicalise(application)
    system = _system()
    entry = APPLICATIONS.get(canonical, {})
    candidates: list[str] = list(entry.get(system, []))
    if not candidates and system == "linux":
        candidates = [canonical]
    if not candidates and system == "darwin":
        candidates = [f"open -a '{application}'"]
    if not candidates and system == "windows":
        candidates = [f"start {application}"]

    for candidate in candidates:
        if candidate.startswith(("start ", "open ")):
            return candidate, canonical
        binary = candidate.split()[0]
        if shutil.which(binary):
            return candidate, canonical
        if "\\" in binary or "/" in binary:
            return candidate, canonical
    return (candidates[0] if candidates else None), canonical


def process_name(application: str) -> str | None:
    canonical = canonicalise(application)
    return PROCESS_NAMES.get(canonical, {}).get(_system())


def describe_applications() -> list[dict[str, Any]]:
    system = _system()
    out = []
    for canonical, entry in sorted(APPLICATIONS.items()):
        cmds = entry.get(system, [])
        out.append({"id": canonical, "commands": cmds, "available": bool(cmds)})
    return out
