"""Meta tools: plans, workflows and self-description.

These are the tools the model uses when the request is bigger than one action —
"prepare my workspace for coding", "plan how to deploy my project",
"what can you do?".
"""

from __future__ import annotations

from typing import Any

from ..automation.workflows import (
    all_workflows,
    build_plan as build_workflow_plan,
    catalogue as workflow_catalogue,
    find_project_hint,
    get_workflow,
    match_workflow,
    workflow_variables_from_memory,
)
from ..core.types import Permission, ToolCategory, ToolResult
from .registry import ToolContext, tool


@tool(permission=Permission.SAFE, category=ToolCategory.META,
      examples=["What can you do?", "Jarvis, help"])
def list_tools(ctx: ToolContext, category: str = "") -> ToolResult:
    """List the tools JARVIS can use, grouped by category.

    Args:
        category: Optional filter: computer, browser, web, files, system, productivity, memory, vision, meta.
    """
    registry = ctx.service("registry")
    if registry is None:
        return ToolResult(ok=False, message="Tool registry unavailable.")

    grouped = registry.by_category()
    if category:
        grouped = {k: v for k, v in grouped.items() if k == category.lower()}
        if not grouped:
            return ToolResult(ok=False, message=f"No category called `{category}`.",
                              data={"categories": sorted(registry.by_category())})

    payload: dict[str, Any] = {}
    lines: list[str] = []
    total = 0
    for name in sorted(grouped):
        specs = [s for s in grouped[name] if s.enabled]
        payload[name] = [s.to_dict() for s in specs]
        total += len(specs)
        lines.append(f"{name.title()}: " + ", ".join(s.name for s in specs))

    speech = (
        f"I have {total} tools. "
        + " I can control apps, the mouse and keyboard, take screenshots, search the web and open pages, "
        "manage files, run reminders, calendar and tasks, remember things about you, and execute multi-step "
        "workflows. Say what you need and I'll pick the right one."
    )
    return ToolResult(ok=True, message=f"{total} tools in {len(payload)} categories", speech=speech,
                      data={"categories": payload, "count": total, "text": "\n".join(lines)})


@tool(permission=Permission.SAFE, category=ToolCategory.META)
def list_workflows() -> ToolResult:
    """List the built-in multi-step routines JARVIS can run."""
    workflows = all_workflows()
    return ToolResult(
        ok=True,
        message=f"{len(workflows)} workflows available",
        speech="I have routines for " + ", ".join(w.title.lower() for w in workflows[:4]) + ".",
        data={"workflows": [w.to_dict() for w in workflows]},
    )


@tool(
    permission=Permission.SAFE,
    category=ToolCategory.META,
    examples=["Prepare my workspace for coding"],
)
async def run_workflow(ctx: ToolContext, name: str, variables: dict[str, Any] | None = None,
                       execute: bool = True) -> ToolResult:
    """Run a built-in multi-step routine.

    Args:
        name: Workflow name, e.g. "coding_setup", "morning_routine", "study_setup", "meeting_prep", "wrap_up".
        variables: Optional overrides, e.g. {"project": "smart-water-grid"}.
        execute: Run the steps now (false = just return the plan).
    """
    planner = ctx.service("planner")
    executor = ctx.service("executor")
    if planner is None or executor is None:
        return ToolResult(ok=False, message="Planner/executor unavailable in this context.")

    workflow = get_workflow(name) or match_workflow(name)
    if workflow is None:
        return ToolResult(ok=False, message=f"No workflow called `{name}`.",
                          speech=f"I don't have a routine called {name}.",
                          data={"available": [w.name for w in all_workflows()]})

    merged: dict[str, Any] = workflow_variables_from_memory(ctx.memory, find_project_hint(name))
    if variables:
        merged.update({k: v for k, v in variables.items() if v not in (None, "")})

    required = sorted({name for step in workflow.steps for name in (step.get("requires") or [])})
    missing = [name for name in required if not merged.get(name)]
    skipped: list[str] = []
    if missing:
        runnable = [step for step in workflow.steps if not (set(step.get("requires") or []) & set(missing))]
        skipped = [str(step.get("tool")) for step in workflow.steps if step not in runnable]
        if not runnable:
            question = {
                "project": "Which project should I set up?",
                "subject": "What subject are you studying?",
            }.get(missing[0], f"I need {', '.join(missing)} before I can run that routine.")
            return ToolResult(
                ok=False,
                message=f"Workflow `{workflow.name}` needs: {', '.join(missing)}",
                speech=question,
                data={"workflow": workflow.to_dict(), "missing": missing, "known": merged},
            )

    plan = build_workflow_plan(workflow, merged, goal=workflow.description)
    plan = planner.validate(plan)
    if not plan.steps:
        return ToolResult(ok=False, message=f"Workflow `{workflow.name}` produced no runnable steps.",
                          data={"workflow": workflow.to_dict()})

    if not execute:
        return ToolResult(ok=True, message=f"Plan for {workflow.title}:\n{planner.summarise(plan)}",
                          speech=f"{workflow.title} would take {len(plan.steps)} steps.",
                          data={"plan": plan.to_dict()})

    run = await executor.run_plan(plan, session_id=ctx.session_id)
    if run.pending is not None:
        return ToolResult(
            ok=True,
            message=f"{workflow.title} paused: {run.stopped_reason}",
            speech=run.pending.prompt,
            data={"plan": plan.to_dict(), "summary": run.summary(), "pending": run.pending.to_dict(),
                  "steps_done": run.succeeded},
        )
    speech = (
        f"{workflow.title} finished — {run.succeeded} of {len(plan.steps)} steps done."
        if run.failed == 0
        else f"{workflow.title} partly finished: {run.succeeded} succeeded, {run.failed} failed."
    )
    note = ""
    if skipped:
        steps_skipped = ", ".join(dict.fromkeys(skipped))
        hint = "project" if "project" in missing else (missing[0] if missing else "")
        note = f" Skipped {steps_skipped} — tell me your {hint} and I will finish those too."
    return ToolResult(
        ok=run.failed == 0,
        message=run.summary() + (f" (skipped: {', '.join(dict.fromkeys(skipped))})" if skipped else ""),
        speech=speech + note,
        data={"plan": plan.to_dict(), "summary": run.summary(), "succeeded": run.succeeded,
              "failed": run.failed, "stopped_reason": run.stopped_reason,
              "skipped": skipped, "missing": missing},
    )


