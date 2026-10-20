"""End-to-end agent behaviour: listen -> understand -> act -> observe -> respond."""

from __future__ import annotations

import pytest

from jarvis.core.types import EventType


async def test_close_this_tab_executes_and_reports(agent) -> None:
    turn = await agent.process("Jarvis, close this tab.", speak=False)

    assert turn.ok is True
    assert turn.intent == "close_tab"
    assert [call.name for call in turn.tool_calls] == ["close_current_tab"]
    assert turn.tool_results[0].ok is True
    assert turn.speech  # something speakable came back
    assert turn.simulated is True  # sandbox backend in tests


async def test_open_application_reaches_the_backend(agent) -> None:
    turn = await agent.process("open VS Code", speak=False)

    assert turn.tool_calls[0].name == "open_application"
    assert turn.tool_calls[0].arguments["application"] == "vscode"
    assert "vscode" in agent.backend.state["running_apps"]
    assert any(w["app"] == "vscode" for w in agent.backend.list_windows())


async def test_screenshot_is_saved_and_recorded(agent) -> None:
    turn = await agent.process("take a screenshot", speak=False)

    result = turn.tool_results[0]
    assert result.ok
    assert result.artifacts and result.artifacts[0]["type"] == "image"
    from pathlib import Path

    assert Path(result.artifacts[0]["path"]).exists()
    assert agent.db.list_artifacts("screenshot", limit=5)


async def test_reminder_is_persisted(agent) -> None:
    turn = await agent.process("remind me tomorrow at 9 AM to submit my assignment", speak=False)

    assert turn.ok
    reminders = agent.db.list_reminders(status="pending")
    assert len(reminders) == 1
    assert reminders[0]["task"] == "submit my assignment"
    assert reminders[0]["remind_at"].endswith("T09:00:00")
    assert "9:00 AM" in turn.speech


async def test_level_two_action_pauses_for_confirmation(agent) -> None:
    turn = await agent.process("Jarvis, shut down.", speak=False)

    assert turn.pending is not None
    assert turn.pending.call.name == "power"
    assert "?" in turn.response
    # nothing has happened yet
    assert agent.backend.state["power"] == "on"
    # the pause is durable: it survived a round-trip through SQLite
    assert agent.db.list_pending(turn.session_id)

    approved = await agent.confirm(turn.pending.token, True, session_id=turn.session_id, speak=False)
    assert approved.ok is True
    assert agent.backend.state["power"] == "shutdown"


async def test_declining_a_confirmation_does_nothing(agent) -> None:
    turn = await agent.process("restart the computer", speak=False)
    assert turn.pending is not None

    denied = await agent.confirm(turn.pending.token, False, session_id=turn.session_id, speak=False)
    assert denied.ok is True
    assert "cancel" in denied.response.lower()
    assert agent.backend.state["power"] == "on"


async def test_spoken_yes_resolves_a_pending_action(agent) -> None:
    session = "voice-session"
    turn = await agent.process("shut down my computer", session_id=session, speak=False)
    assert turn.pending is not None

    reply = await agent.process("yes", session_id=session, speak=False)
    assert reply.intent.startswith("confirm:")
    assert agent.backend.state["power"] == "shutdown"


async def test_level_three_is_blocked_and_audited(agent) -> None:
    turn = await agent.process("run command rm -rf /", speak=False)

    assert turn.ok is False
    assert turn.steps[0]["status"] == "blocked"
    assert "safety" in turn.response.lower() or "blocked" in turn.response.lower()
    audit = agent.db.audit_log(limit=10)
    assert any(entry["decision"] == "block" and entry["tool"] == "run_command" for entry in audit)


async def test_protected_path_is_blocked(agent) -> None:
    turn = await agent.process("delete the file /etc/passwd", speak=False)
    assert turn.ok is False
    assert turn.steps[0]["status"] == "blocked"


async def test_unknown_tools_cannot_be_invoked(agent) -> None:
    from jarvis.core.types import ToolCall

    outcome = await agent.executor.execute(ToolCall(name="format_everything", arguments={}), session_id="t")
    assert outcome.status == "failed"
    assert "Unknown tool" in (outcome.result.error or "")


async def test_workflow_runs_a_multi_step_plan(agent) -> None:
    turn = await agent.process("prepare my workspace for coding", speak=False)

    assert turn.tool_calls[0].name == "run_workflow"
    result = turn.tool_results[0]
    plan = result.data["plan"]
    assert len(plan["steps"]) >= 3
    assert plan["steps"][0]["tool"] == "open_application"
    assert "vscode" in agent.backend.state["running_apps"]
    assert any("localhost:3000" in str(tab) for tab in agent.backend.state["tabs"])


async def test_planner_can_build_a_plan_without_executing(agent) -> None:
    plan, draft = await agent.planner.create_plan("prepare my workspace for coding", session_id="p")
    assert draft.source.startswith("workflow:")
    assert plan.steps
    assert all(step.call.name in agent.registry for step in plan.steps)


async def test_planner_drops_unknown_tools(agent) -> None:
    from jarvis.core.types import Plan, PlanStep, ToolCall

    plan = Plan(
        title="test",
        steps=[
            PlanStep(call=ToolCall(name="open_application", arguments={"application": "chrome"})),
            PlanStep(call=ToolCall(name="self_destruct", arguments={})),
        ],
    )
    validated = agent.planner.validate(plan)
    assert [step.call.name for step in validated.steps] == ["open_application"]


