"""Safety layer — the thing that stands between the LLM and your computer.

Design rules implemented here:

1. The model never gets raw machine access. It only gets *named tools* with
   typed arguments. Every tool declares a :class:`~jarvis.core.types.Permission`.
2. **Level 1 (SAFE)** actions run immediately.
3. **Level 2 (CONFIRM)** actions are queued and require an explicit "yes"
   from the user (spoken or clicked in the dashboard) before they execute.
4. **Level 3 (FORBIDDEN)** operations are never registered as tools, and even
   if the model tries to smuggle one through a string argument (a shell
   command, a path, a URL) the guards below reject it.
5. Every decision — allowed, blocked, confirmed — is written to the audit log.
"""

from __future__ import annotations

import fnmatch
import re
import shlex
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Iterable

from .types import Permission, ToolCall


# ---------------------------------------------------------------------------
# Level 3 — hard deny lists. These are patterns that must never execute,
# regardless of what the model or a user asks for.
# ---------------------------------------------------------------------------

FORBIDDEN_COMMAND_PATTERNS: tuple[str, ...] = (
    r"\brm\s+(-[a-z]*[rf][a-z]*\s+)+(/|~|\$HOME|\*|\.)(\s|$)",
    r"\brm\s+-[a-z]*[rf][a-z]*\s+/",
    r"\bmkfs(\.|\s)",
    r"\bdd\s+if=.*\bof=/dev/",
    r":\(\)\s*\{\s*:\|\s*:\s*&\s*\}\s*;",           # fork bomb
    r"\bformat\s+[a-z]:",
    r"\bdiskpart\b",
    r"\bshutdown\s+/[a-z]*\b.*\b/p?\b.*\bf\b",
    r"\bdel\s+/[sfq]{2,}\s+[a-z]:\\",
    r"\brmdir\s+/s\s+/q\s+[a-z]:\\$",
    r"\breg\s+delete\b",
    r"\bchmod\s+(-[rR]\s+)?777\s+/",
    r"\bchown\s+-[rR]\s+.*\s+/",
    r">\s*/dev/sd[a-z]",
    r"\bshutdown\s+now\b",                            # use the tool instead
    r"\bsystemctl\s+(disable|stop)\s+(firewalld|ufw|apparmor|windows\s+defender)",
    r"\bsetenforce\s+0\b",
    r"\bnetsh\s+advfirewall\s+set\s+.*state\s+off",
    r"\bDisable-NetFirewallProfile\b",
    r"\bpkill\s+-9\s+-1\b",
    r"\bkillall\s+-9\b",
    r"\btasks?kill\s+/im\s+(wininit|csrss|services|svchost)\b",
    r"\bsc\s+delete\b",
    r"\bcurl\b.*\|\s*(ba)?sh\b",                      # pipe-to-shell installs
    r"\bwget\b.*\|\s*(ba)?sh\b",
    r"\biex\s*\(.*\bNew-Object\b.*\bNet\.WebClient",
    r"\bpowershell\b.*-e(nc(odedcommand)?)?\s+[A-Za-z0-9+/=]{40,}",
    r"\bnc\b.*-e\s*/bin/",
    r"\bcrypt(32)?\b.*\bransom",
)

FORBIDDEN_PATH_PATTERNS: tuple[str, ...] = (
    r"^/etc/(passwd|shadow|sudoers|ssh)",
    r"^/boot/",
    r"^/proc/",
    r"^/sys/",
    r"^/dev/",
    r"^/usr/(bin|sbin|lib)/",
    r"^/var/lib/",
    r"^~?/\.ssh/",
    r"^~?/\.aws/",
    r"^~?/\.gnupg/",
    r"^[a-z]:/windows/(system32|syswow64|win32)",
    r"/windows/system32/",
    r"^[a-z]:/boot",
    r"^/system/",
    r"^/library/",
)

