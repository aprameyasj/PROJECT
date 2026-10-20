"""System tools: status, time, notifications, power and the *controlled* shell.

`run_command` is the only tool that touches a shell, and it is fenced three
times over: the safety gate rejects Level-3 patterns, the command must begin
with an allow-listed binary, and anything destructive escalates to a user
confirmation. There is deliberately no "execute any terminal command" tool.
"""

from __future__ import annotations

import platform
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from ..core.timeparse import humanize_duration
from ..core.types import Permission, ToolCategory, ToolResult
from .registry import ToolContext, tool


@tool(permission=Permission.SAFE, category=ToolCategory.SYSTEM, examples=["What time is it?"])
def get_time() -> ToolResult:
    """Report the current local time."""
    now = datetime.now()
    return ToolResult(ok=True, message=now.strftime("%H:%M:%S"), speech=f"It's {now.strftime('%I:%M %p').lstrip('0')}.",
                      data={"time": now.strftime("%H:%M:%S"), "iso": now.isoformat(timespec="seconds"),
                            "hour": now.hour, "minute": now.minute})


@tool(permission=Permission.SAFE, category=ToolCategory.SYSTEM)
def get_date() -> ToolResult:
    """Report today's date and weekday."""
    now = datetime.now()
    return ToolResult(ok=True, message=now.strftime("%A, %d %B %Y"),
                      speech=f"Today is {now.strftime('%A, the %d of %B, %Y')}.",
                      data={"date": now.strftime("%Y-%m-%d"), "weekday": now.strftime("%A"),
                            "iso": now.isoformat(timespec="seconds")})


@tool(
    permission=Permission.SAFE,
    category=ToolCategory.SYSTEM,
    examples=["What's my system status?", "How's my battery?"],
)
def get_system_status(ctx: ToolContext) -> ToolResult:
    """Report CPU, memory, disk, battery, screen, volume and running apps."""
    status = ctx.backend.system_status()
    parts = []
    if "cpu_percent" in status:
        parts.append(f"CPU {status['cpu_percent']}%")
    if "memory_percent" in status:
        parts.append(f"memory {status['memory_percent']}%")
    if "disk_percent" in status:
        parts.append(f"disk {status['disk_percent']}%")
    if status.get("battery_percent") is not None:
        parts.append(f"battery {status['battery_percent']}%")
    speech = "Your computer is " + ", ".join(parts) + "." if parts else "Everything looks normal."

    uptime = _uptime()
    if uptime:
        status["uptime"] = humanize_duration(uptime)
    status["agent"] = ctx.settings.describe()["agent_name"]
    return ToolResult(ok=True, message="; ".join(parts) or "System status collected", speech=speech,
                      data=status, simulated=bool(status.get("simulated")))


def _uptime() -> timedelta | None:
    try:
        import psutil  # type: ignore

        return timedelta(seconds=int(datetime.now().timestamp() - psutil.boot_time()))
    except Exception:  # noqa: BLE001
        return None


@tool(permission=Permission.SAFE, category=ToolCategory.SYSTEM)
def notify_user(ctx: ToolContext, title: str, body: str, speak: bool = False) -> ToolResult:
    """Show a desktop notification (and optionally speak it).

    Args:
        title: Notification title.
        body: Notification text.
        speak: Also say the message out loud.
    """
    message = ctx.backend.notify(title, body)
    return ToolResult(ok=True, message=message, speech=body if speak else "",
                      data={"title": title, "body": body}, simulated=ctx.backend.simulated)


@tool(permission=Permission.CONFIRM, category=ToolCategory.SYSTEM, mutating=True,
      examples=["Jarvis, shut down.", "Lock my screen"])
def power(ctx: ToolContext, action: Literal["shutdown", "restart", "sleep", "hibernate", "lock",
                                             "logout", "cancel_shutdown"] = "shutdown",
          delay_seconds: int = 5) -> ToolResult:
    """Shut down, restart, sleep or lock the computer.

    Args:
        action: One of shutdown, restart, sleep, hibernate, lock, logout, cancel_shutdown.
        delay_seconds: Grace period before a shutdown/restart happens (cancel with cancel_shutdown).
    """
    message = ctx.backend.power(action)
    speech = {
        "shutdown": "Shutting down. Say “cancel shutdown” within five seconds to stop it.",
        "restart": "Restarting now.",
        "sleep": "Going to sleep.",
        "lock": "Locked.",
        "cancel_shutdown": "Shutdown cancelled.",
    }.get(action, f"{action.title()} issued.")
    return ToolResult(ok=True, message=message, speech=speech,
                      data={"action": action, "delay": delay_seconds}, simulated=ctx.backend.simulated)


