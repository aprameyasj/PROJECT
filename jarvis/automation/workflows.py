"""Workflows — reusable multi-step routines (the "prepare my workspace" feature).

A workflow is *data*, not code: a list of tool calls with arguments that may
reference runtime values (`{{project}}`, `{{frontend_url}}`). The planner
expands them, the executor runs them sequentially with error recovery, and the
dashboard shows the plan ticking over.

Adding a routine means appending one `Workflow(...)` — no changes to the brain.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from ..core.types import Plan, PlanStep, ToolCall


@dataclass
class Workflow:
    name: str
    title: str
    description: str
    steps: list[dict[str, Any]]
    triggers: tuple[str, ...] = ()
    #: values that can be overridden by the caller / memory
    parameters: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "steps": self.steps,
            "triggers": list(self.triggers),
            "parameters": self.parameters,
        }


WORKFLOWS: tuple[Workflow, ...] = (
    Workflow(
        name="coding_setup",
        title="Prepare the workspace for coding",
        description="Open the editor and project, start backend + frontend, then open the app in the browser.",
        triggers=("prepare my workspace", "coding setup", "set up my dev environment", "start coding",
                  "get my project ready", "dev setup", "workspace for coding"),
        parameters={"project": "", "frontend_url": "http://localhost:3000", "backend": "backend", "frontend": "frontend"},
        steps=[
            {"tool": "open_application", "arguments": {"application": "vscode"}, "why": "open the editor"},
            {"tool": "run_project", "arguments": {"project": "{{project}}", "command": "start",
                                                  "cwd": "{{backend_path}}"},
             "why": "start the backend server", "critical": False, "requires": ["project"]},
            {"tool": "run_project", "arguments": {"project": "{{project}}", "command": "start",
                                                  "cwd": "{{frontend_path}}"},
             "why": "start the frontend server", "critical": False, "requires": ["project"]},
            {"tool": "wait", "arguments": {"seconds": 3}, "why": "let the servers boot"},
            {"tool": "open_application", "arguments": {"application": "chrome"}, "why": "open the browser"},
            {"tool": "open_url", "arguments": {"url": "{{frontend_url}}"}, "why": "load the running app"},
            {"tool": "get_system_status", "arguments": {}, "why": "confirm the machine is healthy", "critical": False},
        ],
    ),
    Workflow(
        name="morning_routine",
        title="Morning briefing",
        description="Read the agenda, open the calendar and mail, and start some quiet music.",
        triggers=("good morning", "morning routine", "daily briefing", "start my day", "what's my day"),
        parameters={"music": "spotify"},
        steps=[
            {"tool": "get_agenda", "arguments": {"window": "today"}, "why": "today's reminders, events and tasks"},
            {"tool": "open_url", "arguments": {"url": "https://calendar.google.com"}, "why": "open the calendar",
             "critical": False},
            {"tool": "open_url", "arguments": {"url": "https://mail.google.com"}, "why": "open mail", "critical": False},
            {"tool": "open_application", "arguments": {"application": "{{music}}"}, "why": "background music",
             "critical": False},
        ],
    ),
    Workflow(
        name="study_setup",
        title="Focus / study mode",
        description="Open your notes, silence the machine and set a study block with a break reminder.",
        triggers=("study mode", "focus mode", "set up for studying", "deep work", "start studying"),
        parameters={"minutes": 50, "subject": ""},
        steps=[
            {"tool": "open_application", "arguments": {"application": "vscode"}, "why": "notes and code"},
            {"tool": "set_volume", "arguments": {"muted": True}, "why": "silence notifications", "critical": False},
            {"tool": "open_url", "arguments": {"url": "https://duckduckgo.com/?q={{subject}}"},
             "why": "reference material", "critical": False, "requires": ["subject"]},
            {"tool": "create_reminder",
             "arguments": {"task": "Take a 10 minute break — you've been studying {{minutes}} minutes",
                           "when": "in {{minutes}} minutes"},
             "why": "protect against burnout"},
        ],
    ),
    Workflow(
        name="meeting_prep",
        title="Meeting prep",
        description="Check the next meeting, open the call app and mute the mic.",
        triggers=("meeting prep", "prepare for my meeting", "join my meeting", "get ready for the call"),
        parameters={"app": "zoom"},
        steps=[
            {"tool": "list_calendar_events", "arguments": {"window": "today"}, "why": "what's the meeting"},
            {"tool": "open_application", "arguments": {"application": "{{app}}"}, "why": "open the call app"},
            {"tool": "set_volume", "arguments": {"percent": 60}, "why": "audible but not loud", "critical": False},
        ],
    ),
    Workflow(
        name="wrap_up",
        title="Wrap up / lock the machine",
        description="Summarise the day, save a screenshot for the record and lock the screen.",
        triggers=("wrap up", "shut everything down", "end my day", "lock up", "done for the day"),
        parameters={},
        steps=[
            {"tool": "get_agenda", "arguments": {"window": "today"}, "why": "what happened today", "critical": False},
            {"tool": "take_screenshot", "arguments": {"note": "end of day"}, "why": "record the state",
             "critical": False},
            {"tool": "lock_screen", "arguments": {}, "why": "secure the machine"},
        ],
    ),
    Workflow(
        name="debug_session",
        title="Debug session",
        description="Open the project, the logs and localhost so you can reproduce a bug.",
        triggers=("debug session", "start debugging", "reproduce the bug", "dev debug"),
        parameters={"project": "", "url": "http://localhost:8000/docs"},
        steps=[
            {"tool": "open_application", "arguments": {"application": "vscode"}, "why": "editor"},
            {"tool": "open_application", "arguments": {"application": "terminal"}, "why": "logs and commands"},
            {"tool": "open_url", "arguments": {"url": "{{url}}"}, "why": "the running service"},
        ],
    ),
)


def all_workflows() -> list[Workflow]:
    return list(WORKFLOWS)


def get_workflow(name: str) -> Workflow | None:
    needle = _slug(name)
    for workflow in WORKFLOWS:
        if _slug(workflow.name) == needle or _slug(workflow.title) == needle:
            return workflow
        if any(_slug(trigger) == needle for trigger in workflow.triggers):
            return workflow
    return None


def match_workflow(text: str) -> Workflow | None:
    """Fuzzy match a spoken phrase to a workflow."""
    if not text:
        return None
    lowered = text.lower()
    exact = get_workflow(lowered)
    if exact:
        return exact

    best: tuple[int, Workflow | None] = (0, None)
    for workflow in WORKFLOWS:
        for trigger in (workflow.name, workflow.title, *workflow.triggers):
            words = set(re.findall(r"[a-z]+", trigger.lower()))
            overlap = len(words & set(re.findall(r"[a-z]+", lowered)))
            score = overlap / max(1, len(words))
            if score > best[0]:
                best = (score, workflow)
    return best[1] if best[0] >= 0.5 else None


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (value or "").lower()).strip("_")


def _substitute(value: Any, variables: dict[str, Any]) -> Any:
    if isinstance(value, str):
        def replace(match: re.Match[str]) -> str:
            key = match.group(1)
            resolved = variables.get(key, "")
            return str(resolved) if resolved not in (None, "") else ""

        substituted = re.sub(r"\{\{\s*(\w+)\s*\}\}", replace, value).strip()
        return substituted
    if isinstance(value, dict):
        return {k: _substitute(v, variables) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute(v, variables) for v in value]
    return value


def build_plan(workflow: Workflow, variables: dict[str, Any] | None = None, *, goal: str = "") -> Plan:
    """Expand a workflow into a concrete :class:`Plan`."""
    merged = {**workflow.parameters, **(variables or {})}
    steps: list[PlanStep] = []
    for raw in workflow.steps:
        arguments = _substitute(dict(raw.get("arguments") or {}), merged)
        # drop arguments that resolved to nothing (e.g. no project configured)
        arguments = {k: v for k, v in arguments.items() if v not in (None, "")}
        # a step that declares `requires` is skipped when those variables are unknown
        missing = [name for name in (raw.get("requires") or []) if not merged.get(name)]
        if missing:
            continue
        steps.append(
            PlanStep(
                call=ToolCall(name=raw["tool"], arguments=arguments, source="workflow"),
                critical=bool(raw.get("critical", True)),
                delay_seconds=float(raw.get("delay", 0.0)),
            )
        )
    return Plan(title=workflow.title, steps=steps, goal=goal or workflow.description)


def workflow_variables_from_memory(memory: Any, project_hint: str = "") -> dict[str, Any]:
    """Fill workflow parameters from long-term memory ("open my water project")."""
    variables: dict[str, Any] = {}
    if memory is None:
        return variables
    try:
        profile = memory.profile()
    except Exception:  # noqa: BLE001
        profile = {}
    variables["user_name"] = profile.get("name", "")
    for key in ("project", "current_project", "frontend_url", "backend_path", "frontend_path", "subject"):
        value = profile.get(key) or memory.db.get_fact(key)
        if value:
            variables[key] = value
    if project_hint:
        variables["project"] = project_hint
        variables["backend_path"] = str(_join(project_hint, "backend"))
        variables["frontend_path"] = str(_join(project_hint, "frontend"))
    if variables.get("project") and "backend_path" not in variables:
        variables["backend_path"] = str(_join(variables["project"], "backend"))
        variables["frontend_path"] = str(_join(variables["project"], "frontend"))
    return variables


def _join(project: str, sub: str) -> Any:
    from pathlib import Path

    for root in (Path.home() / "Projects", Path.home() / "Code", Path.home()):
        candidate = root / project / sub
        if candidate.exists():
            return candidate
        if (root / project).exists():
            return root / project
    return Path.home() / "Projects" / project / sub


def catalogue() -> str:
    return "\n".join(
        f"- {w.name}: {w.title}. {w.description} (triggers: {', '.join(w.triggers[:3])})" for w in WORKFLOWS
    )


def find_project_hint(text: str, known: Iterable[str] = ()) -> str:
    """Pull a project name out of a phrase, preferring names we already know."""
    lowered = text.lower()
    for name in known:
        if name and name.lower() in lowered:
            return str(name)
    match = re.search(r"\b(?:my|the)\s+([\w\- ]{2,40}?)\s+(?:project|repo|codebase|app)\b", lowered)
    if match:
        return match.group(1).strip()
    match = re.search(r"\b(?:open|run|start)\s+(?:my\s+)?([\w\-]+)\s+(?:project|backend|frontend)\b", lowered)
    return match.group(1).strip() if match else ""


__all__ = ["Workflow", "WORKFLOWS", "all_workflows", "get_workflow", "match_workflow", "build_plan",
           "workflow_variables_from_memory", "catalogue", "find_project_hint"]