#: Shell commands the agent may run *without* asking (still sandboxed).
SHELL_ALLOWLIST: frozenset[str] = frozenset(
    {
        "ls", "dir", "pwd", "cd", "echo", "cat", "type", "head", "tail",
        "wc", "find", "grep", "rg", "which", "where", "whoami", "date",
        "time", "uptime", "df", "du", "free", "ps", "top", "htop", "tree",
        "git", "python", "python3", "pip", "pip3", "node", "npm", "npx",
        "pytest", "make", "cargo", "go", "java", "mvn", "docker", "kubectl",
        "curl", "wget", "ping", "nslookup", "dig", "ip", "ifconfig", "uname",
        "hostname", "env", "printenv", "sort", "uniq", "cut", "awk", "sed",
        "mkdir", "touch", "cp", "mv", "code", "start", "open", "xdg-open",
    }
)

#: Even allowlisted commands become Level 2 when they carry these sub-commands.
SHELL_CONFIRM_SUBCOMMANDS: dict[str, tuple[str, ...]] = {
    "git": ("push", "reset", "clean", "rebase", "checkout", "branch -D", "filter-branch"),
    "pip": ("uninstall",),
    "pip3": ("uninstall",),
    "npm": ("uninstall", "publish"),
    "docker": ("rm", "rmi", "system prune", "stop", "kill"),
    "kubectl": ("delete", "apply", "scale"),
    "rm": (),      # always confirm
    "mv": (),      # always confirm
    "sed": ("-i",),
    "make": ("clean", "install"),
}

DESTRUCTIVE_BINARIES: frozenset[str] = frozenset(
    {"rm", "rmdir", "del", "erase", "mkfs", "dd", "shred", "format", "diskpart",
     "shutdown", "reboot", "halt", "poweroff", "kill", "killall", "pkill", "taskkill"}
)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class SafetyDecision:
    allowed: bool
    permission: Permission
    reason: str = ""
    #: human-readable sentence shown to the user before a Level-2 action
    prompt: str = ""
    blocked: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "permission": self.permission.value,
            "reason": self.reason,
            "prompt": self.prompt,
            "blocked": self.blocked,
        }


def allow(permission: Permission = Permission.SAFE, reason: str = "") -> SafetyDecision:
    return SafetyDecision(True, permission, reason)


def confirm(permission: Permission, prompt: str, reason: str = "") -> SafetyDecision:
    return SafetyDecision(False, permission, reason=reason, prompt=prompt)


def block(reason: str) -> SafetyDecision:
    return SafetyDecision(False, Permission.FORBIDDEN, reason=reason, blocked=True)


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


def is_forbidden_command(command: str) -> str | None:
    """Return the offending pattern if `command` is a Level-3 operation."""
    lowered = (command or "").lower().strip()
    if not lowered:
        return None
    for pattern in FORBIDDEN_COMMAND_PATTERNS:
        if re.search(pattern, lowered):
            return pattern
    return None


def is_forbidden_path(path: str) -> bool:
    """True when the path points at an OS-critical location."""
    if not path:
        return False
    normalised = str(path).strip().replace("\\", "/").lower()
    for pattern in FORBIDDEN_PATH_PATTERNS:
        if re.search(pattern, normalised):
            return True
    return False


def _command_parts(command: str) -> list[str]:
    try:
        return shlex.split(command, posix=True)
    except ValueError:
        return command.split()


def check_shell_command(command: str, *, require_confirmation: bool = True) -> SafetyDecision:
    """Guard the *controlled* terminal tool.

    Never allow arbitrary shell. The command must start with an allow-listed
    binary, must not contain a Level-3 pattern, and destructive variants
    escalate to Level 2 (confirmation).
    """
    command = (command or "").strip()
    if not command:
        return block("Empty command.")

    forbidden = is_forbidden_command(command)
    if forbidden:
        return block(f"Command matches a Level-3 deny pattern ({forbidden}).")

    # chained commands are evaluated piece by piece
    segments = re.split(r"&&|\|\||;|\|", command)
    worst = Permission.SAFE
    for segment in segments:
        parts = _command_parts(segment.strip())
        if not parts:
            continue
        binary = Path(parts[0]).name.lower().replace(".exe", "")
        if binary not in SHELL_ALLOWLIST:
            return block(
                f"`{binary}` is not on the JARVIS shell allow-list. "
                "Add a dedicated tool for it instead of running raw shell."
            )
        if binary in DESTRUCTIVE_BINARIES:
            worst = Permission.CONFIRM
        for needle in SHELL_CONFIRM_SUBCOMMANDS.get(binary, ()):
            if needle in segment.lower():
                worst = Permission.CONFIRM

    if worst is Permission.CONFIRM and require_confirmation:
        return confirm(
            Permission.CONFIRM,
            f"Run this command on your machine?\n\n    {command}",
            reason="Potentially destructive shell command",
        )
    return allow(Permission.SAFE, "allow-listed command")


