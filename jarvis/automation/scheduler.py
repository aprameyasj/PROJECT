"""Scheduler — the thing that makes reminders actually fire.

Runs on APScheduler when it is installed; otherwise a lightweight asyncio
ticker checks the database every few seconds. Either way the public API is the
same:

    scheduler = ReminderScheduler(db, bus, notifier)
    await scheduler.start()
    scheduler.schedule_reminder(row)      # add / refresh a job
    scheduler.remove_reminder(id)
    await scheduler.stop()

When a reminder fires we (1) mark it in SQLite, (2) publish a
`reminder_fired` event for the dashboard, (3) post a desktop notification and
(4) speak it — then reschedule recurring reminders.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable

from ..core.events import EventBus
from ..core.timeparse import next_occurrence, parse_iso
from ..core.types import AgentEvent, EventType

Notifier = Callable[[str, str], Awaitable[None] | None]


class ReminderScheduler:
    """Time-based trigger engine for reminders (and event pre-alerts)."""

    def __init__(self, db: Any, bus: EventBus | None = None, notifier: Notifier | None = None,
                 *, poll_seconds: float = 5.0) -> None:
        self.db = db
        self.bus = bus or EventBus()
        self.notifier = notifier
        self.poll_seconds = poll_seconds
        self._scheduler: Any = None
        self._task: asyncio.Task[None] | None = None
        self._running = False
        self._loop: asyncio.AbstractEventLoop | None = None
        #: strong references, otherwise asyncio may GC a task before it runs
        self._tasks: set[Any] = set()
        self.fired: list[dict[str, Any]] = []
        self.engine = "none"
        self.last_error = ""

    # ------------------------------------------------------------------
    async def start(self) -> str:
        if self._running:
            return self.engine
        self._running = True
        self._loop = asyncio.get_running_loop()
        try:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler
            from apscheduler.triggers.date import DateTrigger

            self._scheduler = AsyncIOScheduler()
            self._scheduler.start()
            self.engine = "apscheduler"
            self._trigger_factory = DateTrigger  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            self._scheduler = None
            self.engine = "asyncio-poller"

        if self._scheduler is None:
            self._task = asyncio.create_task(self._poll_loop())
        else:
            # APScheduler still needs a light sweeper for reminders created
            # while it was down (e.g. during a reboot).
            self._task = asyncio.create_task(self._sweep_loop())

        await self.load_pending()
        return self.engine

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None
        if self._scheduler is not None:
            try:
                self._scheduler.shutdown(wait=False)
            except Exception:  # noqa: BLE001
                pass
            self._scheduler = None
        # let in-flight reminders finish so no coroutine is dropped mid-flight
        pending = [task for task in list(self._tasks) if not getattr(task, "done", lambda: True)()]
        if pending:
            try:
                await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), timeout=5)
            except (asyncio.TimeoutError, Exception):  # noqa: BLE001
                pass
        self._tasks.clear()

    # ------------------------------------------------------------------
    async def load_pending(self) -> int:
        """Re-arm every pending reminder in the database."""
        if self.db is None:
            return 0
        reminders = self.db.list_reminders(status="pending", limit=500)
        now = datetime.now()
        count = 0
        for reminder in reminders:
            when = parse_iso(reminder["remind_at"])
            if when is None:
                continue
            if when <= now:
                # missed while JARVIS was off — fire it once, right now
                self._spawn(self._fire(reminder, missed=True))
                count += 1
                continue
            self._arm(reminder, when)
            count += 1
        return count

    def _job_id(self, reminder_id: int) -> str:
        return f"reminder-{reminder_id}"

    def _arm(self, reminder: dict[str, Any], when: datetime) -> None:
        if when <= datetime.now():
            return
        if self._scheduler is not None:
            try:
                self._scheduler.add_job(
                    self._fire_sync,
                    trigger=self._trigger_factory(run_date=when),  # type: ignore[attr-defined]
                    id=self._job_id(int(reminder["id"])),
                    replace_existing=True,
                    kwargs={"reminder": reminder},
                    misfire_grace_time=3600,
                )
                return
            except Exception:  # noqa: BLE001
                pass
        # poller engine: nothing to do, the sweep loop picks it up

    def schedule_reminder(self, reminder: dict[str, Any]) -> bool:
        """Public API used by the reminder tools."""
        if not self._running or self._scheduler is None:
            return False
        when = parse_iso(reminder.get("remind_at", ""))
        if when is None:
            return False
        self._arm(reminder, when)
        return True

    def remove_reminder(self, reminder_id: int) -> bool:
        if self._scheduler is not None:
            try:
                self._scheduler.remove_job(self._job_id(int(reminder_id)))
                return True
            except Exception:  # noqa: BLE001
                return False
        return False

    def _spawn(self, coro: Any) -> bool:
        """Schedule `coro` on the loop that owns this scheduler.

        APScheduler may invoke job callbacks from a worker thread, where
        ``asyncio.create_task`` would raise; ``run_coroutine_threadsafe`` hands
        the work back to the event loop instead. The future is kept in
        ``self._tasks`` so the coroutine is never garbage-collected unawaited.
        """
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None

        if running is not None:
            task: Any = running.create_task(coro)
        elif self._loop is not None and not self._loop.is_closed():
            task = asyncio.run_coroutine_threadsafe(coro, self._loop)
        else:
            coro.close()
            self.last_error = "no event loop available to fire a reminder"
            return False

        self._tasks.add(task)
        done = getattr(task, "add_done_callback", None)
        if done is not None:
            done(self._tasks.discard)
        return True

    def _fire_sync(self, reminder: dict[str, Any]) -> None:
        self._spawn(self._fire(reminder))

    # ------------------------------------------------------------------
    async def _poll_loop(self) -> None:
        while self._running:
            await self._sweep()
            await asyncio.sleep(max(1.0, self.poll_seconds))

    async def _sweep_loop(self) -> None:
        """Backup sweeper: catches anything the job store missed."""
        while self._running:
            await asyncio.sleep(max(5.0, self.poll_seconds * 4))
            try:
                await self._sweep()
            except Exception:  # noqa: BLE001
                pass

    async def _sweep(self) -> None:
        if self.db is None:
            return
        now = datetime.now()
        for reminder in self.db.list_reminders(status="pending", limit=200):
            when = parse_iso(reminder["remind_at"])
            if when is not None and when <= now:
                await self._fire(reminder)

    # ------------------------------------------------------------------
    async def _fire(self, reminder: dict[str, Any], *, missed: bool = False) -> None:
        reminder_id = int(reminder["id"])
        task = reminder["task"]
        channel = reminder.get("channel", "all")
        when = parse_iso(reminder.get("remind_at", ""))

        self.db.update_reminder(reminder_id, status="fired",
                                fired_at=datetime.now().isoformat(timespec="seconds"))
        self.fired.append({"id": reminder_id, "task": task, "at": datetime.now().isoformat(timespec="seconds"),
                           "missed": missed})

        body = f"Reminder: {task}" + (" (this was scheduled while I was offline)" if missed else "")
        payload = {
            "id": reminder_id,
            "task": task,
            "remind_at": reminder.get("remind_at"),
            "missed": missed,
            "channel": channel,
            "message": body,
        }
        await self.bus.publish(AgentEvent(type=EventType.REMINDER_FIRED, session_id="", payload=payload))

        if channel in {"all", "notification"}:
            if self.notifier is not None:
                try:
                    result = self.notifier("JARVIS reminder", body)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception:  # noqa: BLE001
                    pass

        if channel in {"all", "voice"}:
            await self.bus.publish(AgentEvent(type=EventType.SPEECH, session_id="",
                                              payload={"text": body, "voice": True, "reminder_id": reminder_id}))

        # recurring reminders roll forward
        recurrence = reminder.get("recurrence") or ""
        if recurrence and when is not None:
            following = next_occurrence(recurrence, when)
            while following is not None and following <= datetime.now():
                following = next_occurrence(recurrence, following)
            if following is not None:
                new_row = self.db.add_reminder(task, following, recurrence=recurrence, channel=channel,
                                               notes=reminder.get("notes", ""),
                                               timezone_name=reminder.get("timezone", ""))
                self._arm(new_row, following)

    # ------------------------------------------------------------------
    def describe(self) -> dict[str, Any]:
        return {
            "engine": self.engine,
            "running": self._running,
            "fired_total": len(self.fired),
            "pending": len(self.db.list_reminders(status="pending", limit=500)) if self.db else 0,
            "last_fired": self.fired[-1] if self.fired else None,
        }

    async def next_due(self) -> dict[str, Any] | None:
        pending = self.db.list_reminders(status="pending", limit=1) if self.db else []
        return pending[0] if pending else None


__all__ = ["ReminderScheduler"]
