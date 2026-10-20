# API reference

The agent exposes **36 REST routes** and **one WebSocket**, plus the built dashboard at `/`.
Interactive OpenAPI docs: <http://localhost:8000/api/docs>.

```bash
python main.py serve --host 0.0.0.0 --port 8000
```

Everything is local. There is no cloud component, no account and no telemetry.

## Authentication

Optional, off by default (single-user machine). Set `JARVIS_API_TOKEN=…` in `.env` and every
route — including the WebSocket handshake — requires either:

```
X-Jarvis-Token: <token>          # header
?token=<token>                   # or query parameter (for WebSockets / img src)
```

A missing or wrong token returns `401`. `GET /api/health` is exempt so load balancers can
probe it.

## Core

### `GET /api/health`

```json
{"ok": true, "time": "2026-09-30T13:06:19", "backend": "sandbox", "brain": "offline"}
```

### `GET /api/status`

Everything the dashboard renders in its header and agent-state panel.

```jsonc
{
  "agent": {
    "settings":  { "agent_name": "JARVIS", "platform": "linux", "home": "…/.jarvis",
                   "llm_provider": "offline", "backend": "sandbox", "safety_mode": "normal",
                   "max_steps": 8, "api": {"host": "0.0.0.0", "port": 8000, "token_required": false},
                   "wake_words": ["jarvis", "hey jarvis", "hi jarvis"] },
    "backend":   { "name": "sandbox", "simulated": true,
                   "reason": "no graphical session detected; running the simulated desktop",
                   "screen": [1920, 1080], "running_apps": ["chrome"], "open_windows": 1,
                   "tabs": [], "volume": 45, "power": "on", "actions_recorded": 1 },
    "brain":     { "name": "offline", "model": "jarvis-rule-engine", "available": true, "rules": 51 },
    "tools": 92,
    "tool_categories": {"computer": 25, "productivity": 19, "browser": 10, "system": 9,
                        "files": 8, "memory": 7, "meta": 6, "web": 4, "vision": 4},
    "memory":    { "facts": 5, "by_category": {"profile": 4, "project": 1}, "sessions": 1,
                   "embedder": {"enabled": false, "model": "hash-fallback", "dimensions": 256} },
    "database":  { "tool_calls": 18, "blocked": 0, "confirmations": 1, "pending_reminders": 1 },
    "safety":    { "mode": "normal", "blocked": 0 },
    "turns": 4
  },
  "scheduler": { "engine": "apscheduler", "running": true, "pending": 1, "fired_total": 0 },
  "stt":       { "provider": "browser", "available": false, "browser_supported": true, "microphone": false },
  "tts":       { "provider": "browser", "available": false, "browser_supported": true, "voice": "onyx" },
  "listener":  { "available": false },
  "clients": 1
}
```

### `POST /api/chat`

The main entry point. Body: `{"text": "...", "session_id": "" (optional), "speak": true}`.
Returns the full `Turn`:

```jsonc
{
  "id": "turn_e8e55937115b",
  "session_id": "s_20260930-130619_745",
  "user_text": "take a screenshot",
  "response": "Screenshot captured.",          // what the console shows
  "speech": "Screenshot captured.",            // what JARVIS says
  "intent": "screenshot",
  "ok": true,
  "steps": [                                   // human-readable trace
    {"tool": "take_screenshot", "arguments": {}, "status": "ok",
     "message": "Screenshot saved to …/sandbox-20260930-131247-499.png",
     "duration_ms": 33, "simulated": true}
  ],
  "tool_calls": [
    {"name": "take_screenshot", "arguments": {}, "id": "call_f889f1e97cb9",
     "source": "offline-intent", "reasoning": null}
  ],
  "tool_results": [
    {"ok": true, "tool": "take_screenshot", "call_id": "call_f889f1e97cb9",
     "message": "…", "speech": "Screenshot captured.", "duration_ms": 33,
     "simulated": true,
     "data": {"width": 1280, "height": 720, "bytes": 10552, "path": "…png",
              "windows": [{"app": "chrome", "title": "New Tab - Google Chrome", "focused": true}]},
     "artifacts": [{"type": "image", "path": "…png", "note": ""}],
     "error": null}
  ],
  "plan": null,                                // set for multi-step runs
  "pending": null,                             // set when a Level-2 action is waiting
  "simulated": true,                           // true whenever the sandbox backend ran it
  "provider": "offline", "model": "jarvis-rule-engine",
  "duration_ms": 34, "created_at": 1790773967.85, "error": null
}
```

`step.status` is one of `ok`, `failed`, `blocked`, `confirm`, `skipped`. An empty or
whitespace `text` returns `422`.

### `POST /api/confirm`