@tool(permission=Permission.SAFE, category=ToolCategory.META,
      examples=["Plan how to set up my environment for today"])
async def make_plan(ctx: ToolContext, goal: str, execute: bool = True) -> ToolResult:
    """Break a goal into steps and (optionally) execute them in order.

    Args:
        goal: What you want achieved, e.g. "prepare my workspace for coding".
        execute: Run the plan immediately (false = just show it).
    """
    planner = ctx.service("planner")
    executor = ctx.service("executor")
    if planner is None or executor is None:
        return ToolResult(ok=False, message="Planner/executor unavailable in this context.")

    context = ctx.service("planner_context", "")
    plan, draft = await planner.create_plan(goal, context=context if isinstance(context, str) else "",
                                            session_id=ctx.session_id)
    if not plan.steps:
        message = draft.error or draft.notes or "I couldn't break that into steps I'm able to run."
        return ToolResult(ok=False, message=message,
                          speech="I couldn't plan that with the tools I have. Can you be more specific?",
                          data={"goal": goal, "draft": draft.__dict__ if hasattr(draft, "__dict__") else {}})

    if not execute:
        return ToolResult(ok=True, message=f"Plan for {plan.title}:\n{planner.summarise(plan)}",
                          speech=f"Here's the plan: {planner.summarise(plan).replace(chr(10), ' ')}",
                          data={"plan": plan.to_dict()})

    run = await executor.run_plan(plan, session_id=ctx.session_id)
    if run.pending is not None:
        return ToolResult(ok=True, message=f"Plan paused: {run.stopped_reason}", speech=run.pending.prompt,
                          data={"plan": plan.to_dict(), "summary": run.summary(),
                                "pending": run.pending.to_dict()})

    speech = (
        f"Done — {run.succeeded} of {len(plan.steps)} steps completed."
        if run.failed == 0
        else f"{run.succeeded} steps completed, {run.failed} failed. {run.stopped_reason}".strip()
    )
    return ToolResult(ok=run.failed == 0, message=run.summary(), speech=speech,
                      data={"plan": plan.to_dict(), "summary": run.summary(),
                            "succeeded": run.succeeded, "failed": run.failed,
                            "stopped_reason": run.stopped_reason})


@tool(permission=Permission.SAFE, category=ToolCategory.META)
def show_audit_trail(ctx: ToolContext, limit: int = 15) -> ToolResult:
    """Show what JARVIS has recently done, including anything the safety layer blocked.

    Args:
        limit: How many entries to return.
    """
    if ctx.db is None:
        return ToolResult(ok=False, message="No database attached.")
    entries = ctx.db.audit_log(limit=max(1, min(int(limit), 100)))
    stats = ctx.db.stats()
    return ToolResult(ok=True, message=f"{len(entries)} recent action(s); {stats['blocked']} blocked overall",
                      speech=f"I've run {stats['tool_calls']} actions and blocked {stats['blocked']}.",
                      data={"entries": entries, "stats": stats})


@tool(permission=Permission.SAFE, category=ToolCategory.META)
def jarvis_status(ctx: ToolContext) -> ToolResult:
    """Report JARVIS's configuration: model, backend, safety mode and tool count."""
    registry = ctx.service("registry")
    settings = ctx.settings.describe()
    backend = ctx.backend.describe()
    data = {
        "settings": settings,
        "backend": backend,
        "tools": len(registry.all()) if registry else 0,
        "workflows": [w.name for w in all_workflows()],
        "memory": ctx.memory.stats() if ctx.memory else {},
        "database": ctx.db.stats() if ctx.db else {},
    }
    model = settings.get("llm_model") or "offline rule engine"
    return ToolResult(
        ok=True,
        message=f"model={model} backend={backend.get('name')} tools={data['tools']}",
        speech=f"I'm running on {model} with the {backend.get('name')} action backend and {data['tools']} tools.",
        data=data,
    )