def _workspace_roots(workspace: Iterable[str] | str | None) -> list[str]:
    """Normalise a workspace argument into resolved absolute roots.

    A bare string is treated as one root rather than being exploded into
    characters, which is the kind of mistake that silently disables a guard.
    """
    if not workspace:
        return []
    if isinstance(workspace, str):
        workspace = [workspace]
    roots: list[str] = []
    for entry in workspace:
        if not entry:
            continue
        try:
            resolved = str(Path(entry).expanduser().resolve())
        except Exception:
            resolved = str(entry)
        if resolved not in roots:
            roots.append(resolved)
    return roots


def check_path(target: str, operation: str, workspace: Iterable[str] | str = ()) -> SafetyDecision:
    """Guard file operations: reads are free, writes/deletes need care."""
    if not target:
        return block("No path supplied.")
    if is_forbidden_path(target):
        return block(f"`{target}` is inside a protected system location.")

    op = operation.lower()
    if op in {"read", "stat", "search", "find"}:
        return allow(Permission.SAFE, "read-only file access")

    if op in {"delete", "remove", "trash"}:
        return confirm(
            Permission.CONFIRM,
            f"This will permanently delete `{target}`. Do you want me to continue?",
            reason="destructive file operation",
        )

    if op in {"write", "create", "move", "rename", "edit"}:
        allowed_roots = _workspace_roots(workspace)
        if allowed_roots:
            try:
                resolved = str(Path(target).expanduser().resolve())
            except Exception:
                resolved = target
            if not any(resolved == root or resolved.startswith(root.rstrip("/") + "/") for root in allowed_roots):
                return confirm(
                    Permission.CONFIRM,
                    f"`{target}` is outside your JARVIS workspace. Write there anyway?",
                    reason="write outside workspace",
                )
        return allow(Permission.SAFE, "write inside workspace")

    return allow(Permission.SAFE, "generic file access")


def check_url(url: str) -> SafetyDecision:
    """Block obvious local-network / credential exfiltration targets."""
    lowered = (url or "").lower().strip()
    if not lowered:
        return block("No URL supplied.")
    if not re.match(r"^[a-z][a-z0-9+.-]*://", lowered) and not lowered.startswith(("www.", "localhost")):
        lowered = "https://" + lowered

    host = re.sub(r"^[a-z0-9+.-]+://", "", lowered).split("/")[0].split("?")[0]
    host_only = host.split(":")[0]

    if host_only in {"169.254.169.254", "metadata.google.internal"}:
        return block("Cloud metadata endpoints are not reachable by JARVIS.")
    if re.match(r"^127\.", host_only) or host_only in {"localhost", "0.0.0.0", "[::1]"}:
        return allow(Permission.SAFE, "loopback (local dev server)")
    if re.match(r"^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)", host_only):
        return confirm(
            Permission.CONFIRM,
            f"`{host_only}` is on your local network. Open it?",
            reason="private network address",
        )
    if lowered.startswith(("file://", "ftp://")):
        return confirm(Permission.CONFIRM, f"Open `{url}`?", reason="non-http scheme")
    return allow(Permission.SAFE, "public URL")