Body: `{"token": "cf_9f2a…", "approved": true}` → the same `Turn` shape, describing what
happened after the decision. `GET /api/pending` lists every open confirmation
(`{"pending": [{"token", "tool", "arguments", "prompt", "expires_at"}]}`).

Tokens are single-use and expire after `JARVIS_CONFIRM_TTL` seconds (default 300). An
expired or unknown token yields `ok: true` with a response explaining that it expired —
never a silent success.

### `POST /api/voice`

Speech → text → agent. Accepts three body shapes:

| Content type | Body | Use |
|---|---|---|
| `audio/webm`, `audio/wav`, … | raw bytes | browser MediaRecorder fallback |
| `application/json` | `{"audio": "<base64, data: URL ok>", "filename": "clip.webm"}` | same, base64-encoded |
| `application/json` | `{"text": "what time is it"}` | browser already did the STT |

Query params: `session_id`, `speak=true`. Response:

```json
{"text": "what time is it", "turn": { …Turn… }, "transcript": { …only when audio was sent… }}
```

Server-side STT is used when configured (`openai` / `faster_whisper` / `vosk`); with none
available the endpoint still accepts browser-transcribed `text`, so the mic works with zero
setup. `GET /api/tts?text=…` returns MP3 audio when server-side TTS exists, otherwise
`{"ok": false, "detail": "server-side TTS unavailable; the browser speaks instead"}`.

`POST /api/listener/{start|stop|status}` controls the server-side microphone loop (needs
`sounddevice` + `numpy`).

### `POST /api/session/reset`

Body `{"session_id": ""}` → clears short-term memory for that session.

## Tools

| Route | What |
|---|---|
| `GET /api/tools` | `{count, categories: {name: [tools]}, catalogue}` — the whole registry |
| `GET /api/tools/{name}` | one spec: description, permission, category, JSON schema, examples, stats. `404` if unknown |
| `POST /api/tools/{name}/run` | run it manually: `{"arguments": {…}, "session_id": ""}` |

`POST /api/tools/{name}/run` goes through the **same safety gate** as a model-initiated
call, and returns the executor's outcome rather than a bare result:

```jsonc
{"status": "ok",          // ok | failed | blocked | confirm
 "result": { …ToolResult… },
 "decision": {"allowed": true, "permission": "safe", "reason": "safe action", "prompt": ""},
 "duration_ms": 31,
 "pending": null}         // set when status == "confirm"
```

```bash
curl -X POST localhost:8000/api/tools/run_command/run \
     -H 'content-type: application/json' -d '{"arguments":{"command":"rm -rf /"}}'
# {"status":"blocked","decision":{"allowed":false,"permission":"forbidden", …}}
```

## Productivity

| Route | Notes |
|---|---|
| `GET /api/reminders?status=pending&limit=50` | `{reminders: […], count}` |
| `POST /api/reminders` | `{"task", "when", "recurrence"?, "channel"?, "notes"?}` — `when` accepts ISO **or** natural language (“tomorrow at 9 AM”), parsed by the same engine the voice path uses |
| `POST /api/reminders/{id}/snooze?minutes=15` | rolls the reminder forward and re-arms the scheduler |
| `POST /api/reminders/{id}/complete` | marks it fired |
| `DELETE /api/reminders/{id}` | cancels it (`404` when the id doesn’t exist) |
| `GET /api/calendar?window=today\|week\|month` | `{events, count}` |
| `POST /api/calendar` | `{"title", "starts_at", "ends_at"?, "duration_minutes"?, "location"?, "notes"?, "remind_minutes_before"?}` — creating an event also creates its pre-reminder |
| `DELETE /api/calendar/{event_id}` | removes the event and its pre-reminder |
| `GET /api/tasks?status=todo` · `POST /api/tasks` · `POST /api/tasks/{id}/status?status=done` | the to-do list |
| `GET /api/agenda?window=today\|week\|month` | runs the `get_agenda` tool and returns its `ToolResult`: the merged agenda is in `data` |

```jsonc
{"ok": true, "tool": "get_agenda", "message": "Nothing scheduled.",
 "speech": "Your today is clear. Nothing scheduled.",
 "data": {"window": "today", "reminders": [], "events": [], "tasks": [], "timeline": []}}
```

Reminder rows look like:

```json
{"id": 12, "task": "submit my assignment", "remind_at": "2026-10-01T09:00:00",
 "recurrence": "", "channel": "all", "status": "pending", "notes": "",
 "created_at": "2026-09-30T13:06:19", "fired_at": null}
```

## Memory

| Route | Notes |
|---|---|
| `GET /api/memory` | `{profile: {key: value}, categories: {category: [fact rows]}, stats}` |
| `POST /api/memory` | `{"key", "value", "category"?}` — upsert (no duplicates), returns the stored fact row |
| `DELETE /api/memory?key=name&category=profile` | forgets one fact → `{"ok": true, "key": "name"}`. Omit `category` to delete the key in any category |