@tool(permission=Permission.SAFE, category=ToolCategory.SYSTEM)
def lock_screen(ctx: ToolContext) -> ToolResult:
    """Lock the screen immediately (no confirmation needed — it is reversible)."""
    message = ctx.backend.power("lock")
    return ToolResult(ok=True, message=message, speech="Locking your screen.",
                      simulated=ctx.backend.simulated)


@tool(permission=Permission.SAFE, category=ToolCategory.SYSTEM, mutating=True)
def run_command(ctx: ToolContext, command: str, cwd: str = "", timeout: int = 30) -> ToolResult:
    """Run an allow-listed shell command (read-only by default). Destructive commands need confirmation.

    Args:
        command: The command to run, e.g. "git status" or "python --version".
        cwd: Working directory (defaults to the JARVIS workspace).
        timeout: Maximum seconds to wait (1-600).
    """
    if not command.strip():
        return ToolResult(ok=False, message="No command supplied.")
    working = cwd.strip() or str(Path(ctx.settings.home) / "workspace")
    Path(working).mkdir(parents=True, exist_ok=True)
    result = ctx.backend.shell(command, cwd=working,
                               timeout=min(max(int(timeout), 1), int(getattr(ctx.settings, "max_shell_seconds", 60))))
    ok = result.get("ok", result.get("returncode", 0) == 0)
    stdout = (result.get("stdout") or "").strip()
    stderr = (result.get("stderr") or "").strip()
    summary = stdout.splitlines()[-1][:200] if stdout else (stderr[:200] if stderr else "no output")
    return ToolResult(
        ok=bool(ok),
        message=f"`{command}` → exit {result.get('returncode', 0)}: {summary}",
        speech="Command finished." if ok else "That command failed.",
        data=result,
        simulated=bool(result.get("simulated")),
        error=stderr[:400] if not ok else None,
    )


@tool(permission=Permission.SAFE, category=ToolCategory.SYSTEM)
def run_project(ctx: ToolContext, project: str, command: Literal["start", "install", "test", "build"] = "start",
                cwd: str = "") -> ToolResult:
    """Run a known project task (npm start / pytest / build) inside a project folder.

    Args:
        project: Project folder name, e.g. "smart-water-grid".
        command: What to run: start, install, test or build.
        cwd: Optional explicit project path (overrides `project`).
    """
    root = Path(cwd).expanduser() if cwd else None
    if root is None:
        candidates = [Path.home() / "Projects" / project, Path.home() / "Code" / project, Path.home() / project,
                      Path.cwd() / project]
        root = next((c for c in candidates if c.exists()), candidates[0])
    if not root.exists():
        return ToolResult(ok=False, message=f"I can't find a project folder called `{project}`.",
                          speech=f"I couldn't find the {project} project.",
                          data={"searched": str(root)})

    has_npm = (root / "package.json").exists()
    has_pyproject = (root / "pyproject.toml").exists() or (root / "requirements.txt").exists()
    has_make = (root / "Makefile").exists()

    scripts = {
        "start": ["npm run dev" if has_npm else "python manage.py runserver" if (root / "manage.py").exists()
                  else "make run" if has_make else "python main.py"],
        "install": ["npm install" if has_npm else "pip install -r requirements.txt" if (root / "requirements.txt").exists()
                    else "pip install -e ."],
        "test": ["npm test" if has_npm else "pytest"],
        "build": ["npm run build" if has_npm else "make build" if has_make else "python -m build"],
    }[command]

    script = scripts[0]
    info = ctx.backend.shell(script, cwd=str(root), timeout=int(getattr(ctx.settings, "max_shell_seconds", 60)))
    return ToolResult(
        ok=bool(info.get("ok", info.get("returncode", 0) == 0)),
        message=f"`{script}` in {root.name}",
        speech=f"Running {command} for {project}.",
        data={"project": project, "path": str(root), "command": script, **info},
        simulated=bool(info.get("simulated")),
    )


@tool(permission=Permission.SAFE, category=ToolCategory.SYSTEM)
def environment_info(ctx: ToolContext) -> ToolResult:
    """Report JARVIS's own runtime: platform, backend, model, database and tool count."""
    describe = ctx.settings.describe()
    backend = ctx.backend.describe()
    stats = ctx.db.stats() if ctx.db is not None else {}
    data: dict[str, Any] = {
        "platform": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "python": platform.python_version(),
        "backend": backend,
        "llm": describe,
        "database": stats,
        "registry": ctx.service("registry_stats", {}),
    }
    return ToolResult(ok=True, message="Runtime information collected",
                      speech=f"I'm running on {platform.system()} with the {backend.get('name')} backend.",
                      data=data)