def check_application(app: str, operation: str = "open") -> SafetyDecision:
    """Opening apps is Level 1; killing them and power actions are Level 2."""
    if not app:
        return block("No application supplied.")
    name = app.lower().strip()
    if operation in {"close", "kill", "terminate", "force"}:
        protected = {"explorer", "finder", "system", "registry", "taskmgr", "activity monitor",
                     "kernel", "init", "systemd", "dwm", "csrss", "services"}
        if any(p in name for p in protected):
            return block(f"`{app}` is a protected system process.")
        return confirm(
            Permission.CONFIRM,
            f"Close `{app}`? Any unsaved work in it will be lost.",
            reason="terminating an application",
        )
    return allow(Permission.SAFE, "launching an application")


POWER_ACTIONS = {
    "shutdown": ("Shut down this computer now?", Permission.CONFIRM),
    "restart": ("Restart this computer now?", Permission.CONFIRM),
    "reboot": ("Restart this computer now?", Permission.CONFIRM),
    "sleep": ("Put this computer to sleep?", Permission.SAFE),
    "hibernate": ("Hibernate this computer?", Permission.CONFIRM),
    "lock": ("Lock the screen?", Permission.SAFE),
    "logout": ("Log out of your account?", Permission.CONFIRM),
}


def check_power(action: str) -> SafetyDecision:
    entry = POWER_ACTIONS.get((action or "").lower().strip())
    if not entry:
        return block(f"Unknown power action `{action}`.")
    prompt, permission = entry
    if permission is Permission.SAFE:
        return allow(Permission.SAFE, "reversible power action")
    return confirm(permission, prompt, reason="power action")


def check_outbound(destination: str, action: str) -> SafetyDecision:
    """Sending data off the machine (email, message, upload) is always Level 2."""
    return confirm(
        Permission.CONFIRM,
        f"{action} to `{destination}`. Send it?",
        reason="outbound communication",
    )


# ---------------------------------------------------------------------------
# The gatekeeper
# ---------------------------------------------------------------------------


