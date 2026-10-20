"""The JARVIS brain.

    Listen -> Understand -> Plan -> Select tool -> Execute -> Observe -> Respond

`Jarvis.process()` is the single entry point used by the CLI, the REST API and
the WebSocket. It:

1. builds context (memory + environment + live tool catalogue),
2. asks the brain (LLM, or the offline rule engine) for a decision,
3. hands every tool call to the :class:`~jarvis.core.executor.Executor`
   — which applies the safety gate and talks to the action backend,
4. feeds observations back and repeats until the model answers or the step
   budget is exhausted,
5. publishes events (and therefore speech) for every stage.

The LLM never touches the machine: it can only name tools from the catalogue.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Iterable

from ..backends.base import get_backend
from ..llm.base import Brain, Decision
from ..llm.factory import build_brain
from ..llm.offline import OfflineBrain
from ..store.database import Database
from ..tools.registry import ToolContext, ToolRegistry, load_tools
from .context import ContextBuilder
from .events import EventBus, bus as global_bus
from .executor import ExecutionOutcome, Executor
from .memory import Memory
from .planner import Planner
from .safety import LoopGuard, SafetyGate
from .types import (
    AgentEvent,
    EventType,
    Message,
    Permission,
    Plan,
    TaskStatus,
    ToolCall,
    ToolResult,
    Turn,
    plan_from_dict,
)

YES = re.compile(r"^\s*(?:yes|yep|yeah|yup|ya|sure|okay|ok|k|do it|go ahead|confirm|confirmed|affirmative|"
                 r"please do|correct|haan|han|ji|ji haan|done it)\s*[.!?]*\s*$", re.IGNORECASE)
NO = re.compile(r"^\s*(?:no|nope|nah|cancel|cancel it|stop|abort|don'?t|do not|negative|never mind|"
                 r"forget it|nahi|mat)\b.*$", re.IGNORECASE)


@dataclass
class JarvisOptions:
    """Knobs for assembling an agent (used by the CLI, the API and the tests)."""

    settings: Any = None
    db: Any = None
    backend: Any = None
    registry: Any = None
    brain: Any = None
    bus: Any = None
    memory: Any = None
    scheduler: Any = None
    services: dict[str, Any] = field(default_factory=dict)
    tool_modules: Iterable[str] = ()
    disabled_tools: Iterable[str] = ()
    on_speak: Callable[[str], Any] | None = None


class Jarvis:
    """The agent. One instance per process; sessions are multiplexed by id."""

    def __init__(self, options: JarvisOptions | None = None, **kwargs: Any) -> None:
        options = options or JarvisOptions(**kwargs)
        self.options = options

        from ..config.settings import get_settings

        self.settings = options.settings or get_settings()
        self.db: Database = options.db or Database(self.settings.db_path)
        self.backend = options.backend or get_backend(self.settings)
        self.bus: EventBus = options.bus or global_bus
        self.memory: Memory = options.memory or Memory(self.db, self.settings)

        self.registry: ToolRegistry = options.registry or ToolRegistry()
        self.services: dict[str, Any] = dict(options.services)
        if options.registry is None:
            self._load_tools(options.tool_modules)
        if options.disabled_tools:
            self.registry.disable(list(options.disabled_tools))

        self.gate = SafetyGate(
            mode=self.settings.safety_mode,
            workspace=(str(self.settings.home), str(self.settings.home / "workspace")),
            require_shell_confirmation=self.settings.require_confirmation_for_shell,
        )
        self.brain: Brain = options.brain or build_brain(self.settings, self.registry)
        self.executor = Executor(self.registry, self.gate, self.bus, self._make_context, db=self.db,
                                 settings=self.settings)
        self.planner = Planner(self.registry, self.brain, self.memory)
        self.context = ContextBuilder(self.settings, self.registry, self.memory, self.db, self.backend)
        self.scheduler = options.scheduler
        self.services.update(
            {
                "registry": self.registry,
                "planner": self.planner,
                "executor": self.executor,
                "brain": self.brain,
                "backend": self.backend,
                "memory": self.memory,
                "db": self.db,
            }
        )
        self.on_speak = options.on_speak
        self._paused_plans: dict[str, Plan] = {}
        self._last_session = ""
        self.turn_count = 0

    # ------------------------------------------------------------------
    # construction helpers
    # ------------------------------------------------------------------
    def _load_tools(self, modules: Iterable[str] = ()) -> None:
        """Import the tool modules and register everything they export."""
        from ..tools import (
            browser,
            calendar_tools,
            communication,
            computer,
            files,
            memory_tools,
            meta,
            reminders,
            system,
            vision,
            web,
        )

        default = (computer, browser, web, files, system, reminders, calendar_tools, communication,
                   memory_tools, vision, meta)
        for module in default:
            load_tools(self.registry, module)

        for name in modules:
            import importlib

            load_tools(self.registry, importlib.import_module(name))

        self.services["registry"] = self.registry

    def _make_context(self, *, session_id: str = "", user_text: str = "") -> ToolContext:
        services = dict(self.services)
        services["planner_context"] = self.context.planner_context(session_id)
        if self.scheduler is not None:
            services["scheduler"] = self.scheduler
        return ToolContext(
            settings=self.settings,
            backend=self.backend,
            db=self.db,
            memory=self.memory,
            events=self.bus,
            scheduler=self.scheduler,
            session_id=session_id,
            user_text=user_text,
            services=services,
        )

    def describe(self) -> dict[str, Any]:
        return {
            "settings": self.settings.describe(),
            "backend": self.backend.describe(),
            "brain": self.brain.describe(),
            "tools": len(self.registry.all()),
            "tool_categories": {k: len(v) for k, v in self.registry.by_category().items()},
            "memory": self.memory.stats(),
            "database": self.db.stats(),
            "safety": {"mode": self.settings.safety_mode, "blocked": self.gate.blocked_count},
            "turns": self.turn_count,
        }

    def attach_scheduler(self, scheduler: Any) -> None:
        self.scheduler = scheduler
        self.services["scheduler"] = scheduler

    # ------------------------------------------------------------------
    # the main loop
    # ------------------------------------------------------------------
    async def process(self, text: str, *, session_id: str = "", audio_path: str | None = None,
                      speak: bool | None = None) -> Turn:
        """Handle one utterance end to end and return the resulting :class:`Turn`."""
        started = time.perf_counter()
        session_id = session_id or self._last_session or _new_session()
        self._last_session = session_id
        self.turn_count += 1
        turn = Turn(session_id=session_id, user_text=text.strip())
        speak = self.settings.speak_responses if speak is None else speak

        await self.bus.publish(AgentEvent(type=EventType.USER_MESSAGE, session_id=session_id,
                                          payload={"text": text, "turn_id": turn.id, "audio": audio_path}))

        # a bare "yes"/"no" answers an outstanding confirmation
        pending = self._outstanding_pending(session_id)
        if pending and (YES.match(text.strip()) or NO.match(text.strip())):
            return await self.confirm(pending["token"], bool(YES.match(text.strip())), session_id=session_id,
                                      speak=speak, parent_turn=turn)

        # learn durable facts opportunistically ("my name is ...")
        try:
            absorbed = await self.memory.absorb(text)
            if absorbed:
                await self.bus.publish(AgentEvent(type=EventType.NOTIFICATION, session_id=session_id,
                                                  payload={"level": "memory", "stored": absorbed}))
        except Exception:  # noqa: BLE001
            pass

        system_prompt = await self.context.system_prompt()
        messages = self.context.messages(session_id, text, system_prompt)
        tools = self.registry.to_openai_tools()
        guard = LoopGuard(max_repeats=2)
        observations: list[str] = []
        final_text = ""
        step_budget = max(1, int(self.settings.max_steps))

        for step_index in range(step_budget):
            decision = await self._decide(messages, tools, session_id, turn)

            if decision.error and isinstance(self.brain, OfflineBrain) is False:
                # LLM failure -> degrade to the deterministic engine instead of dying
                await self.bus.publish(AgentEvent(type=EventType.ERROR, session_id=session_id,
                                                  payload={"stage": "decide", "error": decision.error}))
                fallback = OfflineBrain(self.settings, self.registry)
                decision = await fallback.decide(messages, tools=tools,
                                                 context={"now": datetime.now(), "session_id": session_id})
                decision.error = None

            if decision.text and not decision.requires_tool:
                final_text = decision.text
                turn.intent = decision.intent or turn.intent
                break

            if not decision.requires_tool:
                final_text = decision.text or ""
                break

            turn.intent = decision.intent or turn.intent
            await self.bus.publish(AgentEvent(
                type=EventType.THINKING, session_id=session_id,
                payload={"step": step_index + 1, "intent": decision.intent,
                         "tools": [c.name for c in decision.tool_calls],
                         "provider": decision.provider, "model": decision.model,
                         "confidence": decision.confidence},
            ))

            assistant_message = Message(
                role="assistant",
                content=decision.text or None,
                tool_calls=[
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {"name": call.name, "arguments": _json_dumps(call.arguments)},
                    }
                    for call in decision.tool_calls
                ] if decision.provider != "offline" else None,
            )
            messages.append(assistant_message)

            executed_any = False
            for call in decision.tool_calls:
                turn.tool_calls.append(call)
                ok, message = guard.register(call)
                if not ok:
                    result = ToolResult(ok=False, tool=call.name, call_id=call.id, error=message, message=message)
                else:
                    outcome: ExecutionOutcome = await self.executor.execute(
                        call, session_id=session_id, turn_id=turn.id
                    )
                    if outcome.status == "awaiting_confirmation" and outcome.pending is not None:
                        turn.pending = outcome.pending
                        turn.steps.append({"tool": call.name, "status": "awaiting_confirmation",
                                           "prompt": outcome.pending.prompt})
                        final_text = outcome.pending.prompt
                        await self._remember_pause(outcome, call, session_id)
                        break
                    result = outcome.result or ToolResult(ok=False, tool=call.name, message="No result.",
                                                          call_id=call.id)
                    turn.steps.append({"tool": call.name, "arguments": call.arguments,
                                       "status": outcome.status, "message": result.message,
                                       "duration_ms": result.duration_ms, "simulated": result.simulated})
                    executed_any = True

                turn.tool_results.append(result)
                observations.append(result.to_observation())
                messages.append(Message(role="tool", content=result.to_observation(), tool_call_id=call.id,
                                        name=call.name))
                self._track_paused_plan(result, session_id)

            if turn.pending is not None:
                break

            if isinstance(self.brain, OfflineBrain) or decision.provider == "offline":
                # the rule engine is single-shot: build the reply from the results
                final_text = _compose_offline_reply(turn.tool_results, decision)
                break

            if not executed_any:
                final_text = decision.text or "I couldn't do that."
                break

        else:
            final_text = final_text or (
                f"I hit my step limit ({step_budget}) before finishing. Here's what happened: "
                + "; ".join(step.get("message", step["tool"]) for step in turn.steps[-3:])
            )

        turn.response = final_text.strip() or _fallback_reply(turn)
        turn.speech = _spoken_form(turn)
        turn.ok = all(r.ok for r in turn.tool_results) if turn.tool_results else True
        turn.simulated = bool(self.backend.simulated) or any(r.simulated for r in turn.tool_results)
        turn.provider = getattr(self.brain, "name", "?")
        turn.model = getattr(self.brain, "describe", lambda: {})().get("model", "")
        turn.duration_ms = int((time.perf_counter() - started) * 1000)

        self.memory.push(session_id, "user", text, turn_id=turn.id)
        self.memory.push(session_id, "assistant", turn.response, turn_id=turn.id,
                         tool_calls=[c.name for c in turn.tool_calls])

        await self.bus.publish(AgentEvent(type=EventType.AGENT_MESSAGE, session_id=session_id,
                                          payload={"text": turn.response, "turn_id": turn.id,
                                                   "steps": turn.steps, "pending": turn.pending.to_dict()
                                                   if turn.pending else None}))
        if speak and turn.speech:
            await self.speak(turn.speech, session_id=session_id)

        return turn

    # ------------------------------------------------------------------
    async def _decide(self, messages: list[Message], tools: list[dict[str, Any]], session_id: str,
                      turn: Turn) -> Decision:
        return await self.brain.decide(
            messages,
            tools=tools if getattr(self.brain, "name", "") != "offline" else tools,
            catalogue=self.registry.prompt_catalogue(),
            context={"now": datetime.now(), "session_id": session_id, "turn_id": turn.id},
        )

    def _outstanding_pending(self, session_id: str) -> dict[str, Any] | None:
        rows = self.db.list_pending(session_id) if self.db is not None else []
        for row in rows:
            if row.get("expires_at", 0) >= time.time():
                return row
        for token, pending in list(self.executor.pending_tokens.items()):
            if pending.session_id == session_id and pending.expires_at >= time.time():
                return pending.to_dict() | {"token": token}
        return None

    async def _remember_pause(self, outcome: ExecutionOutcome, call: ToolCall, session_id: str) -> None:
        """Keep the paused plan so `confirm()` can resume it."""
        if outcome.pending is None:
            return
        if call.arguments.get("_plan") or outcome.pending.plan_id:
            plan_id = outcome.pending.plan_id
            if plan_id and self.db is not None:
                stored = self.db.get_plan(plan_id)
                if stored and stored.get("spec"):
                    self._paused_plans[outcome.pending.token] = plan_from_dict(stored["spec"])

    def _track_paused_plan(self, result: ToolResult, session_id: str) -> None:
        data = result.data if isinstance(result.data, dict) else {}
        pending = data.get("pending")
        plan = data.get("plan")
        if isinstance(pending, dict) and isinstance(plan, dict) and pending.get("token"):
            self._paused_plans[pending["token"]] = plan_from_dict(plan)

    # ------------------------------------------------------------------
    async def confirm(self, token: str, approved: bool, *, session_id: str = "", speak: bool | None = True,
                      parent_turn: Turn | None = None) -> Turn:
        """Resolve a Level-2 confirmation ("yes" / "no") and finish the turn."""
        started = time.perf_counter()
        turn = parent_turn or Turn(session_id=session_id or self._last_session, user_text="yes" if approved else "no")
        session_id = turn.session_id or session_id
        speak = self.settings.speak_responses if speak is None else speak

        outcome = await self.executor.resolve(token, approved, session_id=session_id)
        if outcome is None:
            turn.response = "That confirmation has expired or was already handled."
            turn.ok = False
            turn.error = "unknown token"
            if speak:
                await self.speak(turn.response, session_id=session_id)
            return turn

        if outcome.result:
            turn.tool_results.append(outcome.result)
            turn.steps.append({"tool": outcome.call.name, "status": outcome.status,
                               "message": outcome.result.message, "simulated": outcome.result.simulated})
        turn.tool_calls.append(outcome.call)
        turn.intent = f"confirm:{outcome.call.name}"

        paused_plan = self._paused_plans.pop(token, None)
        if approved and paused_plan is not None:
            for index, step in enumerate(paused_plan.steps):
                if index < (outcome.pending.plan_index if outcome.pending else 0):
                    step.status = TaskStatus.DONE
            run = await self.executor.resume_plan(paused_plan, session_id=session_id)
            turn.plan = paused_plan
            turn.response = (
                f"{paused_plan.title}: {run.succeeded} of {len(paused_plan.steps)} steps completed."
                if run.failed == 0 else
                f"{paused_plan.title}: {run.succeeded} done, {run.failed} failed. {run.stopped_reason}".strip()
            )
            turn.simulated = self.backend.simulated
        elif outcome.result is not None:
            turn.response = outcome.result.message
            turn.speech = outcome.result.spoken
            turn.simulated = outcome.result.simulated
        else:
            turn.response = "Cancelled." if not approved else "Done."
            turn.speech = turn.response

        turn.ok = outcome.status in {"ok", "denied"}
        turn.duration_ms = int((time.perf_counter() - started) * 1000)
        turn.provider = getattr(self.brain, "name", "?")
        if not turn.speech:
            turn.speech = _spoken_form(turn)

        self.memory.push(session_id, "user", "yes" if approved else "no")
        self.memory.push(session_id, "assistant", turn.response, tool_calls=[outcome.call.name])
        await self.bus.publish(AgentEvent(type=EventType.AGENT_MESSAGE, session_id=session_id,
                                          payload={"text": turn.response, "turn_id": turn.id,
                                                   "steps": turn.steps, "resumed": bool(approved)}))
        if speak and turn.speech:
            await self.speak(turn.speech, session_id=session_id)
        return turn

    # ------------------------------------------------------------------
    async def speak(self, text: str, *, session_id: str = "", voice: bool = True) -> None:
        """Announce something (the voice layer / browser TTS picks this up)."""
        if not text:
            return
        await self.bus.publish(AgentEvent(type=EventType.SPEECH, session_id=session_id,
                                          payload={"text": text, "voice": voice}))
        if self.on_speak is not None:
            try:
                result = self.on_speak(text)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:  # noqa: BLE001
                pass

    async def notify(self, title: str, body: str, *, session_id: str = "", speak: bool = True) -> None:
        """Push a notification (used by the reminder scheduler)."""
        try:
            self.backend.notify(title, body)
        except Exception:  # noqa: BLE001
            pass
        await self.bus.publish(AgentEvent(type=EventType.NOTIFICATION, session_id=session_id,
                                          payload={"title": title, "body": body}))
        if speak:
            await self.speak(body, session_id=session_id)

    # ------------------------------------------------------------------
    async def greet(self, *, session_id: str = "") -> Turn:
        """First-contact greeting that also surfaces today's agenda."""
        session_id = session_id or self._last_session or _new_session()
        self._last_session = session_id
        hour = datetime.now().hour
        part = "morning" if 5 <= hour < 12 else "afternoon" if hour < 17 else "evening"
        name = self.memory.profile().get("name") or self.settings.user_name
        greeting = f"Good {part}" + (f", {name}" if name else "") + ". "

        agenda: ToolResult | None = None
        spec = self.registry.get("get_agenda")
        if spec is not None and spec.enabled:
            agenda = await self.executor.registry.execute(
                ToolCall(name="get_agenda", arguments={"window": "today"}, source="agent"),
                self._make_context(session_id=session_id),
            )
        detail = ""
        if agenda and agenda.ok and agenda.data:
            data = agenda.data
            counts = [f"{len(data.get('reminders', []))} reminder(s)", f"{len(data.get('events', []))} event(s)",
                      f"{len(data.get('tasks', []))} open task(s)"]
            if any(not c.startswith("0") for c in counts):
                detail = "You have " + ", ".join(c for c in counts if not c.startswith("0")) + " today."
        response = (greeting + detail + " What would you like me to do?").strip()

        turn = Turn(session_id=session_id, user_text="(greeting)", response=response, speech=response,
                    intent="greet", provider=getattr(self.brain, "name", "?"))
        await self.bus.publish(AgentEvent(type=EventType.AGENT_MESSAGE, session_id=session_id,
                                          payload={"text": response, "turn_id": turn.id, "greeting": True}))
        await self.speak(response, session_id=session_id)
        self.memory.push(session_id, "assistant", response)
        return turn

    async def briefing(self, *, session_id: str = "", window: str = "today") -> Turn:
        """Proactive briefing (Phase 6): reminders + calendar + tasks."""
        return await self.process(f"give me my {window} agenda", session_id=session_id)

    # ------------------------------------------------------------------
    def pending_confirmations(self, session_id: str = "") -> list[dict[str, Any]]:
        rows = self.db.list_pending(session_id or None) if self.db is not None else []
        live = []
        for row in rows:
            if row.get("expires_at", 0) < time.time():
                self.db.resolve_pending(row["token"], "expired")
                continue
            live.append(row)
        return live

    def reset_session(self, session_id: str) -> None:
        self.memory.clear(session_id)
        self.context.invalidate()

    def close(self) -> None:
        try:
            self.db.close()
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _new_session() -> str:
    return f"s_{datetime.now().strftime('%Y%m%d-%H%M%S')}_{int(time.time() * 1000) % 1000:03d}"


