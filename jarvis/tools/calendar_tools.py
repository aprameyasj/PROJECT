"""Calendar and task tools.

The MVP keeps events and to-dos in SQLite. Both are written so a Google
Calendar / CalDAV / Notion sync can be added later by swapping the storage
calls — the tool signatures (and therefore the LLM contract) stay the same.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Literal

from ..core.timeparse import (
    day_bounds,
    guess_duration_from_title,
    humanize,
    month_bounds,
    parse_range,
    week_bounds,
)
from ..core.types import Permission, ToolCategory, ToolResult
from .registry import ToolContext, tool
from .reminders import _resolve_when


# ---------------------------------------------------------------------------
# calendar
# ---------------------------------------------------------------------------


@tool(permission=Permission.SAFE, category=ToolCategory.PRODUCTIVITY,
      examples=["Schedule a meeting with the team tomorrow at 3pm for an hour"])
def create_calendar_event(ctx: ToolContext, title: str, starts_at: str = "", ends_at: str = "",
                          duration_minutes: int = 0, location: str = "", notes: str = "",
                          remind_minutes_before: int = 15) -> ToolResult:
    """Create a calendar event, optionally with a reminder before it starts.

    Args:
        title: What the event is, e.g. "Interview at Google".
        starts_at: ISO datetime or natural language ("tomorrow at 3pm").
        ends_at: Optional ISO datetime or natural language end time.
        duration_minutes: Length in minutes (used when ends_at is missing).
        location: Optional place or meeting link.
        notes: Optional description.
        remind_minutes_before: Create a reminder this many minutes before the event (0 = none).
    """
    if not title.strip():
        return ToolResult(ok=False, message="The event needs a title.")

    start, human = _resolve_when(starts_at)
    if start is None:
        # maybe the whole phrase carried the time, e.g. "team sync tomorrow at 3"
        parsed, _ = parse_range(f"{title} {starts_at}")
        start, human = parsed.when, humanize(parsed.when) if parsed.when else ""
    if start is None:
        return ToolResult(ok=False, message="I couldn't work out when that starts.",
                          speech="When does that start?", data={"title": title})

    if ends_at:
        end, _ = _resolve_when(ends_at)
    elif duration_minutes:
        end = start + timedelta(minutes=max(5, min(int(duration_minutes), 24 * 60)))
    else:
        end = start + guess_duration_from_title(title)
    if end <= start:
        end = start + timedelta(minutes=30)

    event = ctx.db.add_event(title, start, ends_at=end, location=location, notes=notes)

    reminder = None
    if remind_minutes_before > 0:
        when = start - timedelta(minutes=int(remind_minutes_before))
        if when > datetime.now():
            reminder = ctx.db.add_reminder(f"{title} starts at {start.strftime('%H:%M')}", when,
                                           channel="all", notes=f"auto-reminder for event #{event['id']}",
                                           timezone_name=ctx.settings.timezone)
            if ctx.scheduler and reminder:
                ctx.scheduler.schedule_reminder(reminder)

    duration = humanize_duration_compact(end - start)
    return ToolResult(
        ok=True,
        message=f"Event “{title}” created for {human} ({duration})",
        speech=f"Added {title} to your calendar, {human}, for {duration}.",
        data={"event": event, "human": human, "duration": duration,
              "reminder_id": reminder["id"] if reminder else None},
    )


def humanize_duration_compact(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    if minutes < 60:
        return f"{minutes} min"
    hours, rest = divmod(minutes, 60)
    return f"{hours}h{f' {rest}m' if rest else ''}"


@tool(permission=Permission.SAFE, category=ToolCategory.PRODUCTIVITY)
def list_calendar_events(ctx: ToolContext, window: Literal["today", "tomorrow", "week", "month", "all"] = "today",
                         limit: int = 25) -> ToolResult:
    """List calendar events.

    Args:
        window: today, tomorrow, week, month or all.
        limit: Maximum number of events to return.
    """
    now = datetime.now()
    if window == "today":
        start, end = day_bounds(now.date())
    elif window == "tomorrow":
        start, end = day_bounds((now + timedelta(days=1)).date())
    elif window == "week":
        start, end = week_bounds(now)
    elif window == "month":
        start, end = month_bounds(now)
    else:
        start, end = now - timedelta(days=3650), now + timedelta(days=3650)

    events = ctx.db.list_events(start=start, end=end, limit=max(1, min(int(limit), 200)))
    enriched = []
    for event in events:
        when, human = _resolve_when(event["starts_at"])
        ends, _ = _resolve_when(event["ends_at"]) if event.get("ends_at") else (None, "")
        enriched.append({
            "id": event["id"],
            "title": event["title"],
            "starts_at": event["starts_at"],
            "ends_at": event.get("ends_at"),
            "human": human,
            "duration": humanize_duration_compact(ends - when) if (when and ends) else "",
            "location": event.get("location", ""),
        })

    if not enriched:
        return ToolResult(ok=True, message=f"No events {window}.",
                          speech=f"Nothing on your calendar {window}.", data={"events": [], "window": window})
    spoken = ", ".join(f"{e['title']} {e['human']}" for e in enriched[:3])
    return ToolResult(ok=True, message=f"{len(enriched)} event(s) {window}",
                      speech=f"{spoken}." + (" And more." if len(enriched) > 3 else ""),
                      data={"events": enriched, "window": window})


@tool(permission=Permission.SAFE, category=ToolCategory.PRODUCTIVITY)
def cancel_calendar_event(ctx: ToolContext, event_id: int | None = None, title: str = "") -> ToolResult:
    """Cancel a calendar event by id or by matching its title.

    Args:
        event_id: Numeric event id.
        title: Part of the event title to match.
    """
    events = ctx.db.list_events(limit=200)
    if event_id:
        matches = [e for e in events if e["id"] == int(event_id)]
    elif title:
        needle = title.lower()
        matches = [e for e in events if needle in e["title"].lower()]
    else:
        return ToolResult(ok=False, message="Tell me which event to cancel.")

    if not matches:
        return ToolResult(ok=False, message="No matching event.", speech="I couldn't find that event.")
    if len(matches) > 1:
        return ToolResult(ok=False,
                          message="Multiple matches: " + ", ".join(f"#{e['id']} {e['title']}" for e in matches[:6]),
                          speech=f"That matched {len(matches)} events. Which one?",
                          data={"candidates": [{"id": e["id"], "title": e["title"]} for e in matches]})

    event = matches[0]
    ctx.db.cancel_event(event["id"])
    return ToolResult(ok=True, message=f"Cancelled “{event['title']}”",
                      speech=f"Cancelled {event['title']}.", data={"id": event["id"], "title": event["title"]})


# ---------------------------------------------------------------------------
# tasks
# ---------------------------------------------------------------------------


@tool(permission=Permission.SAFE, category=ToolCategory.PRODUCTIVITY, examples=["Add a task to finish the DBMS notes"])
def create_task(ctx: ToolContext, title: str, project: str = "", priority: Literal[1, 2, 3, 4, 5] = 3,
                due: str = "", notes: str = "") -> ToolResult:
    """Add a to-do item to the task list.

    Args:
        title: The task, e.g. "finish DBMS notes".
        project: Optional project name to group it under.
        priority: 1 (highest) to 5 (lowest); default 3.
        due: Optional due date/time, ISO or natural language ("friday", "tomorrow 5pm").
        notes: Optional detail.
    """
    if not title.strip():
        return ToolResult(ok=False, message="The task needs a title.")
    due_at = _resolve_when(due)[0] if due else None
    task = ctx.db.add_task(title, project=project, priority=int(priority), due_at=due_at, notes=notes)
    speech = f"Added “{title}” to your tasks" + (f", due {humanize(due_at)}" if due_at else "") + "."
    return ToolResult(ok=True, message=f"Task #{task['id']} created", speech=speech, data={"task": task})


@tool(permission=Permission.SAFE, category=ToolCategory.PRODUCTIVITY)
def list_tasks(ctx: ToolContext, status: Literal["todo", "doing", "done", "all"] = "todo",
               project: str = "", limit: int = 25) -> ToolResult:
    """List to-do items.

    Args:
        status: todo, doing, done or all.
        project: Optional project filter.
        limit: Maximum number of tasks.
    """
    tasks = ctx.db.list_tasks(status=None if status == "all" else status,
                              project=project or None, limit=max(1, min(int(limit), 200)))
    if not tasks:
        return ToolResult(ok=True, message="No tasks.", speech="Your task list is empty.",
                          data={"tasks": [], "count": 0})
    spoken = ", ".join(t["title"] for t in tasks[:4])
    return ToolResult(
        ok=True,
        message=f"{len(tasks)} task(s)",
        speech=f"You have {len(tasks)} task" + ("s" if len(tasks) != 1 else "") + f": {spoken}.",
        data={"tasks": tasks, "count": len(tasks)},
    )


@tool(permission=Permission.SAFE, category=ToolCategory.PRODUCTIVITY)
def complete_task(ctx: ToolContext, needle: str = "", task_id: int | None = None,
                  status: Literal["done", "doing", "todo", "cancelled"] = "done") -> ToolResult:
    """Mark a task as done (or change its status).

    Args:
        needle: Text to match against task titles.
        task_id: Numeric task id.
        status: New status: done, doing, todo or cancelled.
    """
    tasks = ctx.db.list_tasks(status=None, limit=500)
    if task_id:
        matches = [t for t in tasks if t["id"] == int(task_id)]
    elif needle:
        lowered = needle.lower()
        matches = [t for t in tasks if lowered in t["title"].lower() and t["status"] != status]
    else:
        return ToolResult(ok=False, message="Tell me which task.")

    if not matches:
        return ToolResult(ok=False, message=f"No task matching “{needle or task_id}”.",
                          speech="I couldn't find that task.")
    if len(matches) > 1:
        return ToolResult(ok=False,
                          message="Multiple matches: " + ", ".join(f"#{t['id']} {t['title']}" for t in matches[:6]),
                          speech=f"That matched {len(matches)} tasks. Which one?",
                          data={"candidates": [{"id": t["id"], "title": t["title"]} for t in matches]})

    task = matches[0]
    updated = ctx.db.set_task_status(task["id"], status)
    verb = {"done": "Finished", "doing": "Started", "todo": "Reopened", "cancelled": "Dropped"}[status]
    return ToolResult(ok=True, message=f"{verb} task #{task['id']}: {task['title']}",
                      speech=f"{verb} {task['title']}.", data={"task": updated})


@tool(permission=Permission.SAFE, category=ToolCategory.PRODUCTIVITY)
def delete_task(ctx: ToolContext, task_id: int) -> ToolResult:
    """Remove a task from the list entirely.

    Args:
        task_id: Numeric task id to delete.
    """
    task = next((t for t in ctx.db.list_tasks(status=None, limit=500) if t["id"] == int(task_id)), None)
    if not task:
        return ToolResult(ok=False, message=f"No task with id {task_id}.")
    ctx.db.set_task_status(int(task_id), "cancelled")
    return ToolResult(ok=True, message=f"Removed task #{task_id}", speech=f"Removed {task['title']}.",
                      data={"task": task})


# ---------------------------------------------------------------------------
# free/busy helper used by the planner
# ---------------------------------------------------------------------------


def busy_slots(ctx: ToolContext, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    slots: list[tuple[datetime, datetime]] = []
    for event in ctx.db.list_events(start=start, end=end, limit=200):
        begins, _ = _resolve_when(event["starts_at"])
        finishes, _ = _resolve_when(event["ends_at"]) if event.get("ends_at") else (None, None)
        if begins:
            slots.append((begins, finishes or begins + timedelta(minutes=45)))
    return sorted(slots)


def suggest_slot(ctx: ToolContext, duration_minutes: int, *, prefer_morning: bool = True) -> datetime | None:
    """Find the next free gap for a meeting — used by the planner and by voice."""
    now = datetime.now()
    start, end = week_bounds(now)
    if start < now:
        start = now + timedelta(minutes=15)
    busy = busy_slots(ctx, start, end)
    cursor = start.replace(minute=0, second=0, microsecond=0) + timedelta(hours=9 if prefer_morning else 14)
    duration = timedelta(minutes=duration_minutes)
    while cursor + duration <= end:
        if cursor.hour < 9 or cursor.hour > 19 or cursor.weekday() >= 5:
            cursor = (cursor + timedelta(days=1)).replace(hour=9 if prefer_morning else 14, minute=0)
            continue
        clash = any(not (cursor + duration <= begin or cursor >= finish) for begin, finish in busy)
        if not clash:
            return cursor
        cursor += timedelta(minutes=30)
    return None


@tool(permission=Permission.SAFE, category=ToolCategory.PRODUCTIVITY)
def find_free_slot(ctx: ToolContext, duration_minutes: int = 60,
                   prefer: Literal["morning", "afternoon"] = "morning") -> ToolResult:
    """Suggest the next free slot in the calendar for a given duration.

    Args:
        duration_minutes: How long the meeting needs.
        prefer: "morning" or "afternoon".
    """
    slot = suggest_slot(ctx, max(5, min(int(duration_minutes), 480)), prefer_morning=prefer == "morning")
    if slot is None:
        return ToolResult(ok=False, message="No free slot found this week.",
                          speech="I couldn't find a free slot this week.")
    end = slot + timedelta(minutes=duration_minutes)
    return ToolResult(ok=True, message=f"Free {humanize(slot, now=datetime.now())} – {end.strftime('%H:%M')}",
                      speech=f"You're free {humanize(slot, now=datetime.now())} for {duration_minutes} minutes.",
                      data={"starts_at": slot.isoformat(timespec="minutes"),
                            "ends_at": end.isoformat(timespec="minutes"),
                            "duration_minutes": duration_minutes})
