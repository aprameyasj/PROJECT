"""Reminder tools — the Phase-3 productivity feature.

Flow:  speech -> intent -> `create_reminder` -> SQLite -> APScheduler job ->
at the right moment the scheduler emits a `reminder_fired` event, which the
dashboard shows, the notification backend posts, and the voice layer speaks.

`when` accepts either ISO-8601 ("2026-10-01T09:00") or natural language
("tomorrow at 9am", "in 45 minutes", "next monday 3pm") — the same parser the
offline intent engine uses, so both paths agree.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Literal

from ..core.timeparse import (
    countdown,
    day_bounds,
    humanize,
    humanize_duration,
    next_occurrence,
    parse_when,
    week_bounds,
)
from ..core.types import Permission, ToolCategory, ToolResult
from .registry import ToolContext, tool


def _resolve_when(value: Any, *, now: datetime | None = None) -> tuple[datetime | None, str]:
    """Accept ISO or natural language. Returns (datetime, human description)."""
    now = now or datetime.now()
    if isinstance(value, datetime):
        return value, humanize(value, now=now)
    text = str(value or "").strip()
    if not text:
        return None, ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", ""))
        return parsed, humanize(parsed, now=now)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y %H:%M", "%H:%M"):
        try:
            parsed = datetime.strptime(text, fmt)
            if fmt == "%H:%M":
                parsed = datetime.combine(now.date(), parsed.time())
                if parsed <= now:
                    parsed += timedelta(days=1)
            return parsed, humanize(parsed, now=now)
        except ValueError:
            continue
    result = parse_when(text, now=now)
    return result.when, humanize(result.when, now=now) if result.when else ""


@tool(
    permission=Permission.SAFE,
    category=ToolCategory.PRODUCTIVITY,
    examples=["Remind me tomorrow at 9 AM to submit my assignment"],
)
def create_reminder(
    ctx: ToolContext,
    task: str,
    when: str = "",
    recurrence: Literal["", "daily", "weekdays", "weekly", "weekend"] = "",
    channel: Literal["all", "notification", "voice", "dashboard"] = "all",
    notes: str = "",
) -> ToolResult:
    """Create a reminder that JARVIS will announce at a specific time.

    Args:
        task: What to be reminded about, e.g. "submit my assignment".
        when: ISO datetime ("2026-10-01T09:00") or natural language ("tomorrow at 9am", "in 30 minutes").
        recurrence: "" for one-off, or daily / weekdays / weekly / weekend.
        channel: How to deliver it: notification, voice, dashboard, or all.
        notes: Optional extra detail stored with the reminder.
    """
    if not task.strip():
        return ToolResult(ok=False, message="Tell me what to remind you about.")

    when_dt, human = _resolve_when(when)
    if when_dt is None:
        return ToolResult(ok=False, message=f"I couldn't understand the time “{when}”.",
                          speech="When should I remind you?")
    if when_dt < datetime.now() - timedelta(minutes=1):
        return ToolResult(ok=False, message=f"{human} is already in the past.",
                          speech=f"{human.title()} has already passed. When should I remind you instead?",
                          data={"when": when_dt.isoformat()})

    reminder = ctx.db.add_reminder(task, when_dt, recurrence=recurrence, channel=channel, notes=notes,
                                   timezone_name=ctx.settings.timezone)
    if ctx.scheduler is not None:
        ctx.scheduler.schedule_reminder(reminder)

    payload = {
        "id": reminder["id"],
        "task": task,
        "remind_at": reminder["remind_at"],
        "human": human,
        "countdown": countdown(when_dt),
        "recurrence": recurrence,
        "channel": channel,
    }
    speech = f"Okay, I'll remind you {human}" + (f", {recurrence}" if recurrence else "") + "."
    return ToolResult(ok=True, message=f"Reminder #{reminder['id']} set for {human}", speech=speech, data=payload)


@tool(permission=Permission.SAFE, category=ToolCategory.PRODUCTIVITY,
      examples=["What are my reminders?"])
def list_reminders(ctx: ToolContext, status: Literal["pending", "fired", "cancelled", "all"] = "pending",
                   limit: int = 20, window: Literal["", "today", "week", "month"] = "") -> ToolResult:
    """List reminders.

    Args:
        status: Which reminders: pending, fired, cancelled or all.
        limit: Maximum number to return.
        window: Optional filter: today, week or month.
    """
    reminders = ctx.db.list_reminders(status=None if status == "all" else status, limit=max(1, min(limit, 100)))
    if window:
        start, end = {"today": day_bounds(), "week": week_bounds(),
                      "month": (datetime.now().replace(day=1), datetime.now().replace(day=1) + timedelta(days=31))}[window]
        reminders = [r for r in reminders if _in_window(r["remind_at"], start, end)]

    enriched = []
    for reminder in reminders:
        when_dt, human = _resolve_when(reminder["remind_at"])
        enriched.append({
            "id": reminder["id"],
            "task": reminder["task"],
            "remind_at": reminder["remind_at"],
            "human": human,
            "countdown": countdown(when_dt) if when_dt else "",
            "status": reminder["status"],
            "recurrence": reminder["recurrence"],
        })

    if not enriched:
        return ToolResult(ok=True, message="No reminders.", speech="You have no reminders.",
                          data={"reminders": [], "count": 0})

    spoken = "; ".join(f"{r['task']} {r['countdown']}" for r in enriched[:3])
    return ToolResult(
        ok=True,
        message=f"{len(enriched)} reminder(s)",
        speech=f"You have {len(enriched)} reminder" + ("s" if len(enriched) != 1 else "") + f": {spoken}.",
        data={"reminders": enriched, "count": len(enriched)},
    )


def _in_window(iso: str, start: datetime, end: datetime) -> bool:
    when, _ = _resolve_when(iso)
    return bool(when and start <= when <= end)


@tool(permission=Permission.SAFE, category=ToolCategory.PRODUCTIVITY)
def cancel_reminder(ctx: ToolContext, reminder_id: int | None = None, needle: str = "",
                    all_pending: bool = False) -> ToolResult:
    """Cancel a reminder by id or by matching its text.

    Args:
        reminder_id: Numeric reminder id.
        needle: Text to match against reminder tasks, e.g. "assignment".
        all_pending: Cancel every pending reminder (use with care).
    """
    if all_pending:
        pending = ctx.db.list_reminders(status="pending", limit=200)
        for reminder in pending:
            ctx.db.update_reminder(reminder["id"], status="cancelled")
            if ctx.scheduler:
                ctx.scheduler.remove_reminder(reminder["id"])
        return ToolResult(ok=True, message=f"Cancelled {len(pending)} reminders",
                          speech=f"Cancelled all {len(pending)} reminders.",
                          data={"cancelled": [r["id"] for r in pending]})

    matches: list[dict[str, Any]] = []
    if reminder_id:
        found = ctx.db.get_reminder(int(reminder_id))
        matches = [found] if found else []
    elif needle:
        matches = [r for r in ctx.db.search_reminders(needle) if r["status"] == "pending"]

    if not matches:
        return ToolResult(ok=False, message=f"No reminder matching “{needle or reminder_id}”.",
                          speech="I couldn't find that reminder.")
    if len(matches) > 1:
        listing = ", ".join(f"#{r['id']} {r['task']}" for r in matches[:5])
        return ToolResult(ok=False, message=f"{len(matches)} reminders match: {listing}",
                          speech=f"That matched {len(matches)} reminders. Which one?",
                          data={"candidates": [{"id": r["id"], "task": r["task"]} for r in matches]})

    reminder = matches[0]
    ctx.db.update_reminder(reminder["id"], status="cancelled")
    if ctx.scheduler:
        ctx.scheduler.remove_reminder(reminder["id"])
    return ToolResult(ok=True, message=f"Cancelled reminder #{reminder['id']}: {reminder['task']}",
                      speech=f"Cancelled the reminder to {reminder['task']}.",
                      data={"id": reminder["id"], "task": reminder["task"]})


@tool(permission=Permission.SAFE, category=ToolCategory.PRODUCTIVITY)
def complete_reminder(ctx: ToolContext, needle: str = "", reminder_id: int | None = None) -> ToolResult:
    """Mark a reminder as done.

    Args:
        needle: Text to match, e.g. "assignment".
        reminder_id: Numeric reminder id.
    """
    matches: list[dict[str, Any]] = []
    if reminder_id:
        found = ctx.db.get_reminder(int(reminder_id))
        matches = [found] if found else []
    elif needle:
        matches = [r for r in ctx.db.search_reminders(needle) if r["status"] in {"pending", "fired"}]
    if not matches:
        return ToolResult(ok=False, message=f"No reminder matching “{needle or reminder_id}”.",
                          speech="I couldn't find that reminder.")
    reminder = matches[0]
    ctx.db.update_reminder(reminder["id"], status="fired", fired_at=datetime.now().isoformat(timespec="seconds"))
    if ctx.scheduler:
        ctx.scheduler.remove_reminder(reminder["id"])
    return ToolResult(ok=True, message=f"Marked reminder #{reminder['id']} as done",
                      speech=f"Nice. {reminder['task']} is done.", data={"id": reminder["id"]})


@tool(permission=Permission.SAFE, category=ToolCategory.PRODUCTIVITY)
def snooze_reminder(ctx: ToolContext, minutes: int = 10, needle: str = "",
                    reminder_id: int | None = None) -> ToolResult:
    """Postpone a reminder by a number of minutes.

    Args:
        minutes: How many minutes to postpone (default 10).
        needle: Text to match, e.g. "assignment".
        reminder_id: Numeric reminder id.
    """
    matches: list[dict[str, Any]] = []
    if reminder_id:
        found = ctx.db.get_reminder(int(reminder_id))
        matches = [found] if found else []
    elif needle:
        matches = [r for r in ctx.db.search_reminders(needle) if r["status"] == "pending"]
    else:
        matches = ctx.db.list_reminders(status="pending", limit=1)[:1]

    if not matches:
        return ToolResult(ok=False, message="No matching reminder to snooze.", speech="Nothing to snooze.")

    reminder = matches[0]
    current, _ = _resolve_when(reminder["remind_at"])
    if current is None:
        current = datetime.now()
    updated = current + timedelta(minutes=max(1, min(int(minutes), 60 * 24 * 7)))
    ctx.db.update_reminder(reminder["id"], remind_at=updated.isoformat(timespec="seconds"), status="pending")
    if ctx.scheduler:
        reminder = ctx.db.get_reminder(reminder["id"]) or reminder
        ctx.scheduler.schedule_reminder(reminder)
    return ToolResult(ok=True, message=f"Snoozed “{reminder['task']}” by {minutes} minutes",
                      speech=f"Okay, I'll remind you again {countdown(updated)}.",
                      data={"id": reminder["id"], "remind_at": updated.isoformat(timespec="seconds")})


@tool(permission=Permission.SAFE, category=ToolCategory.PRODUCTIVITY)
def get_agenda(ctx: ToolContext, window: Literal["today", "tomorrow", "week"] = "today") -> ToolResult:
    """Build the daily briefing: reminders, calendar events and open tasks.

    Args:
        window: "today", "tomorrow" or "week".
    """
    now = datetime.now()
    if window == "tomorrow":
        start, end = day_bounds((now + timedelta(days=1)).date())
    elif window == "week":
        start, end = week_bounds(now)
    else:
        start, end = day_bounds(now.date())

    reminders = [r for r in ctx.db.list_reminders(status="pending", limit=50)
                 if _in_window(r["remind_at"], start, end)]
    events = ctx.db.list_events(start=start, end=end, limit=50)
    tasks = ctx.db.list_tasks(status="todo", limit=10)

    lines = []
    for event in events:
        when, human = _resolve_when(event["starts_at"])
        lines.append(f"{when.strftime('%H:%M') if when else '?'} — {event['title']} (event)")
    for reminder in reminders:
        when, _ = _resolve_when(reminder["remind_at"])
        lines.append(f"{when.strftime('%H:%M') if when else '?'} — {reminder['task']} (reminder)")
    lines.sort()

    data = {
        "window": window,
        "events": events,
        "reminders": reminders,
        "tasks": tasks,
        "timeline": lines,
    }
    if not lines and not tasks:
        return ToolResult(ok=True, message="Nothing scheduled.",
                          speech=f"Your {window} is clear. Nothing scheduled.", data=data)

    greeting = "Good morning" if now.hour < 12 else "Good afternoon" if now.hour < 17 else "Good evening"
    spoken = [f"{r['task']} {countdown(_resolve_when(r['remind_at'])[0] or now)}" for r in reminders[:2]]
    spoken += [f"{e['title']}" for e in events[:2]]
    speech = f"{greeting}. You have " + ", ".join(spoken[:3])
    if tasks:
        speech += f", and {len(tasks)} open task" + ("s" if len(tasks) != 1 else "")
    return ToolResult(ok=True, message=f"{len(lines)} scheduled item(s), {len(tasks)} open task(s)",
                      speech=speech + ".", data=data)
