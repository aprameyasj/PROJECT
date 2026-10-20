"""The offline intent engine: speech -> the right tool, with no API key."""

from __future__ import annotations

import pytest

from jarvis.core.types import Message
from jarvis.llm.offline import OfflineBrain
from jarvis.tools.registry import ToolRegistry


@pytest.fixture()
def brain(settings) -> OfflineBrain:
    from jarvis.core.agent import Jarvis

    jarvis = Jarvis(settings=settings)
    engine = OfflineBrain(settings, jarvis.registry)
    jarvis.close()
    return engine


async def decide(brain: OfflineBrain, text: str, registry: ToolRegistry | None = None):
    tools = registry.to_openai_tools() if registry is not None else None
    return await brain.decide([Message(role="user", content=text)], tools=tools, context={})


MVP_COMMANDS: list[tuple[str, str, dict]] = [
    ("Jarvis, open Chrome.", "open_application", {"application": "chrome"}),
    ("Jarvis, close this tab.", "close_current_tab", {}),
    ("Jarvis, open VS Code.", "open_application", {"application": "vscode"}),
    ("Jarvis, take a screenshot.", "take_screenshot", {}),
    ("Jarvis, search the web for AWS Lambda.", "search_web", {"query": "aws lambda"}),
    ("Jarvis, what time is it?", "get_time", {}),
    ("Jarvis, shut down.", "power", {"action": "shutdown"}),
    ("open the terminal", "open_application", {"application": "terminal"}),
    ("close firefox", "close_application", {"application": "firefox"}),
    ("type hello world", "type_text", {"text": "hello world"}),
    ("press enter", "press_key", {"key": "enter"}),
    ("press ctrl + s", "press_hotkey", {}),
    ("move mouse to 100, 200", "move_mouse", {"x": 100, "y": 200}),
    ("click at 340, 500", "click", {"x": 340, "y": 500}),
    ("scroll down", "scroll", {}),
    ("open github.com", "open_url", {}),
    ("go to http://localhost:3000", "open_url", {}),
    ("set the volume to 20", "set_volume", {"percent": 20}),
    ("mute", "set_volume", {"muted": True}),
    ("pause the music", "media_control", {}),
    ("what's my system status", "get_system_status", {}),
    ("what is today's date", "get_date", {}),
    ("what apps are open", "list_windows", {}),
    ("switch to the chrome window", "switch_window", {}),
    ("find my resume pdf", "find_file", {}),
    ("open notes.txt", "open_file", {}),
    ("list files in Downloads", "list_directory", {}),
    ("what can you do", "list_tools", {}),
    ("what's on my screen", "describe_screen", {}),
    ("prepare my workspace for coding", "run_workflow", {}),
    ("lock my screen", "power", {"action": "lock"}),
]


@pytest.mark.parametrize("text,tool,subset", MVP_COMMANDS)
async def test_mvp_commands_map_to_the_right_tool(brain: OfflineBrain, text: str, tool: str, subset: dict) -> None:
    decision = await decide(brain, text)
    assert decision.requires_tool, f"{text!r} produced no tool call (text={decision.text!r})"
    assert decision.tool_calls[0].name == tool, f"{text!r} -> {decision.tool_calls[0].name}"
    arguments = decision.tool_calls[0].arguments
    for key, value in subset.items():
        assert str(arguments.get(key, "")).lower() == str(value).lower(), f"{text!r}: {key}"


async def test_reminder_extracts_task_and_time(brain: OfflineBrain) -> None:
    decision = await decide(brain, "Jarvis, remind me tomorrow at 9 AM to submit my assignment")
    assert decision.tool_calls[0].name == "create_reminder"
    arguments = decision.tool_calls[0].arguments
    assert arguments["task"].lower() == "submit my assignment"
    assert arguments["when"].endswith("T09:00")


async def test_reminder_without_a_time_asks_instead_of_guessing(brain: OfflineBrain) -> None:
    decision = await decide(brain, "remind me to buy milk")
    assert not decision.requires_tool
    assert "?" in decision.text


async def test_calendar_event_extracts_title_and_range(brain: OfflineBrain) -> None:
    decision = await decide(brain, "schedule a meeting with the team tomorrow at 3pm")
    assert decision.tool_calls[0].name == "create_calendar_event"
    arguments = decision.tool_calls[0].arguments
    assert arguments["starts_at"].endswith("T15:00")
    assert arguments["ends_at"] > arguments["starts_at"]
    assert "team" in arguments["title"]


async def test_memory_commands(brain: OfflineBrain) -> None:
    remember = await decide(brain, "remember that my project is called Smart Water Grid")
    assert remember.tool_calls[0].name == "remember_fact"
    assert remember.tool_calls[0].arguments["value"].lower().startswith("smart water grid")

    recall = await decide(brain, "what do you know about me")
    assert recall.tool_calls[0].name == "recall_fact"


async def test_greetings_and_thanks_do_not_call_tools(brain: OfflineBrain) -> None:
    for text in ("hello", "good morning jarvis", "thanks!", "who are you"):
        decision = await decide(brain, text)
        assert not decision.requires_tool, text
        assert decision.text


async def test_unmatched_text_falls_back_to_help(brain: OfflineBrain) -> None:
    decision = await decide(brain, "blah blah do the thing")
    assert not decision.requires_tool
    assert "couldn't map" in decision.text.lower() or "offline" in decision.text.lower()


async def test_questions_without_a_key_become_web_searches(brain: OfflineBrain) -> None:
    decision = await decide(brain, "what is the capital of Australia")
    assert decision.tool_calls[0].name == "search_web"
    assert decision.tool_calls[0].arguments["query"]


async def test_respects_the_enabled_tool_subset(brain: OfflineBrain, settings) -> None:
    from jarvis.core.agent import Jarvis

    jarvis = Jarvis(settings=settings)
    jarvis.registry.disable(["take_screenshot"])
    tools = jarvis.registry.to_openai_tools()
    decision = await brain.decide([Message(role="user", content="take a screenshot")], tools=tools)
    names = [call.name for call in decision.tool_calls]
    assert "take_screenshot" not in names
    jarvis.close()


async def test_offline_planner_uses_workflows(brain: OfflineBrain) -> None:
    plan = await brain.plan("prepare my workspace for coding", catalogue="")
    assert plan["steps"], plan
    assert plan["steps"][0]["tool"] == "open_application"


async def test_wake_word_and_politeness_are_stripped(brain: OfflineBrain) -> None:
    for text in ("hey jarvis, please close this tab", "Jarvis... close this tab!", "ok jarvis close this tab"):
        decision = await decide(brain, text)
        assert decision.tool_calls and decision.tool_calls[0].name == "close_current_tab", text


async def test_destructive_phrases_still_route_through_the_gate(brain: OfflineBrain) -> None:
    """The parser may understand it; the safety gate decides whether it runs."""
    decision = await decide(brain, "run command rm -rf /")
    assert decision.tool_calls[0].name == "run_command"
    assert decision.tool_calls[0].arguments["command"] == "rm -rf /"
