"""WebSocket bridge between JARVIS and the React dashboard.

Client -> server messages:

    {"type": "chat",      "text": "close this tab", "session_id": "..."}
    {"type": "confirm",   "token": "...", "approved": true}
    {"type": "audio",     "data": "<base64 audio>"}        # server-side STT
    {"type": "tool",      "name": "take_screenshot", "arguments": {}}
    {"type": "workflow",  "name": "coding_setup"}
    {"type": "ping"}

Server -> client: every :class:`~jarvis.core.types.AgentEvent` (transcript,
thinking, tool_call, tool_result, confirmation_required, agent_message,
speech, reminder_fired, ...), plus a `turn` message when an utterance has been
fully processed and a `hello` snapshot on connect.
"""

from __future__ import annotations

import asyncio
import base64
import json
import tempfile
from pathlib import Path
from typing import Any

from ..core.types import AgentEvent, EventType, ToolCall
from .runtime import Runtime


class Connection:
    """One WebSocket client."""

    def __init__(self, socket: Any, runtime: Runtime, session_id: str = "") -> None:
        self.socket = socket
        self.runtime = runtime
        self.session_id = session_id
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1000)
        self._unsubscribe: Any = None
        self.closed = False

    async def attach(self) -> None:
        self._unsubscribe = self.runtime.subscribe(self.on_event)

    async def on_event(self, event: AgentEvent) -> None:
        if self.session_id and event.session_id and event.session_id != self.session_id:
            return
        await self.enqueue({"type": event.type.value, "payload": event.payload,
                            "session_id": event.session_id, "ts": event.ts, "id": event.id})

    async def enqueue(self, message: dict[str, Any]) -> None:
        if self.closed:
            return
        try:
            self.queue.put_nowait(message)
        except asyncio.QueueFull:
            # drop the oldest event rather than blocking the agent
            try:
                self.queue.get_nowait()
                self.queue.put_nowait(message)
            except asyncio.QueueEmpty:
                pass

    async def send(self, message: dict[str, Any]) -> None:
        await self.socket.send_text(json.dumps(message, default=str))

    async def pump(self) -> None:
        while not self.closed:
            message = await self.queue.get()
            if message is None:
                break
            try:
                await self.send(message)
            except Exception:  # noqa: BLE001
                self.closed = True
                break

    async def detach(self) -> None:
        self.closed = True
        if self._unsubscribe is not None:
            self._unsubscribe()
        await self.enqueue(None)  # type: ignore[arg-type]


async def handle_socket(socket: Any, runtime: Runtime, session_id: str = "") -> None:
    """Main WebSocket loop."""
    connection = Connection(socket, runtime, session_id)
    await connection.attach()

    hello = {
        "type": "hello",
        "payload": {
            "status": runtime.status(),
            "session_id": session_id,
            "recent_events": runtime.bus.recent(25),
            "pending": runtime.agent.pending_confirmations(session_id or None),
        },
    }
    await connection.send(hello)

    pump = asyncio.create_task(connection.pump())
    try:
        while True:
            raw = await socket.receive_text()
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                await connection.enqueue({"type": "error", "payload": {"message": "invalid JSON"}})
                continue
            await dispatch(connection, message)
    except Exception:  # noqa: BLE001 — includes WebSocketDisconnect
        pass
    finally:
        await connection.detach()
        pump.cancel()
        try:
            await pump
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass


async def dispatch(connection: Connection, message: dict[str, Any]) -> None:
    runtime = connection.runtime
    kind = str(message.get("type", "chat")).lower()
    session_id = str(message.get("session_id") or connection.session_id or "")
    connection.session_id = session_id

    if kind == "ping":
        await connection.enqueue({"type": "pong", "payload": {"ts": __import__("time").time()}})
        return

    if kind in {"chat", "transcript", "message", "text"}:
        text = str(message.get("text", "")).strip()
        if not text:
            await connection.enqueue({"type": "error", "payload": {"message": "empty text"}})
            return
        speak = bool(message.get("speak", True))
        turn = await runtime.agent.process(text, session_id=session_id, speak=speak)
        await connection.enqueue({"type": "turn", "payload": turn.to_dict()})
        return

    if kind == "confirm":
        token = str(message.get("token", ""))
        approved = bool(message.get("approved", False))
        turn = await runtime.agent.confirm(token, approved, session_id=session_id,
                                           speak=bool(message.get("speak", True)))
        await connection.enqueue({"type": "turn", "payload": turn.to_dict()})
        return

    if kind == "audio":
        data = message.get("data") or ""
        if isinstance(data, str):
            try:
                audio = base64.b64decode(data.split(",", 1)[-1])
            except Exception:  # noqa: BLE001
                await connection.enqueue({"type": "error", "payload": {"message": "bad base64 audio"}})
                return
        else:
            audio = bytes(data)
        with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as handle:
            handle.write(audio)
            path = handle.name
        transcript = await runtime.stt.transcribe_file(path)
        Path(path).unlink(missing_ok=True)
        await connection.enqueue({"type": "transcript", "payload": transcript.to_dict()})
        if transcript.text:
            turn = await runtime.agent.process(transcript.text, session_id=session_id, speak=True)
            await connection.enqueue({"type": "turn", "payload": turn.to_dict()})
        return

    if kind == "tool":
        name = str(message.get("name", ""))
        arguments = message.get("arguments") or {}
        spec = runtime.agent.registry.get(name)
        if spec is None:
            await connection.enqueue({"type": "error", "payload": {"message": f"unknown tool `{name}`"}})
            return
        outcome = await runtime.agent.executor.execute(
            ToolCall(name=name, arguments=arguments, source="user"), session_id=session_id
        )
        await connection.enqueue({"type": "manual_tool", "payload": outcome.to_dict()})
        return

    if kind == "workflow":
        name = str(message.get("name", ""))
        turn = await runtime.agent.process(f"run workflow {name}", session_id=session_id, speak=True)
        await connection.enqueue({"type": "turn", "payload": turn.to_dict()})
        return

    if kind == "listener":
        action = str(message.get("action", "status"))
        if runtime.listener is None:
            await connection.enqueue({"type": "listener", "payload": {"available": False,
                                                                     "reason": "no microphone / sounddevice"}})
            return
        if action == "start":
            ok = await runtime.listener.start()
        elif action == "stop":
            await runtime.listener.stop()
            ok = False
        else:
            ok = runtime.listener._running  # noqa: SLF001
        await connection.enqueue({"type": "listener", "payload": {**runtime.listener.status(), "ok": ok}})
        return

    if kind == "status":
        await connection.enqueue({"type": "status", "payload": runtime.status()})
        return

    await connection.enqueue({"type": "error", "payload": {"message": f"unknown message type `{kind}`"}})


__all__ = ["handle_socket", "Connection", "dispatch"]