def _json_dumps(value: Any) -> str:
    import json

    try:
        return json.dumps(value or {}, ensure_ascii=False)
    except (TypeError, ValueError):
        return "{}"


def _compose_offline_reply(results: list[ToolResult], decision: Decision) -> str:
    """Build a spoken reply from tool results when there is no LLM to write one."""
    if not results:
        return decision.text or "Done."
    spoken = [r.spoken for r in results if r.spoken]
    if not spoken:
        return decision.text or "Done."
    if len(spoken) == 1:
        return spoken[0]
    return f"{spoken[0]} " + " ".join(f"Also: {s}" for s in spoken[1:])


def _fallback_reply(turn: Turn) -> str:
    if turn.tool_results:
        last = turn.tool_results[-1]
        return last.message or ("Done." if last.ok else "That didn't work.")
    return "Done."


def _spoken_form(turn: Turn) -> str:
    """Pick the best thing to say aloud."""
    if turn.pending is not None:
        return _strip_markdown(turn.pending.prompt)
    spoken = [r.spoken for r in turn.tool_results if r.spoken]
    if turn.response and not spoken:
        return _strip_markdown(turn.response)
    if spoken and turn.response and _is_richer(turn.response, spoken[0]):
        return _strip_markdown(turn.response)
    if spoken:
        return _strip_markdown(" ".join(dict.fromkeys(spoken)))
    return _strip_markdown(turn.response or "Done.")


def _is_richer(response: str, spoken: str) -> bool:
    return len(response.split()) > len(spoken.split()) + 4


def _strip_markdown(text: str) -> str:
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\*\*|\*|__|_#|^#+\s|\[([^\]]+)\]\([^)]+\)", lambda m: m.group(1) or "", text,
                  flags=re.MULTILINE)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


__all__ = ["Jarvis", "JarvisOptions"]
