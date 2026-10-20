"""Reminders end to end: database -> scheduler -> notification + speech."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest

from jarvis.automation.scheduler import ReminderScheduler
from jarvis.core.types import EventType


@pytest.fixture()
def fired(agent) -> list[dict]:
    events: list[dict] = []

    def listener(event) -> None:
        if event.type in {EventType.REMINDER_FIRED, EventType.SPEECH}:
            events.append({"type": event.type.value, **event.payload})

    agent.bus.subscribe(listener)
    return events


async def test_reminder_fires_and_notifies(agent, fired) -> None:
    reminder = agent.db.add_reminder("stretch your legs", datetime.now() + timedelta(seconds=1))

    scheduler = ReminderScheduler(agent.db, agent.bus, notifier=lambda *_a, **_k: None, poll_seconds=0.2)
    await scheduler.start()
    try:
        for _ in range(60):
            if any(event["type"] == "reminder_fired" for event in fired):
                break
            await asyncio.sleep(0.1)
    finally:
        await scheduler.stop()

    assert any(event["type"] == "reminder_fired" and event["task"] == "stretch your legs" for event in fired)
    assert any(event["type"] == "speech" and "stretch your legs" in event["text"] for event in fired)
    assert agent.db.get_reminder(reminder["id"])["status"] == "fired"


async def test_missed_reminders_fire_on_startup(agent, fired) -> None:
    agent.db.add_reminder("you missed this", datetime.now() - timedelta(minutes=5))

    scheduler = ReminderScheduler(agent.db, agent.bus, poll_seconds=0.2)
    await scheduler.start()
    await asyncio.sleep(0.4)
    await scheduler.stop()

    assert any(event.get("missed") for event in fired if event["type"] == "reminder_fired")


async def test_recurring_reminders_reschedule(agent) -> None:
    scheduler = ReminderScheduler(agent.db, agent.bus, poll_seconds=0.2)
    agent.db.add_reminder("take your meds", datetime.now() + timedelta(seconds=1), recurrence="daily")
    await scheduler.start()
    try:
        for _ in range(60):
            if agent.db.list_reminders(status="pending"):
                break
            await asyncio.sleep(0.1)
        pending = agent.db.list_reminders(status="pending")
        assert pending, "a daily reminder should have been re-armed"
        assert pending[0]["task"] == "take your meds"
        assert datetime.fromisoformat(pending[0]["remind_at"]) > datetime.now()
    finally:
        await scheduler.stop()


async def test_cancelling_a_reminder_removes_the_job(agent) -> None:
    reminder = agent.db.add_reminder("cancelled", datetime.now() + timedelta(hours=1))
    scheduler = ReminderScheduler(agent.db, agent.bus, poll_seconds=0.2)
    await scheduler.start()
    try:
        scheduler.schedule_reminder(reminder)
        agent.db.update_reminder(reminder["id"], status="cancelled")
        scheduler.remove_reminder(reminder["id"])
        await asyncio.sleep(0.3)
        assert agent.db.get_reminder(reminder["id"])["status"] == "cancelled"
    finally:
        await scheduler.stop()


async def test_reminder_tools_round_trip(agent) -> None:
    from jarvis.core.types import ToolCall

    scheduler = ReminderScheduler(agent.db, agent.bus, poll_seconds=60)
    agent.attach_scheduler(scheduler)
    ctx = agent._make_context(session_id="t")  # noqa: SLF001

    when = (datetime.now() + timedelta(hours=2)).isoformat(timespec="minutes")
    created = await agent.registry.execute(
        ToolCall(name="create_reminder", arguments={"task": "call the bank", "when": when}), ctx
    )
    assert created.ok, created.message
    reminder_id = created.data["id"]

    listed = await agent.registry.execute(ToolCall(name="list_reminders", arguments={}), ctx)
    assert any(item["id"] == reminder_id for item in listed.data["reminders"])

    snoozed = await agent.registry.execute(
        ToolCall(name="snooze_reminder", arguments={"minutes": 30, "reminder_id": reminder_id}), ctx
    )
    assert snoozed.ok
    assert snoozed.data["remind_at"] > when

    done = await agent.registry.execute(
        ToolCall(name="complete_reminder", arguments={"reminder_id": reminder_id}), ctx
    )
    assert done.ok
    assert agent.db.get_reminder(reminder_id)["status"] == "fired"


async def test_calendar_event_creates_a_pre_reminder(agent) -> None:
    from jarvis.core.types import ToolCall

    agent.attach_scheduler(ReminderScheduler(agent.db, agent.bus, poll_seconds=60))
    ctx = agent._make_context(session_id="t")  # noqa: SLF001
    starts = (datetime.now() + timedelta(hours=3)).isoformat(timespec="minutes")

    result = await agent.registry.execute(
        ToolCall(name="create_calendar_event",
                 arguments={"title": "Interview at Google", "starts_at": starts, "remind_minutes_before": 30}),
        ctx,
    )
    assert result.ok, result.message
    assert result.data["reminder_id"]
    reminders = agent.db.list_reminders(status="pending")
    assert any("Interview at Google" in r["task"] for r in reminders)

    events = agent.db.list_events()
    assert events and events[0]["title"] == "Interview at Google"


async def test_agenda_combines_reminders_events_and_tasks(agent) -> None:
    from jarvis.core.types import ToolCall

    now = datetime.now()
    agent.db.add_reminder("submit assignment", now + timedelta(hours=1))
    agent.db.add_event("Team sync", now + timedelta(hours=2), ends_at=now + timedelta(hours=3))
    agent.db.add_task("Read DBMS chapter 4")

    ctx = agent._make_context(session_id="t")  # noqa: SLF001
    result = await agent.registry.execute(ToolCall(name="get_agenda", arguments={"window": "today"}), ctx)
    assert result.ok
    assert result.data["reminders"] and result.data["events"] and result.data["tasks"]
    assert result.data["timeline"]
    assert "Good" in result.speech


async def test_tasks_round_trip(agent) -> None:
    from jarvis.core.types import ToolCall

    ctx = agent._make_context(session_id="t")  # noqa: SLF001
    created = await agent.registry.execute(
        ToolCall(name="create_task", arguments={"title": "finish DBMS notes", "project": "college"}), ctx
    )
    assert created.ok
    listed = await agent.registry.execute(ToolCall(name="list_tasks", arguments={}), ctx)
    assert listed.data["count"] == 1
    completed = await agent.registry.execute(
        ToolCall(name="complete_task", arguments={"needle": "dbms"}), ctx
    )
    assert completed.ok
    assert agent.db.list_tasks(status="done")
