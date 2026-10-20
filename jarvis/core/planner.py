"""Planner — turns a goal into an ordered, validated list of tool calls.

Two sources of plans:

1. The LLM (or the offline planner) proposes JSON steps.
2. A built-in :mod:`jarvis.automation.workflows` routine is expanded.

Either way the result is validated against the live tool registry: unknown
tools are dropped, unknown arguments are stripped, and every step is tagged
with its permission so the executor can pause for confirmation mid-plan.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

from ..automation.workflows import (
    build_plan as build_workflow_plan,
    get_workflow,
    match_workflow,
    workflow_variables_from_memory,
)
from .types import Plan, PlanStep, TaskStatus, ToolCall


@dataclass
class PlanDraft:
    title: str = ""
    goal: str = ""
    steps: list[dict[str, Any]] | None = None
    notes: str = ""
    error: str | None = None
    source: str = "llm"


class Planner:
    def __init__(self, registry: Any, brain: Any, memory: Any = None) -> None:
        self.registry = registry
        self.brain = brain
        self.memory = memory
        self.max_steps = 12

    # ------------------------------------------------------------------
    async def create_plan(self, goal: str, *, context: str = "", session_id: str = "") -> tuple[Plan, PlanDraft]:
        """Produce a validated plan for a spoken goal."""
        draft = PlanDraft(goal=goal)

        # 1. known routine? use the deterministic workflow (fast, reliable)
        workflow = match_workflow(goal)
        if workflow:
            variables = workflow_variables_from_memory(self.memory, _project_hint(goal))
            plan = build_workflow_plan(workflow, variables, goal=goal)
            draft.title = plan.title
            draft.source = f"workflow:{workflow.name}"
            draft.steps = [step.to_dict() for step in plan.steps]
            return self.validate(plan), draft

        # 2. ask the brain
        catalogue = self.registry.prompt_catalogue()
        raw = await self.brain.plan(goal, catalogue=catalogue, context=context)
        if not isinstance(raw, dict):
            draft.error = "planner returned no usable plan"
            return Plan(title=goal, steps=[], goal=goal, status=TaskStatus.FAILED), draft

        draft.title = str(raw.get("title") or goal)[:120]
        draft.notes = str(raw.get("notes") or "")
        draft.error = raw.get("error")
        steps = raw.get("steps") or []
        if not isinstance(steps, list) or not steps:
            return Plan(title=draft.title, steps=[], goal=goal), draft

        plan_steps: list[PlanStep] = []
        for entry in steps[: self.max_steps]:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("tool") or entry.get("name") or "").strip()
            if not name:
                continue
            arguments = entry.get("arguments") or entry.get("args") or {}
            if not isinstance(arguments, dict):
                arguments = {}
            reasoning = str(entry.get("why") or entry.get("reason") or "")
            plan_steps.append(
                PlanStep(
                    call=ToolCall(name=name, arguments=arguments, source="plan", reasoning=reasoning or None),
                    critical=bool(entry.get("critical", True)),
                    delay_seconds=float(entry.get("delay", 0) or 0),
                )
            )
        draft.steps = [s.to_dict() for s in plan_steps]
        plan = Plan(title=draft.title, steps=plan_steps, goal=goal)
        return self.validate(plan), draft

    # ------------------------------------------------------------------
    def validate(self, plan: Plan) -> Plan:
        """Drop steps whose tool does not exist; strip unknown arguments."""
        kept: list[PlanStep] = []
        for step in plan.steps:
            spec = self.registry.get(step.call.name)
            if spec is None or not spec.enabled:
                step.status = TaskStatus.CANCELLED
                step.call.arguments.setdefault("_error", f"unknown tool `{step.call.name}`")
                continue
            args, _problems = self.registry.validate_arguments(spec, step.call.arguments)
            step.call.arguments = args
            kept.append(step)
        plan.steps = kept[: self.max_steps]
        return plan

    # ------------------------------------------------------------------
    @staticmethod
    def summarise(plan: Plan) -> str:
        if not plan.steps:
            return "No steps could be planned."
        lines = [f"{index + 1}. {step.call.name}"
                 f"({_render_args(step.call.arguments)}) — {step.call.reasoning or ''}".rstrip(" —")
                 for index, step in enumerate(plan.steps)]
        return "\n".join(lines)


def _render_args(arguments: dict[str, Any]) -> str:
    if not arguments:
        return ""
    return ", ".join(f"{k}={_quote(v)}" for k, v in arguments.items() if not k.startswith("_"))


def _quote(value: Any) -> str:
    text = str(value)
    return f'"{text}"' if " " in text else text


def _project_hint(goal: str) -> str:
    from ..automation.workflows import find_project_hint

    known: Iterable[str] = ()
    return find_project_hint(goal, known)


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


__all__ = ["Planner", "PlanDraft", "slugify"]