class SafetyGate:
    """Decides what may run, and escalates/denies what may not.

    The gate has no knowledge of *how* tools work — it only inspects the
    tool name, the declared permission and the arguments. That keeps the
    LLM at arm's length from the operating system.
    """

    #: argument keys whose string values are inspected for deny patterns
    _STRING_ARG_KEYS = ("command", "cmd", "script", "path", "url", "target", "query")

    def __init__(
        self,
        mode: str = "normal",
        workspace: Iterable[str] = (),
        require_shell_confirmation: bool = True,
    ) -> None:
        self.mode = mode.lower()
        self.workspace = tuple([workspace] if isinstance(workspace, str) else workspace)
        self.require_shell_confirmation = require_shell_confirmation
        self.blocked_count = 0
        self.confirmed_count = 0

    # ------------------------------------------------------------------
    def evaluate(self, call: ToolCall, declared: Permission) -> SafetyDecision:
        """Run every relevant guard and return the strictest verdict."""
        name = call.name.lower()
        args = call.arguments or {}

        # 0. hard-deny anything marked forbidden
        if declared is Permission.FORBIDDEN:
            self.blocked_count += 1
            return block(f"`{call.name}` is a Level-3 tool and is not available to the agent.")

        # 1. argument-level scanning (catches smuggling through strings)
        for key, value in args.items():
            if not isinstance(value, str):
                continue
            if key in {"command", "cmd", "script"}:
                forbidden = is_forbidden_command(value)
                if forbidden:
                    self.blocked_count += 1
                    return block(f"Blocked `{key}`: matches deny pattern `{forbidden}`.")
            if key in {"path", "target", "file", "filename", "directory"}:
                if is_forbidden_path(value):
                    self.blocked_count += 1
                    return block(f"Blocked access to protected path `{value}`.")

        # 2. tool-specific escalation
        decision = self._evaluate_tool(name, args)
        if decision.blocked:
            self.blocked_count += 1
            return decision

        # 3. combine with the declared permission (strictest wins)
        order = {Permission.SAFE: 0, Permission.CONFIRM: 1, Permission.FORBIDDEN: 2}
        final_permission = max((decision.permission, declared), key=lambda p: order[p])

        if final_permission is Permission.CONFIRM:
            if self.mode == "yolo":
                return allow(Permission.SAFE, "confirmation skipped (safety mode = yolo)")
            prompt = decision.prompt or self._default_prompt(call)
            return confirm(Permission.CONFIRM, prompt, decision.reason or "requires confirmation")

        return allow(Permission.SAFE, decision.reason or "safe action")

    # ------------------------------------------------------------------
    def _evaluate_tool(self, name: str, args: dict[str, Any]) -> SafetyDecision:
        if "shell" in name or "command" in name or "terminal" in name:
            return check_shell_command(
                str(args.get("command", "")), require_confirmation=self.require_shell_confirmation
            )
        if name in {"shutdown", "restart_computer", "sleep_computer", "lock_screen", "logout", "power"}:
            return check_power(str(args.get("action", name.split("_")[0])))
        if name in {"delete_file", "remove_file", "trash_file"}:
            return check_path(str(args.get("path", "")), "delete")
        if name in {"write_file", "create_file", "save_file", "append_file"}:
            return check_path(str(args.get("path", "")), "write", self.workspace)
        if name in {"move_file", "rename_file"}:
            decision = check_path(str(args.get("source", args.get("path", ""))), "write", self.workspace)
            if decision.blocked:
                return decision
            return check_path(str(args.get("destination", "")), "write", self.workspace)
        if name in {"open_file", "read_file", "find_file", "search_files", "list_directory"}:
            return check_path(str(args.get("path", args.get("query", ""))), "read")
        if name in {"open_url", "navigate", "open_website"}:
            return check_url(str(args.get("url", "")))
        if name in {"close_application", "kill_application", "terminate_application"}:
            return check_application(str(args.get("application", "")), "close")
        if name in {"open_application", "launch_application"}:
            return check_application(str(args.get("application", "")), "open")
        if name in {"send_email", "send_message", "send_sms", "post_message"}:
            return check_outbound(str(args.get("to", "the recipient")), name.replace("_", " ").capitalize())
        if name in {"install_software", "install_package"}:
            return confirm(
                Permission.CONFIRM,
                f"Install `{args.get('package', 'software')}` on this machine?",
                reason="software installation",
            )
        return allow(Permission.SAFE, "")

    # ------------------------------------------------------------------
    @staticmethod
    def _default_prompt(call: ToolCall) -> str:
        rendered = ", ".join(f"{k}={v!r}" for k, v in (call.arguments or {}).items())
        title = call.name.replace("_", " ")
        return f"I need your OK to **{title}**{f' ({rendered})' if rendered else ''}. Continue?"


# ---------------------------------------------------------------------------
# Loop guard — stops the agent from thrashing when it gets stuck
# ---------------------------------------------------------------------------


class LoopGuard:
    """Detects repeated identical actions inside one turn."""

    def __init__(self, max_repeats: int = 2) -> None:
        self.max_repeats = max_repeats
        self._seen: dict[str, int] = {}

    def reset(self) -> None:
        self._seen.clear()

    def register(self, call: ToolCall) -> tuple[bool, str]:
        key = f"{call.name}:{sorted((call.arguments or {}).items())}"
        self._seen[key] = self._seen.get(key, 0) + 1
        if self._seen[key] > self.max_repeats:
            return False, (
                f"`{call.name}` has already been attempted {self._seen[key] - 1} times with the "
                "same arguments. Stop retrying and report the problem instead."
            )
        return True, ""


def matches_wildcard(value: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch.fnmatch(value.lower(), p.lower()) for p in patterns)


def safe_join(base: str | Path, *parts: str) -> Path:
    """Join without letting `..` escape the base directory."""
    base_path = Path(base).expanduser().resolve()
    candidate = (base_path.joinpath(*parts)).resolve()
    try:
        candidate.relative_to(base_path)
    except ValueError as exc:
        raise PermissionError(f"{candidate} escapes {base_path}") from exc
    return candidate


def normalise_windows_path(path: str) -> str:
    """Best-effort display normalisation for cross-platform paths."""
    try:
        return str(PureWindowsPath(path)) if "\\" in path else str(PurePosixPath(path))
    except Exception:
        return path