async def test_memory_absorbs_a_name_and_uses_it_later(agent) -> None:
    await agent.process("my name is Aprameya", speak=False)
    assert agent.memory.profile()["name"].lower() == "aprameya"

    turn = await agent.process("what do you know about me", speak=False)
    assert "aprameya" in turn.response.lower() or "aprameya" in str(turn.tool_results[0].data).lower()


async def test_remember_and_recall_tools(agent) -> None:
    created = await agent.process("remember that my favourite IDE is VS Code", speak=False)
    assert created.tool_calls[0].name == "remember_fact"

    recalled = await agent.process("what do you know about me", speak=False)
    blob = str(recalled.tool_results[0].data).lower()
    assert "vs code" in blob or "ide" in blob


async def test_short_term_memory_is_scoped_to_a_session(agent) -> None:
    await agent.process("open chrome", session_id="a", speak=False)
    await agent.process("open firefox", session_id="b", speak=False)

    assert len(agent.memory.window("a").messages) >= 2
    assert any("firefox" in str(m.get("content", "")).lower() for m in agent.memory.window("b").messages)
    assert not any("firefox" in str(m.get("content", "")).lower() for m in agent.memory.window("a").messages)


async def test_events_are_published_for_every_stage(agent) -> None:
    seen: list[str] = []
    agent.bus.subscribe(lambda event: seen.append(event.type.value))

    await agent.process("take a screenshot", speak=False)

    assert "user_message" in seen
    assert "tool_call" in seen
    assert "tool_result" in seen
    assert "agent_message" in seen


async def test_speech_events_are_emitted_when_enabled(agent) -> None:
    spoken: list[str] = []
    agent.bus.subscribe(lambda event: spoken.append(event.payload.get("text", "")),
                        events=[EventType.SPEECH])

    await agent.process("what time is it", speak=True)
    assert spoken and spoken[-1]


async def test_loop_guard_stops_repetition(agent) -> None:
    from jarvis.core.safety import LoopGuard
    from jarvis.core.types import ToolCall

    guard = LoopGuard(max_repeats=1)
    call = ToolCall(name="take_screenshot", arguments={})
    assert guard.register(call)[0] is True
    assert guard.register(call)[0] is False


async def test_step_budget_is_respected(agent, settings) -> None:
    """Even a pathological loop must terminate with a report, not hang."""
    settings.max_steps = 2

    from jarvis.llm.base import Decision
    from jarvis.core.types import ToolCall as TC

    class StuckBrain:
        name = "stuck"
        available = True

        def describe(self):
            return {"name": "stuck"}

        async def decide(self, messages, *, tools=None, catalogue="", context=None):
            return Decision(tool_calls=[TC(name="get_time", arguments={})], intent="stuck", provider="llm")

        async def plan(self, goal, *, catalogue, context=""):
            return {"steps": []}

        async def summarise(self, *, user_text, observations):
            return ""

    agent.brain = StuckBrain()
    turn = await agent.process("loop forever", speak=False)
    assert turn.duration_ms < 10000
    assert len(turn.tool_results) <= settings.max_steps


async def test_tool_failures_are_reported_not_raised(agent) -> None:
    turn = await agent.process("open the file /definitely/not/here.xyz", speak=False)
    assert turn.ok is False or "couldn't" in turn.response.lower() or "not" in turn.response.lower()


async def test_audit_trail_records_every_call(agent) -> None:
    await agent.process("open chrome", speak=False)
    await agent.process("take a screenshot", speak=False)
    await agent.process("run command rm -rf /", speak=False)

    stats = agent.db.stats()
    assert stats["tool_calls"] >= 3
    assert stats["blocked"] >= 1


async def test_describe_reports_the_full_runtime(agent) -> None:
    report = agent.describe()
    assert report["tools"] > 40
    assert report["backend"]["name"] == "sandbox"
    assert report["settings"]["backend"] == "sandbox"
    assert report["brain"]["name"] == "offline"


async def test_greeting_includes_the_agenda(agent) -> None:
    await agent.process("remind me tomorrow at 9 AM to submit my assignment", speak=False)
    turn = await agent.greet()
    assert "reminder" in turn.response.lower() or "good" in turn.response.lower()


async def test_two_tools_in_one_utterance_are_both_executed(agent) -> None:
    """The model may emit several calls; the executor runs them in order."""
    from jarvis.core.types import ToolCall

    calls = [ToolCall(name="open_application", arguments={"application": "chrome"}),
             ToolCall(name="take_screenshot", arguments={})]
    results = []
    for call in calls:
        outcome = await agent.executor.execute(call, session_id="multi")
        results.append(outcome)
    assert all(outcome.ok for outcome in results)
    assert "chrome" in agent.backend.state["running_apps"]


@pytest.mark.parametrize("command", ["open chrome", "close this tab", "what time is it"])
async def test_idempotent_replay_of_mvp_commands(agent, command: str) -> None:
    first = await agent.process(command, session_id="replay", speak=False)
    second = await agent.process(command, session_id="replay", speak=False)
    assert first.ok == second.ok
    assert second.tool_calls[0].name == first.tool_calls[0].name