Fact rows carry `id`, `key`, `value`, `category`, `confidence`, `source`, `created_at`,
`updated_at` (and `embedding` when vector memory is enabled). Categories are `profile`,
`preference`, `project`, `goal`, `general`.

## Automation & inspection

| Route | Notes |
|---|---|
| `GET /api/workflows` | the named multi-step routines with their steps |
| `POST /api/workflows/{name}/run` | `{"variables": {"project": "smart-water-grid"}, "execute": true}` — `404` for an unknown workflow |
| `POST /api/plan` | `{"goal", "execute": false}` → `{plan: {title, steps[…], source}, draft}`. With `execute: true` the plan is run and you get a `Turn` |
| `GET /api/audit?limit=100` | `{entries: [{tool, arguments, decision, reason, duration_ms, created_at, session_id}], stats: {tool_calls, blocked, confirmed, failed}}` |
| `GET /api/events?limit=50` | recent event-bus traffic (the WebSocket backlog) |
| `GET /api/events/stream` | the same events as **Server-Sent Events**, for clients that can't do WebSockets |
| `GET /api/screenshots` | `{screenshots: [{name, path, url, size, created}]}` newest first |
| `GET /api/screenshots/{name}` · `GET /api/audio/{name}` | the PNG / MP3 bytes (`image/png`, `audio/mpeg`) |

## WebSocket `/ws`

One connection per dashboard tab. On connect the server pushes a `hello` with the full
status payload, then streams every agent event in real time.

**Server → client**

```jsonc
{"type": "hello",                 "payload": { …same shape as /api/status… }}
{"type": "user_message",          "payload": {"text": "close this tab", "session_id": "…"}}
{"type": "thinking",              "payload": {"step": 1, "provider": "offline"}}
{"type": "plan",                  "payload": {"title": "…", "steps": […]}}
{"type": "tool_call",             "payload": {"id": "call_…", "name": "close_current_tab",
                                              "arguments": {}, "permission": "safe",
                                              "category": "computer", "source": "offline-intent"}}
{"type": "tool_result",           "payload": {"ok": true, "tool": "close_current_tab",
                                              "message": "…", "speech": "…", "data": {},
                                              "artifacts": [], "duration_ms": 12, "simulated": true}}
{"type": "confirmation_required", "payload": {"token": "cf_…", "tool": "power",
                                              "arguments": {"action": "shutdown"},
                                              "prompt": "Shut down this computer now?",
                                              "expires_at": "…"}}
{"type": "agent_message",         "payload": {"text": "Done.", "session_id": "…"}}
{"type": "speech",                "payload": {"text": "Done.", "voice": true}}
{"type": "reminder_fired",        "payload": {"id": 12, "task": "…", "message": "…", "missed": false}}
{"type": "notification",          "payload": {"title": "JARVIS reminder", "body": "…"}}
{"type": "error",                 "payload": {"message": "…", "where": "executor"}}
{"type": "turn",                  "payload": { …the complete Turn… }}   // always last
```

Observed order for a normal command:

```
user_message → thinking → tool_call → tool_result → agent_message → speech → turn
```

**Client → server**

```jsonc
{"type": "chat",     "text": "…", "session_id": "", "speak": true}
{"type": "confirm",  "token": "cf_…", "approved": true}
{"type": "tool",     "name": "take_screenshot", "arguments": {}}
{"type": "workflow", "name": "coding_setup", "variables": {"project": "…"}}
{"type": "state"}                                    // re-send the status payload
{"type": "reset",    "session_id": ""}
{"type": "ping"}                                     // → {"type":"pong"}
```

Unknown message types are answered with an `error` event; the connection stays open. If the
socket drops, the dashboard reconnects with exponential backoff and falls back to REST
polling in the meantime.

## Dashboard

`frontend/dist` is mounted at `/` when it exists (`cd frontend && npm run build`), so one
process serves the agent *and* the UI with no CORS and no second port. The Vite dev server
on `:5173` proxies `/api` and `/ws` to `127.0.0.1:8000` and is bound to `0.0.0.0` with
`allowedHosts: true` for tunnels and preview proxies; every URL in the client is relative.

## Quick smoke test

```bash
curl -s localhost:8000/api/health | jq .
curl -s -X POST localhost:8000/api/chat -H 'content-type: application/json' \
     -d '{"text":"Jarvis, close this tab","speak":false}' | jq '.intent, .steps'
curl -s -X POST localhost:8000/api/reminders -H 'content-type: application/json' \
     -d '{"task":"stretch","when":"in 2 minutes"}' | jq .
curl -s localhost:8000/api/tools/take_screenshot | jq '.permission, .parameters'
```
