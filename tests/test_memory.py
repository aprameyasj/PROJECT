"""Memory: short-term windows, long-term facts, retrieval."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from jarvis.core.memory import Memory, normalise_category


@pytest.fixture()
def memory(agent) -> Memory:
    return agent.memory


async def test_remember_and_recall(memory: Memory) -> None:
    await memory.remember("preferred_ide", "VS Code", category="preference")
    hits = await memory.recall("which IDE do I like")
    assert hits and hits[0].value == "VS Code"


async def test_upsert_does_not_duplicate(memory: Memory) -> None:
    await memory.remember("project", "Smart Water Grid", category="project")
    await memory.remember("project", "Hotel Booking", category="project")
    facts = [f for f in memory.db.get_facts(category="project") if f["key"] == "project"]
    assert len(facts) == 1
    assert facts[0]["value"] == "Hotel Booking"


async def test_profile_is_exposed_for_prompts(memory: Memory) -> None:
    await memory.remember("name", "Aprameya", category="profile")
    await memory.remember("preferred_language", "Python", category="profile")
    profile = memory.profile()
    assert profile["name"] == "Aprameya"
    assert profile["preferred_language"] == "Python"


async def test_forget_removes_a_memory(memory: Memory) -> None:
    await memory.remember("phone", "98765 43210", category="profile")
    removed = await memory.forget("phone")
    assert removed == 1
    assert memory.db.get_fact("phone", "profile") is None


async def test_absorb_extracts_durable_facts(memory: Memory) -> None:
    stored = await memory.absorb("My name is Aprameya and I prefer Python. I'm working on Smart Water Grid.")
    keys = {item["key"] for item in stored}
    assert "name" in keys
    assert "preference" in keys or "current_project" in keys
    assert memory.profile()["name"].lower() == "aprameya"


async def test_absorb_ignores_chatter(memory: Memory) -> None:
    assert await memory.absorb("close this tab please") == []


def test_category_normalisation() -> None:
    assert normalise_category("preferences") == "preference"
    assert normalise_category("projects") == "project"
    assert normalise_category("weird") == "weird"
    assert normalise_category("") == "general"


async def test_short_term_window_is_ordered_and_bounded(memory: Memory) -> None:
    for index in range(30):
        memory.push("session", "user", f"message {index}")
    window = memory.window("session", limit=10)
    assert len(window.messages) == 10
    assert window.messages[-1]["content"] == "message 29"
    assert "message 25" in window.to_prompt(limit=10)


async def test_short_term_memory_survives_a_restart(agent, settings) -> None:
    agent.memory.push("persisted", "user", "open chrome")
    agent.memory.push("persisted", "assistant", "Opening chrome.")

    fresh = Memory(agent.db, settings)
    window = fresh.window("persisted")
    assert [m["content"] for m in window.messages] == ["open chrome", "Opening chrome."]


async def test_clear_session(memory: Memory) -> None:
    memory.push("wipe", "user", "hello")
    memory.clear("wipe")
    assert memory.window("wipe").messages == []


async def test_context_block_renders_facts(memory: Memory) -> None:
    await memory.remember("goal", "placements", category="goal")
    block = await memory.context_block()
    assert "goal" in block and "placements" in block


async def test_empty_memory_does_not_break_prompts(memory: Memory) -> None:
    memory.db.wipe(["facts"])
    block = await memory.context_block()
    assert "no long-term memory" in block.lower()


async def test_recall_ranks_relevant_memories_first(memory: Memory) -> None:
    await memory.remember("project_alpha", "Smart Water Grid", category="project")
    await memory.remember("project_beta", "Hotel Booking", category="project")
    await memory.remember("favourite_food", "dosa", category="preference")
    hits = await memory.recall("water project", limit=3)
    assert hits and "water" in hits[0].value.lower()


async def test_reminders_and_memory_share_one_database(agent, memory: Memory) -> None:
    await memory.remember("name", "Aprameya", category="profile")
    agent.db.add_reminder("standup", datetime.now() + timedelta(hours=1))
    snapshot = agent.db.export_snapshot()
    assert snapshot["facts"] and snapshot["reminders"]
    assert snapshot["stats"]["pending_reminders"] == 1
