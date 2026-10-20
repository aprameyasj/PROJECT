"""The REST/WebSocket API that the React dashboard talks to."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(agent, settings):
    from jarvis.api.runtime import Runtime
    from jarvis.api.server import create_app
    from jarvis.automation.scheduler import ReminderScheduler
    from jarvis.voice.stt import SpeechToText
    from jarvis.voice.tts import TextToSpeech

    runtime = Runtime(
        agent=agent,
        bus=agent.bus,
        scheduler=ReminderScheduler(agent.db, agent.bus, poll_seconds=60),
        stt=SpeechToText(settings),
        tts=TextToSpeech(settings),
    )
    with TestClient(create_app(runtime=runtime, settings=settings)) as test_client:
        yield test_client


def test_health_and_status(client: TestClient) -> None:
    health = client.get("/api/health").json()
    assert health["ok"] is True

    status = client.get("/api/status").json()
    assert status["agent"]["backend"]["name"] == "sandbox"
    assert status["agent"]["tools"] > 40
    assert status["stt"]["provider"]
    assert status["tts"]["provider"]


def test_chat_executes_a_tool(client: TestClient) -> None:
    response = client.post("/api/chat", json={"text": "open chrome", "speak": False})
    assert response.status_code == 200
    turn = response.json()
    assert turn["ok"] is True
    assert turn["tool_calls"][0]["name"] == "open_application"
    assert turn["steps"][0]["status"] == "ok"


def test_chat_validation(client: TestClient) -> None:
    assert client.post("/api/chat", json={"text": "  "}).status_code == 422


def test_confirmation_round_trip(client: TestClient) -> None:
    turn = client.post("/api/chat", json={"text": "shut down the computer", "speak": False}).json()
    assert turn["pending"] is not None
    token = turn["pending"]["token"]

    listed = client.get("/api/pending").json()["pending"]
    assert any(item["token"] == token for item in listed)

    denied = client.post("/api/confirm", json={"token": token, "approved": False}).json()
    assert "cancel" in denied["response"].lower()

    # the token cannot be reused
    assert "expired" in client.post("/api/confirm", json={"token": token, "approved": True}).json()["response"].lower()


def test_tools_catalogue(client: TestClient) -> None:
    payload = client.get("/api/tools").json()
    assert payload["count"] > 40
    assert "computer" in payload["categories"]
    assert "close_current_tab" in payload["catalogue"]

    detail = client.get("/api/tools/take_screenshot").json()
    assert detail["permission"] == "safe"
    assert client.get("/api/tools/nope").status_code == 404


def test_manual_tool_run(client: TestClient) -> None:
    outcome = client.post("/api/tools/take_screenshot/run", json={"arguments": {"note": "api"}}).json()
    assert outcome["status"] == "ok"
    assert outcome["result"]["ok"] is True


def test_manual_tool_run_respects_safety(client: TestClient) -> None:
    outcome = client.post("/api/tools/run_command/run", json={"arguments": {"command": "rm -rf /"}}).json()
    assert outcome["status"] == "blocked"


def test_reminders_crud(client: TestClient) -> None:
    when = (datetime.now() + timedelta(hours=1)).isoformat(timespec="minutes")
    created = client.post("/api/reminders", json={"task": "drink water", "when": when}).json()
    assert created["task"] == "drink water"

    listed = client.get("/api/reminders").json()["reminders"]
    assert any(item["id"] == created["id"] for item in listed)

    snoozed = client.post(f"/api/reminders/{created['id']}/snooze?minutes=15").json()
    assert snoozed["remind_at"] > when

    completed = client.post(f"/api/reminders/{created['id']}/complete").json()
    assert completed["status"] == "fired"

    second = client.post("/api/reminders", json={"task": "temp", "when": when}).json()
    assert client.delete(f"/api/reminders/{second['id']}").json()["ok"] is True
    assert client.delete("/api/reminders/99999").status_code == 404


def test_calendar_and_tasks(client: TestClient) -> None:
    starts = (datetime.now() + timedelta(hours=2)).isoformat(timespec="minutes")
    event = client.post("/api/calendar", json={"title": "Team sync", "starts_at": starts}).json()
    assert event["title"] == "Team sync"
    assert client.get("/api/calendar?window=week").json()["count"] >= 1
    assert client.delete(f"/api/calendar/{event['id']}").json()["ok"] is True

    task = client.post("/api/tasks", json={"title": "read chapter 4", "project": "dbms"}).json()
    assert task["status"] == "todo"
    done = client.post(f"/api/tasks/{task['id']}/status?status=done").json()
    assert done["status"] == "done"


def test_memory_endpoints(client: TestClient) -> None:
    client.post("/api/memory", json={"key": "preferred_ide", "value": "VS Code", "category": "preference"})
    payload = client.get("/api/memory").json()
    assert payload["categories"]["preference"]
    assert client.delete("/api/memory?key=preferred_ide&category=preference").json()["ok"] is True


def test_audit_and_events(client: TestClient) -> None:
    client.post("/api/chat", json={"text": "run command rm -rf /", "speak": False})
    audit = client.get("/api/audit").json()
    assert audit["stats"]["blocked"] >= 1
    assert any(entry["decision"] == "block" for entry in audit["entries"])
    assert "events" in client.get("/api/events").json()


def test_workflows(client: TestClient) -> None:
    flows = client.get("/api/workflows").json()["workflows"]
    assert any(flow["name"] == "coding_setup" for flow in flows)
    assert client.post("/api/workflows/nope/run").status_code == 404


def test_plan_endpoint(client: TestClient) -> None:
    payload = client.post("/api/plan", json={"goal": "prepare my workspace for coding", "execute": False}).json()
    assert payload["plan"]["steps"]


def test_voice_endpoint_accepts_text(client: TestClient) -> None:
    payload = client.post("/api/voice", json={"text": "what time is it"}).json()
    assert payload["turn"]["intent"] == "time"
    assert payload["text"] == "what time is it"


def test_voice_endpoint_accepts_raw_audio(client: TestClient) -> None:
    """With no STT provider installed the request degrades gracefully."""
    response = client.post("/api/voice", content=b"\x00\x01not-really-audio",
                           headers={"content-type": "audio/webm"})
    assert response.status_code in (200, 422, 502)


def test_tts_reports_availability(client: TestClient) -> None:
    response = client.get("/api/tts", params={"text": "hello"})
    assert response.status_code in (200, 502)


def test_screenshots_listing(client: TestClient) -> None:
    client.post("/api/tools/take_screenshot/run", json={"arguments": {}})
    shots = client.get("/api/screenshots").json()["screenshots"]
    assert shots and shots[0]["url"]
    fetched = client.get(shots[0]["url"])
    assert fetched.status_code == 200
    assert fetched.headers["content-type"] == "image/png"


def test_token_is_enforced_when_configured(agent, settings, monkeypatch) -> None:
    from jarvis.api.runtime import Runtime
    from jarvis.api.server import create_app
    from jarvis.automation.scheduler import ReminderScheduler
    from jarvis.voice.stt import SpeechToText
    from jarvis.voice.tts import TextToSpeech

    monkeypatch.setattr(settings, "api_token", "s3cret")
    runtime = Runtime(agent=agent, bus=agent.bus,
                      scheduler=ReminderScheduler(agent.db, agent.bus, poll_seconds=60),
                      stt=SpeechToText(settings), tts=TextToSpeech(settings))
    with TestClient(create_app(runtime=runtime, settings=settings)) as protected:
        assert protected.get("/api/status").status_code == 401
        assert protected.get("/api/status", headers={"X-Jarvis-Token": "s3cret"}).status_code == 200
        assert protected.get("/api/status?token=s3cret").status_code == 200


def test_openapi_schema_is_valid(client: TestClient) -> None:
    schema = client.get("/api/openapi.json").json()
    assert schema["info"]["title"].startswith("JARVIS")
    assert "/api/chat" in schema["paths"]


def test_websocket_streams_events(client: TestClient) -> None:
    with client.websocket_connect("/ws") as socket:
        hello = socket.receive_json()
        assert hello["type"] == "hello"
        assert hello["payload"]["status"]["agent"]["tools"] > 40

        socket.send_json({"type": "chat", "text": "take a screenshot", "speak": False})
        seen: list[str] = []
        turn = None
        for _ in range(20):
            message = socket.receive_json()
            seen.append(message["type"])
            if message["type"] == "turn":
                turn = message["payload"]
                break
        assert turn is not None
        assert {"user_message", "tool_call", "tool_result", "turn"} <= set(seen)
