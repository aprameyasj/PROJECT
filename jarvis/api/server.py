"""FastAPI server — the control surface for JARVIS.

The React dashboard is *not* the agent: it is a client of this API. The Python
process owns the brain, the tools, the safety gate and the scheduler, so the
same JARVIS can be driven from the dashboard, the CLI, or a microphone loop at
the same time.

    python main.py serve        # REST + WebSocket on 0.0.0.0:8000
    frontend (vite)             # proxies /api and /ws to this server
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from ..automation.workflows import all_workflows, get_workflow
from ..core.timeparse import parse_when
from ..core.types import AgentEvent, EventType, ToolCall
from .runtime import Runtime, build_runtime
from .websocket import handle_socket

# ---------------------------------------------------------------------------
# request / response models
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    text: str
    session_id: str = ""
    speak: bool = True


class ConfirmRequest(BaseModel):
    token: str
    approved: bool = True
    session_id: str = ""
    speak: bool = True


class ToolRunRequest(BaseModel):
    arguments: dict[str, Any] = Field(default_factory=dict)
    session_id: str = ""


class ReminderRequest(BaseModel):
    task: str
    when: str = ""
    recurrence: str = ""
    channel: str = "all"
    notes: str = ""


class EventRequest(BaseModel):
    title: str
    starts_at: str
    ends_at: str = ""
    location: str = ""
    notes: str = ""
    remind_minutes_before: int = 15


class TaskRequest(BaseModel):
    title: str
    project: str = ""
    priority: int = 3
    due: str = ""
    notes: str = ""


class MemoryRequest(BaseModel):
    key: str
    value: str
    category: str = "general"


class PlanRequest(BaseModel):
    goal: str
    execute: bool = False
    session_id: str = ""


# ---------------------------------------------------------------------------
# app factory
# ---------------------------------------------------------------------------


def create_app(runtime: Runtime | None = None, *, settings: Any = None) -> FastAPI:
    runtime = runtime or build_runtime()
    agent = runtime.agent
    app_settings = settings or agent.settings

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> Any:
        await runtime.start()
        runtime.bus.emit(EventType.STATUS, {"api": "ready", "port": app_settings.api_port})
        try:
            yield
        finally:
            await runtime.stop()

    app = FastAPI(
        title="JARVIS — Personal AI Agent",
        version="0.1.0",
        description="Local-first voice agent: intent -> tool -> executor -> result.",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.cors_origins or ["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.runtime = runtime

    # ------------------------------------------------------------------
    # auth (only enforced when JARVIS_API_TOKEN is configured)
    # ------------------------------------------------------------------
    def require_token(request: Request) -> None:
        expected = app_settings.api_token
        if not expected:
            return
        provided = request.headers.get("x-jarvis-token") or request.query_params.get("token")
        if provided != expected:
            raise HTTPException(status_code=401, detail="invalid or missing JARVIS token")

    auth = Depends(require_token)

    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # health & status
    # ------------------------------------------------------------------
    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "time": datetime.now().isoformat(timespec="seconds"),
                "backend": runtime.agent.backend.name,
                "brain": getattr(runtime.agent.brain, "name", "?")}

    @app.get("/api/status", dependencies=[auth])
    async def status() -> dict[str, Any]:
        return runtime.status()

    # ------------------------------------------------------------------
    # the main entry point
    # ------------------------------------------------------------------
    @app.post("/api/chat", dependencies=[auth])
    async def chat(request: ChatRequest) -> dict[str, Any]:
        if not request.text.strip():
            raise HTTPException(status_code=422, detail="text is required")
        turn = await agent.process(request.text, session_id=request.session_id, speak=request.speak)
        return turn.to_dict()

    @app.post("/api/confirm", dependencies=[auth])
    async def confirm(request: ConfirmRequest) -> dict[str, Any]:
        turn = await agent.confirm(request.token, request.approved, session_id=request.session_id,
                                   speak=request.speak)
        return turn.to_dict()

    @app.get("/api/pending", dependencies=[auth])
    async def pending(session_id: str = "") -> dict[str, Any]:
        return {"pending": agent.pending_confirmations(session_id or None)}

    @app.post("/api/session/reset", dependencies=[auth])
    async def reset_session(session_id: str = "") -> dict[str, Any]:
        agent.reset_session(session_id or agent._last_session)  # noqa: SLF001
        return {"ok": True, "session_id": session_id or agent._last_session}  # noqa: SLF001

    # ------------------------------------------------------------------
    # tools
    # ------------------------------------------------------------------
    @app.get("/api/tools", dependencies=[auth])
    async def tools() -> dict[str, Any]:
        registry = agent.registry
        return {
            "count": len(registry.all()),
            "categories": {name: [spec.to_dict() for spec in specs]
                           for name, specs in registry.by_category().items()},
            "stats": registry.stats(),
            "catalogue": registry.prompt_catalogue(),
        }

    @app.get("/api/tools/{name}", dependencies=[auth])
    async def tool_detail(name: str) -> dict[str, Any]:
        spec = agent.registry.get(name)
        if spec is None:
            raise HTTPException(status_code=404, detail=f"no tool called `{name}`")
        return spec.to_dict()

    @app.post("/api/tools/{name}/run", dependencies=[auth])
    async def run_tool(name: str, request: ToolRunRequest) -> dict[str, Any]:
        spec = agent.registry.get(name)
        if spec is None:
            raise HTTPException(status_code=404, detail=f"no tool called `{name}`")
        outcome = await agent.executor.execute(
            ToolCall(name=name, arguments=request.arguments, source="user"),
            session_id=request.session_id,
        )
        return outcome.to_dict()

    @app.get("/api/workflows", dependencies=[auth])
    async def workflows() -> dict[str, Any]:
        return {"workflows": [w.to_dict() for w in all_workflows()]}

    @app.post("/api/workflows/{name}/run", dependencies=[auth])
    async def run_workflow(name: str, variables: dict[str, Any] | None = None,
                           session_id: str = "") -> dict[str, Any]:
        workflow = get_workflow(name)
        if workflow is None:
            raise HTTPException(status_code=404, detail=f"no workflow called `{name}`")
        call = ToolCall(name="run_workflow", arguments={"name": name, "variables": variables or {}},
                        source="user")
        result = await agent.executor.execute(call, session_id=session_id)
        return result.result.to_dict() if result.result else result.to_dict()

    @app.post("/api/plan", dependencies=[auth])
    async def make_plan(request: PlanRequest) -> dict[str, Any]:
        plan, draft = await agent.planner.create_plan(request.goal, session_id=request.session_id,
                                                      context=agent.context.planner_context(request.session_id))
        payload: dict[str, Any] = {"plan": plan.to_dict(), "draft_notes": draft.notes, "error": draft.error}
        if request.execute:
            run = await agent.executor.run_plan(plan, session_id=request.session_id)
            payload["run"] = {"summary": run.summary(), "succeeded": run.succeeded, "failed": run.failed,
                              "stopped_reason": run.stopped_reason,
                              "pending": run.pending.to_dict() if run.pending else None}
        return payload

    # ------------------------------------------------------------------
    # reminders / calendar / tasks
    # ------------------------------------------------------------------
    @app.get("/api/reminders", dependencies=[auth])
    async def list_reminders(status_filter: str = Query("pending", alias="status")) -> dict[str, Any]:
        rows = agent.db.list_reminders(status=None if status_filter == "all" else status_filter, limit=200)
        return {"reminders": rows, "count": len(rows)}

    @app.post("/api/reminders", dependencies=[auth])
    async def add_reminder(request: ReminderRequest) -> dict[str, Any]:
        parsed = parse_when(request.when) if request.when else None
        when = parsed.when if parsed and parsed.when else datetime.now() + timedelta(hours=1)
        if request.when:
            try:
                when = datetime.fromisoformat(request.when)
            except ValueError:
                pass
        reminder = agent.db.add_reminder(request.task, when, recurrence=request.recurrence,
                                         channel=request.channel, notes=request.notes,
                                         timezone_name=app_settings.timezone)
        runtime.scheduler.schedule_reminder(reminder)
        return reminder

    @app.delete("/api/reminders/{reminder_id}", dependencies=[auth])
    async def delete_reminder(reminder_id: int) -> dict[str, Any]:
        if not agent.db.delete_reminder(reminder_id):
            raise HTTPException(status_code=404, detail="reminder not found")
        runtime.scheduler.remove_reminder(reminder_id)
        return {"ok": True, "id": reminder_id}

    @app.post("/api/reminders/{reminder_id}/complete", dependencies=[auth])
    async def complete_reminder(reminder_id: int) -> dict[str, Any]:
        reminder = agent.db.update_reminder(reminder_id, status="fired",
                                            fired_at=datetime.now().isoformat(timespec="seconds"))
        if reminder is None:
            raise HTTPException(status_code=404, detail="reminder not found")
        runtime.scheduler.remove_reminder(reminder_id)
        return reminder

    @app.post("/api/reminders/{reminder_id}/snooze", dependencies=[auth])
    async def snooze_reminder(reminder_id: int, minutes: int = 10) -> dict[str, Any]:
        reminder = agent.db.get_reminder(reminder_id)
        if reminder is None:
            raise HTTPException(status_code=404, detail="reminder not found")
        when = datetime.fromisoformat(reminder["remind_at"]) + timedelta(minutes=max(1, minutes))
        updated = agent.db.update_reminder(reminder_id, remind_at=when.isoformat(timespec="seconds"),
                                           status="pending")
        runtime.scheduler.schedule_reminder(updated or reminder)
        return updated or reminder

    @app.get("/api/calendar", dependencies=[auth])
    async def list_calendar(window: str = "week", limit: int = 50) -> dict[str, Any]:
        now = datetime.now()
        start = {"today": now.replace(hour=0, minute=0, second=0, microsecond=0),
                 "tomorrow": (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0),
                 "week": now - timedelta(days=now.weekday()),
                 "month": now.replace(day=1),
                 "all": now - timedelta(days=3650)}.get(window, now - timedelta(days=1))
        end = {"today": start + timedelta(days=1), "tomorrow": start + timedelta(days=1),
               "week": start + timedelta(days=7), "month": start + timedelta(days=31),
               "all": now + timedelta(days=3650)}.get(window, start + timedelta(days=1))
        events = agent.db.list_events(start=start, end=end, limit=limit)
        return {"events": events, "window": window, "count": len(events)}

    @app.post("/api/calendar", dependencies=[auth])
    async def add_calendar_event(request: EventRequest) -> dict[str, Any]:
        try:
            starts = datetime.fromisoformat(request.starts_at)
        except ValueError:
            parsed = parse_when(request.starts_at)
            if not parsed.when:
                raise HTTPException(status_code=422, detail=f"could not parse `{request.starts_at}`")
            starts = parsed.when
        ends = None
        if request.ends_at:
            try:
                ends = datetime.fromisoformat(request.ends_at)
            except ValueError:
                ends = None
        event = agent.db.add_event(request.title, starts, ends_at=ends, location=request.location,
                                   notes=request.notes)
        if request.remind_minutes_before > 0:
            when = starts - timedelta(minutes=request.remind_minutes_before)
            if when > datetime.now():
                reminder = agent.db.add_reminder(f"{request.title} starts at {starts:%H:%M}", when)
                runtime.scheduler.schedule_reminder(reminder)
                event["reminder_id"] = reminder["id"]
        return event

    @app.delete("/api/calendar/{event_id}", dependencies=[auth])
    async def cancel_calendar_event(event_id: int) -> dict[str, Any]:
        if not agent.db.cancel_event(event_id):
            raise HTTPException(status_code=404, detail="event not found")
        return {"ok": True, "id": event_id}

    @app.get("/api/tasks", dependencies=[auth])
    async def list_tasks(status_filter: str = Query("todo", alias="status")) -> dict[str, Any]:
        tasks = agent.db.list_tasks(status=None if status_filter == "all" else status_filter, limit=200)
        return {"tasks": tasks, "count": len(tasks)}

    @app.post("/api/tasks", dependencies=[auth])
    async def add_task(request: TaskRequest) -> dict[str, Any]:
        due = parse_when(request.due).when if request.due else None
        return agent.db.add_task(request.title, project=request.project, priority=request.priority,
                                 due_at=due, notes=request.notes)

    @app.post("/api/tasks/{task_id}/status", dependencies=[auth])
    async def set_task_status(task_id: int, status_value: str = Query("done", alias="status")) -> dict[str, Any]:
        task = agent.db.set_task_status(task_id, status_value)
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        return task

    @app.get("/api/agenda", dependencies=[auth])
    async def agenda(window: str = "today") -> dict[str, Any]:
        result = await agent.executor.registry.execute(
            ToolCall(name="get_agenda", arguments={"window": window}, source="api"),
            agent._make_context(session_id="api"),  # noqa: SLF001
        )
        return result.to_dict()

    # ------------------------------------------------------------------
    # memory & audit
    # ------------------------------------------------------------------
    @app.get("/api/memory", dependencies=[auth])
    async def get_memory() -> dict[str, Any]:
        return {"profile": agent.memory.profile(), "categories": agent.memory.facts_by_category(),
                "stats": agent.memory.stats()}

    @app.post("/api/memory", dependencies=[auth])
    async def put_memory(request: MemoryRequest) -> dict[str, Any]:
        return await agent.memory.remember(request.key, request.value, category=request.category)

    @app.delete("/api/memory", dependencies=[auth])
    async def drop_memory(key: str, category: str = "") -> dict[str, Any]:
        removed = agent.db.delete_fact(key, category or None)
        return {"ok": removed, "key": key}

    @app.get("/api/audit", dependencies=[auth])
    async def audit(limit: int = 60) -> dict[str, Any]:
        return {"entries": agent.db.audit_log(limit=limit), "stats": agent.db.stats()}

    @app.get("/api/events", dependencies=[auth])
    async def recent_events(limit: int = 60) -> dict[str, Any]:
        return {"events": runtime.bus.recent(limit)}

    @app.get("/api/events/stream", dependencies=[auth])
    async def event_stream(request: Request) -> StreamingResponse:
        """Server-sent events — a fallback for clients without WebSocket."""
        queue: Any = None
        import asyncio

        queue = asyncio.Queue(maxsize=256)

        async def sender(event: AgentEvent) -> None:
            try:
                queue.put_nowait(event.to_dict())
            except asyncio.QueueFull:
                pass

        unsubscribe = runtime.subscribe(sender)

        async def stream() -> Any:
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        payload = await asyncio.wait_for(queue.get(), timeout=15)
                        yield f"data: {json.dumps(payload, default=str)}\n\n"
                    except asyncio.TimeoutError:
                        yield ": keep-alive\n\n"
            finally:
                unsubscribe()

        return StreamingResponse(stream(), media_type="text/event-stream")

    # ------------------------------------------------------------------
    # artifacts: screenshots & audio
    # ------------------------------------------------------------------
    @app.get("/api/screenshots", dependencies=[auth])
    async def screenshots(limit: int = 24) -> dict[str, Any]:
        artifacts = agent.db.list_artifacts("screenshot", limit=limit)
        for artifact in artifacts:
            name = Path(artifact["path"]).name
            artifact["url"] = f"/api/screenshots/{name}" if Path(artifact["path"]).exists() else None
        return {"screenshots": artifacts}

    @app.get("/api/screenshots/{name}")
    async def screenshot_file(name: str) -> FileResponse:
        return _safe_file(Path(app_settings.screenshots_dir) / name, "image/png")

    @app.get("/api/audio/{name}")
    async def audio_file(name: str) -> FileResponse:
        return _safe_file(Path(app_settings.audio_dir) / name, "audio/mpeg")

    # ------------------------------------------------------------------
    # voice
    # ------------------------------------------------------------------
    @app.post("/api/voice", dependencies=[auth])
    async def voice(request: Request, session_id: str = "", speak: bool = True) -> dict[str, Any]:
        """Speech -> text -> agent. Accepts either a raw audio body or JSON
        `{"audio": "<base64>"}` / `{"text": "..."}` (browser STT)."""
        body = await request.body()
        content_type = request.headers.get("content-type", "")
        audio = b""
        text = ""
        filename = "audio.webm"

        if "application/json" in content_type:
            try:
                data = json.loads(body or b"{}")
            except json.JSONDecodeError:
                raise HTTPException(status_code=422, detail="invalid JSON body")
            text = str(data.get("text", "")).strip()
            if data.get("audio"):
                import base64

                audio = base64.b64decode(str(data["audio"]).split(",", 1)[-1])
            filename = str(data.get("filename") or filename)
        elif body:
            audio = body

        payload: dict[str, Any] = {}
        if audio:
            transcript = await runtime.stt.transcribe_bytes(audio, filename=filename)
            payload["transcript"] = transcript.to_dict()
            text = text or transcript.text

        if not text:
            raise HTTPException(status_code=422, detail="no audio and no text supplied")
        turn = await agent.process(text, session_id=session_id, speak=speak)
        payload["turn"] = turn.to_dict()
        payload["text"] = text
        return payload

    @app.get("/api/tts", dependencies=[auth])
    async def tts(text: str = Query(..., min_length=1, max_length=2000)) -> Any:
        if runtime.tts.provider not in {"openai", "system"}:
            return JSONResponse({"ok": False, "detail": "server-side TTS unavailable; the browser speaks instead",
                                 "text": text})
        path = await runtime.tts.synthesise(text)
        if not path:
            raise HTTPException(status_code=502, detail=runtime.tts.last_error or "synthesis failed")
        return FileResponse(path, media_type="audio/mpeg")

    @app.post("/api/listener/{action}", dependencies=[auth])
    async def listener(action: str) -> dict[str, Any]:
        if runtime.listener is None:
            return {"ok": False, "detail": "no microphone available (install sounddevice + numpy)"}
        if action == "start":
            return {"ok": await runtime.listener.start(), "status": runtime.listener.status()}
        if action == "stop":
            await runtime.listener.stop()
            return {"ok": True, "status": runtime.listener.status()}
        return {"ok": True, "status": runtime.listener.status()}

    # ------------------------------------------------------------------
    # websocket
    # ------------------------------------------------------------------
    @app.websocket("/ws")
    async def websocket_endpoint(socket: WebSocket) -> None:
        token = socket.query_params.get("token", "")
        if app_settings.api_token and token != app_settings.api_token:
            await socket.close(code=4401)
            return
        await socket.accept()
        session_id = socket.query_params.get("session_id", "")
        try:
            await handle_socket(socket, runtime, session_id)
        except WebSocketDisconnect:
            pass
        except Exception:  # noqa: BLE001
            try:
                await socket.close()
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------
    # static dashboard (served when the React build exists)
    # ------------------------------------------------------------------
    dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if dist.exists():
        from fastapi.staticfiles import StaticFiles

        app.mount("/", StaticFiles(directory=str(dist), html=True), name="dashboard")

    @app.get("/api", include_in_schema=False)
    async def index() -> dict[str, Any]:
        return {
            "name": "JARVIS",
            "version": "0.1.0",
            "docs": "/api/docs",
            "websocket": "/ws",
            "backend": agent.backend.name,
            "brain": getattr(agent.brain, "name", "?"),
            "tools": len(agent.registry.all()),
            "dashboard_built": dist.exists(),
        }

    return app


def _safe_file(path: Path, media_type: str) -> FileResponse:
    """Serve a file but never escape its directory."""
    resolved = path.resolve()
    if not str(resolved).startswith(str(path.parent.resolve())) or not resolved.is_file():
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(str(resolved), media_type=media_type)


__all__ = ["create_app", "Runtime", "build_runtime"]
