"""Action executor — the only component allowed to cause effects.

    brain decides -> executor validates (safety gate) -> backend acts -> audit

The executor is also responsible for:

* pausing on Level-2 actions and resuming them once the user says "yes",
* running multi-step plans with error recovery,
* emitting an event for every stage so the UI can show a live trace,
* writing an audit record for every decision (allow / confirm / block / error).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from .events import EventBus
from .safety import SafetyDecision, SafetyGate
from .types import (
    AgentEvent,
    EventType,
    PendingAction,
    Permission,
    Plan,
    PlanStep,
    TaskStatus,
    ToolCall,
    ToolResult,
)

ExecutionStatus = Literal["ok", "failed", "blocked", "awaiting_confirmation", "denied"]


@dataclass
class ExecutionOutcome:
    status: ExecutionStatus
    call: ToolCall
    result: ToolResult | None = None
    decision: SafetyDecision | None = None
    pending: PendingAction | None = None
    duration_ms: int = 0

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "call": self.call.to_dict(),
            "result": self.result.to_dict() if self.result else None,
            "decision": self.decision.to_dict() if self.decision else None,
            "pending": self.pending.to_dict() if self.pending else None,
            "duration_ms": self.duration_ms,
        }


@dataclass
class PlanRun:
    plan: Plan
    outcomes: list[ExecutionOutcome] = field(default_factory=list)
    stopped_reason: str = ""
    pending: PendingAction | None = None

    @property
    def succeeded(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "ok")

    @property
    def failed(self) -> int:
        return sum(1 for o in self.outcomes if o.status in {"failed", "blocked", "denied"})

    def summary(self) -> str:
        lines = []
        for index, outcome in enumerate(self.outcomes, start=1):
            symbol = {"ok": "✓", "failed": "✗", "blocked": "⛔", "denied": "✗",
                      "awaiting_confirmation": "…"}[outcome.status]
            detail = (outcome.result.message if outcome.result else outcome.status)
            lines.append(f"{index}. {symbol} {outcome.call.name} — {detail}")
        if self.stopped_reason:
            lines.append(f"stopped: {self.stopped_reason}")
        return "\n".join(lines)


class Executor:
    """Runs tool calls and plans under the safety gate."""

    def __init__(
        self,
        registry: Any,
        gate: SafetyGate,
        bus: EventBus,
        ctx_factory: Callable[..., Any],
        db: Any = None,
        settings: Any = None,
    ) -> None:
        self.registry = registry
        self.gate = gate
        self.bus = bus
        self.ctx_factory = ctx_factory
        self.db = db
        self.settings = settings
        self.pending_tokens: dict[str, PendingAction] = {}

    # ------------------------------------------------------------------
    async def execute(
        self,
        call: ToolCall,
        *,
        session_id: str = "",
        turn_id: str = "",
        plan_id: str | None = None,
        plan_index: int = 0,
        pre_approved: bool = False,
    ) -> ExecutionOutcome:
        started = time.perf_counter()
        spec = self.registry.get(call.name)

        if spec is None:
            result = ToolResult(ok=False, tool=call.name, call_id=call.id,
                                error=f"Unknown tool `{call.name}`.",
                                message=f"I don't have a tool called `{call.name}`.")
            self._audit(call, "safe", "error", "unknown tool", result, session_id, turn_id)
            await self._emit_tool_result(call, result, session_id)
            return ExecutionOutcome("failed", call, result=result, duration_ms=_ms(started))

        decision = self.gate.evaluate(call, spec.permission)

        await self.bus.publish(AgentEvent(
            type=EventType.TOOL_CALL,
            session_id=session_id,
            payload={
                "call": call.to_dict(),
                "permission": spec.permission.value,
                "category": spec.category.value,
                "decision": decision.to_dict(),
                "plan_id": plan_id,
                "plan_index": plan_index,
                "turn_id": turn_id,
            },
        ))

        if decision.blocked:
            result = ToolResult(ok=False, tool=call.name, call_id=call.id, error=decision.reason,
                                message=f"Blocked by the safety layer: {decision.reason}",
                                speech="I can't do that — it's blocked by my safety rules.")
            self._audit(call, spec.permission.value, "block", decision.reason, result, session_id, turn_id)
            await self._emit_tool_result(call, result, session_id)
            await self.bus.publish(AgentEvent(type=EventType.NOTIFICATION, session_id=session_id,
                                              payload={"level": "blocked", "message": decision.reason,
                                                       "tool": call.name}))
            return ExecutionOutcome("blocked", call, result=result, decision=decision, duration_ms=_ms(started))

        if decision.permission is Permission.CONFIRM and not pre_approved:
            pending = self._register_pending(call, decision, session_id, plan_id, plan_index)
            self._audit(call, spec.permission.value, "confirm", decision.reason, None, session_id, turn_id)
            await self.bus.publish(AgentEvent(
                type=EventType.CONFIRMATION_REQUIRED,
                session_id=session_id,
                payload=pending.to_dict(),
            ))
            return ExecutionOutcome("awaiting_confirmation", call, decision=decision, pending=pending,
                                    duration_ms=_ms(started))

        result = await self.registry.execute(call, self.ctx_factory(session_id=session_id, user_text=""))
        self._audit(call, spec.permission.value, "ok" if result.ok else "error", decision.reason, result,
                    session_id, turn_id)
        await self._emit_tool_result(call, result, session_id)
        status: ExecutionStatus = "ok" if result.ok else "failed"
        return ExecutionOutcome(status, call, result=result, decision=decision, duration_ms=_ms(started))

    # ------------------------------------------------------------------
    def _register_pending(self, call: ToolCall, decision: SafetyDecision, session_id: str,
                          plan_id: str | None, plan_index: int) -> PendingAction:
        import secrets

        token = secrets.token_urlsafe(9)
        ttl = int(getattr(self.settings, "confirmation_ttl_seconds", 300))
        pending = PendingAction(token=token, session_id=session_id, call=call, permission=decision.permission,
                                prompt=decision.prompt, expires_at=time.time() + ttl, plan_id=plan_id,
                                plan_index=plan_index)
        self.pending_tokens[token] = pending
        if self.db is not None:
            self.db.save_pending(token, session_id, call.name, call.arguments, decision.prompt, ttl,
                                 plan_id, plan_index)
        return pending

    def take_pending(self, token: str) -> PendingAction | None:
        pending = self.pending_tokens.pop(token, None)
        if pending is None and self.db is not None:
            row = self.db.get_pending(token)
            if row and row["status"] == "waiting" and not row["expired"]:
                pending = PendingAction(
                    token=token,
                    session_id=row["session_id"],
                    call=ToolCall(name=row["tool"], arguments=row.get("arguments") or {}),
                    permission=Permission(row.get("permission", "confirm")),
                    prompt=row.get("prompt", ""),
                    created_at=row.get("created_at", time.time()),
                    expires_at=row.get("expires_at", 0),
                    plan_id=row.get("plan_id"),
                    plan_index=row.get("plan_index", 0),
                )
        if pending and pending.expires_at and pending.expires_at < time.time():
            return None
        return pending

    async def resolve(self, token: str, approved: bool, *, session_id: str = "") -> ExecutionOutcome | None:
        """Apply (or drop) a Level-2 action the user was asked about."""
        pending = self.take_pending(token)
        if pending is None:
            return None
        if self.db is not None:
            self.db.resolve_pending(token, "approved" if approved else "denied")

        await self.bus.publish(AgentEvent(
            type=EventType.CONFIRMATION_RESOLVED,
            session_id=session_id or pending.session_id,
            payload={"token": token, "approved": approved, "tool": pending.call.name},
        ))

        if not approved:
            result = ToolResult(ok=False, tool=pending.call.name, call_id=pending.call.id,
                                message=f"Cancelled `{pending.call.name}` at your request.",
                                speech="Okay, cancelled.")
            self._audit(pending.call, pending.permission.value, "denied", "user declined", result,
                        pending.session_id)
            await self._emit_tool_result(pending.call, result, pending.session_id)
            return ExecutionOutcome("denied", pending.call, result=result, pending=pending)

        return await self.execute(
            pending.call,
            session_id=pending.session_id,
            plan_id=pending.plan_id,
            plan_index=pending.plan_index,
            pre_approved=True,
        )

    # ------------------------------------------------------------------
    async def run_plan(
        self,
        plan: Plan,
        *,
        session_id: str = "",
        stop_on_failure: bool = True,
        step_delay: float = 0.25,
        on_step: Callable[[PlanStep, ExecutionOutcome], Any] | None = None,
    ) -> PlanRun:
        """Execute a plan sequentially with recovery. Pauses on confirmations."""
        run = PlanRun(plan=plan)
        plan.status = TaskStatus.RUNNING
        await self.bus.publish(AgentEvent(type=EventType.PLAN, session_id=session_id, payload=plan.to_dict()))
        if self.db is not None:
            self.db.save_plan(plan.to_dict(), session_id)

        for index, step in enumerate(plan.steps):
            if step.delay_seconds:
                await asyncio.sleep(min(step.delay_seconds, 5))
            step.status = TaskStatus.RUNNING
            outcome = await self.execute(step.call, session_id=session_id, plan_id=plan.id, plan_index=index)
            step.result = outcome.result
            run.outcomes.append(outcome)

            if outcome.status == "awaiting_confirmation":
                step.status = TaskStatus.AWAITING_CONFIRMATION
                run.pending = outcome.pending
                run.stopped_reason = f"waiting for confirmation on step {index + 1} ({step.call.name})"
                plan.status = TaskStatus.AWAITING_CONFIRMATION
                break

            if outcome.status == "ok":
                step.status = TaskStatus.DONE
            elif outcome.status == "denied":
                step.status = TaskStatus.CANCELLED
                run.stopped_reason = f"you cancelled step {index + 1}"
                plan.status = TaskStatus.CANCELLED
                break
            else:
                step.status = TaskStatus.FAILED
                if step.critical and stop_on_failure:
                    run.stopped_reason = f"step {index + 1} failed: {(outcome.result.message if outcome.result else '')}"
                    plan.status = TaskStatus.FAILED
                    for remaining in plan.steps[index + 1:]:
                        remaining.status = TaskStatus.CANCELLED
                    break

            if on_step is not None:
                callback = on_step(step, outcome)
                if asyncio.iscoroutine(callback):
                    await callback
            if step_delay and index < len(plan.steps) - 1:
                await asyncio.sleep(step_delay)

        if plan.status is TaskStatus.RUNNING:
            plan.status = TaskStatus.DONE if run.failed == 0 else TaskStatus.FAILED
        if self.db is not None:
            self.db.save_plan(plan.to_dict(), session_id)
        await self.bus.publish(AgentEvent(type=EventType.PLAN, session_id=session_id,
                                          payload={**plan.to_dict(), "summary": run.summary()}))
        return run

    async def resume_plan(self, plan: Plan, *, session_id: str = "", pre_approved: bool = True,
                          stop_on_failure: bool = True) -> PlanRun:
        """Continue a plan that paused for confirmation."""
        run = PlanRun(plan=plan)
        plan.status = TaskStatus.RUNNING
        for index, step in enumerate(plan.steps):
            if step.status in {TaskStatus.DONE, TaskStatus.CANCELLED}:
                continue
            outcome = await self.execute(step.call, session_id=session_id, plan_id=plan.id, plan_index=index,
                                         pre_approved=pre_approved)
            step.result = outcome.result
            run.outcomes.append(outcome)
            if outcome.status == "awaiting_confirmation":
                step.status = TaskStatus.AWAITING_CONFIRMATION
                run.pending = outcome.pending
                run.stopped_reason = f"waiting for confirmation on step {index + 1}"
                plan.status = TaskStatus.AWAITING_CONFIRMATION
                break
            step.status = TaskStatus.DONE if outcome.status == "ok" else TaskStatus.FAILED
            if step.status is TaskStatus.FAILED and step.critical and stop_on_failure:
                run.stopped_reason = f"step {index + 1} failed"
                plan.status = TaskStatus.FAILED
                break
            await asyncio.sleep(0.15)
        if plan.status is TaskStatus.RUNNING:
            plan.status = TaskStatus.DONE if run.failed == 0 else TaskStatus.FAILED
        if self.db is not None:
            self.db.save_plan(plan.to_dict(), session_id)
        await self.bus.publish(AgentEvent(type=EventType.PLAN, session_id=session_id,
                                          payload={**plan.to_dict(), "summary": run.summary()}))
        return run

    # ------------------------------------------------------------------
    def _audit(self, call: ToolCall, permission: str, decision: str, reason: str,
               result: ToolResult | None, session_id: str = "", turn_id: str = "") -> None:
        if self.db is None:
            return
        try:
            self.db.audit(
                tool=call.name,
                arguments=call.arguments,
                permission=permission,
                decision=decision,
                reason=reason,
                ok=bool(result.ok) if result else decision == "confirm",
                message=(result.message if result else "")[:500],
                duration_ms=result.duration_ms if result else 0,
                simulated=bool(result.simulated) if result else False,
                session_id=session_id,
                turn_id=turn_id,
            )
        except Exception:  # noqa: BLE001 — auditing must never break an action
            pass

    async def _emit_tool_result(self, call: ToolCall, result: ToolResult, session_id: str) -> None:
        await self.bus.publish(AgentEvent(
            type=EventType.TOOL_RESULT,
            session_id=session_id,
            payload={"call_id": call.id, "tool": call.name, "result": result.to_dict()},
        ))


def _ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


__all__ = ["Executor", "ExecutionOutcome", "PlanRun", "ExecutionStatus"]
