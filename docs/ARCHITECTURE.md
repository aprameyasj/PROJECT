# Architecture

JARVIS is a **local-first Python agent**. The React dashboard is a control panel that talks
to it over HTTP and a WebSocket — it is never the brain, and the agent runs fine without it.

```
                        ┌─────────────────────────────┐
   microphone / text ──▶│  jarvis/voice/listener.py   │  wake word, VAD, push-to-talk
                        └──────────────┬──────────────┘
                                       ▼
                        ┌─────────────────────────────┐
                        │  jarvis/voice/stt.py        │  browser | openai | faster-whisper | vosk
                        └──────────────┬──────────────┘
                                       ▼
        ┌──────────────────────────────────────────────────────────────┐
        │                    jarvis/core/agent.py                      │
        │                          Jarvis                              │
        │                                                              │
        │  1. core/context.py   build the prompt context               │
        │        ├─ core/memory.py    short-term + long-term           │
        │        ├─ store/database.py SQLite (sessions, facts, agenda) │
        │        └─ backends/*        what's on screen right now        │
        │                                                              │
        │  2. llm/factory.py → HybridBrain                             │
        │        ├─ llm/openai_provider.py  tool-calling model          │
        │        ├─ llm/offline.py          rule-based intent engine    │
        │        └─ llm/prompts.py          system prompt + tool rules  │
        │                    │                                          │
        │                    ▼  Decision(text | tool_calls[])           │
        │  3. core/safety.py  SafetyGate.evaluate(call, permission)     │
        │                    │                                          │
        │                    ▼  allow / confirm / block                 │
        │  4. core/executor.py  run the tool through tools/registry.py  │
        │                    │                                          │
        │                    ▼  ToolResult(ok, message, speech, data)   │
        │  5. observations → back to step 2 (max_steps, LoopGuard)      │
        │  6. Turn{intent, steps, tool_calls, results, response,        │
        │          speech, pending, duration_ms}                        │
        └───────┬───────────────────────┬───────────────────┬──────────┘
                ▼                       ▼                   ▼
      backends/desktop.py     automation/scheduler.py   core/events.py
      backends/sandbox.py     automation/workflows.py   (EventBus)
      (the machine)           (time-based triggers)          │
                                                             ▼
                                              api/server.py · api/websocket.py
                                              REST + WS + dashboard + TTS/STT
```

## The one rule

**The model proposes; the executor disposes.**

The LLM's entire authority is a list of tool names and JSON arguments. It cannot import
modules, spawn processes, or write files. Between its proposal and your machine sit:

1. `tools/registry.py` — the tool either exists with those arguments or the call fails;
2. `core/safety.py` — the permission level, deny patterns, allow-lists and confirmations;
3. `core/executor.py` — event emission, audit logging, timeouts, error containment;
4. `backends/*` — the only code that touches the OS, and it is one small interface that
   can be swapped for a simulator.

That is four independent places a bad idea can be stopped, and none of them is a prompt.

## Module guide

### `jarvis/config/settings.py`
A `pydantic-settings` object loaded from environment + `.env`, exposed as a singleton via
`get_settings()` (and `reload_settings()` for tests). Everything tunable lives here: model,
backend, safety mode, voice providers, API host/port/token, paths, timezone.

### `jarvis/core/types.py`
The shared vocabulary. Every layer speaks these dataclasses:

| Type | Purpose |
|---|---|
| `Permission` | `SAFE` / `CONFIRM` / `FORBIDDEN` — the three levels |
| `ToolCategory` | computer, browser, web, files, system, productivity, memory, vision, meta |
| `ToolCall` | `name`, `arguments`, `source`, `id` — what the model asked for |
| `ToolResult` | `ok`, `message`, `speech`, `data`, `artifacts`, `error`, `duration_ms` |
| `PendingAction` | a paused Level-2 call: `token`, `call`, `prompt`, `expires_at` |
| `PlanStep` / `Plan` | a multi-step routine the executor can run in order |
| `AgentEvent` / `EventType` | everything the dashboard is told about |
| `Turn` | the full record of one utterance: intent, steps, results, response, speech |
| `Message` | a chat message for the brain |

`Turn.to_dict()` is the wire format for REST and WebSocket; nothing else serialises.

### `jarvis/tools/registry.py`
The contract layer.

- `@tool(permission=…, category=…, examples=…)` decorates a normal function.
- `ToolRegistry.register()` infers the JSON schema from the **resolved** signature
  (`typing.get_type_hints`, so `from __future__ import annotations` still yields real
  `integer` / `boolean` / `enum` types) and from the Google-style docstring's `Args:`
  section. Required vs optional comes from the defaults.
- `ToolContext` is injected at call time and deliberately excluded from the schema.
- `execute()` coerces model output (`"3"` → `3`, `"true"` → `True`), rejects unknown and
  missing arguments, applies a timeout, and **never raises** — failures become
  `ToolResult(ok=False, error=…)` so the model can recover.
- `prompt_catalogue()` renders the compact text form used by the offline engine and the
  system prompt; `to_openai_tools()` renders the function-calling payload.
- `disable()` / `restrict_to()` let a session run a subset (e.g. no screenshots while a
  screen-share is active).

### `jarvis/core/safety.py`
See [SAFETY.md](SAFETY.md). Pure functions (`check_shell_command`, `check_path`, `check_url`,
`check_application`, `check_power`, `is_forbidden_command`, `is_forbidden_path`) plus a
`SafetyGate` that combines them with the declared permission and returns a `SafetyDecision`,
and a `LoopGuard` that stops repeated identical calls inside one turn.

### `jarvis/core/executor.py`
`Executor.execute(call, session_id)` → `ExecutionOutcome{status, result, decision, duration_ms}`
where status is `ok | failed | blocked | confirm`. It publishes `tool_call` /
`tool_result` / `confirmation_required`, writes the audit row, and hands the observation
back to the agent. `run_plan(plan, session_id)` executes a `Plan` step by step, stopping at
the first failure, block, or confirmation request and reporting how far it got.

### `jarvis/core/planner.py`
`create_plan(goal, context)` first tries to match a **named workflow** (deterministic,
reviewed, fast) and only then asks the brain for a plan. Either way the result goes through
`validate()`, which drops unknown tools, unknown arguments and anything the safety gate
would forbid — a hallucinated step cannot survive planning.

### `jarvis/core/memory.py` + `jarvis/store/`
- **Short-term**: per-session message windows, persisted in SQLite so a restart doesn't
  wipe the conversation. `window(session_id)` returns a prompt-ready slice.
- **Long-term**: facts with `key`, `value`, `category` (profile / preference / project /
  goal / general), upserted so nothing duplicates. `absorb(text)` opportunistically
  extracts durable facts from ordinary conversation ("my name is …", "I prefer …",
  "I'm working on …").
- **Task memory**: reminders, calendar events and to-dos live in the same database and
  surface through `get_agenda()`.
- **Retrieval**: keyword scoring by default; `store/embeddings.py` adds cosine similarity
  over vectors when an API key exists, and degrades to hashed bag-of-words vectors when it
  doesn't, so recall never crashes.

### `jarvis/llm/`
- `prompts.py` — the system prompt: identity, the "propose, don't perform" rule, when to
  ask vs act, how to speak (short, no emoji, no markdown out loud), and the tool catalogue.
- `openai_provider.py` — any OpenAI-compatible `/chat/completions` endpoint with
  `tools=[…]`; parses `tool_calls`, handles multi-step loops, retries once on a malformed
  response, and reports `finish_reason`.
- `offline.py` — the rule engine. 51 intent rules, each a tuple of regexes plus an
  argument builder that reuses the same time parser and app alias table as the tools.
  Matches are scored by confidence and the best one wins; a rule may `_abort` and hand off
  to the next candidate (that's how "find my resume pdf" beats "search the web for …").
  It also handles small talk, `yes`/`no` for pending confirmations, and a
  "couldn't map that" fallback that lists what JARVIS *can* do.
- `factory.py` — `HybridBrain`: real provider when configured and reachable, offline engine
  otherwise, and it can fall back mid-session if the API errors.

### `jarvis/backends/`
`ActionBackend` is a `Protocol` of ~35 primitives: `launch_app`, `terminate_app`,
`focus_window`, `list_windows`, `hotkey`, `type_text`, `press_key`, `move_mouse`, `click`,
`drag`, `scroll`, `screenshot`, `screen_size`, `open_url`, `clipboard_get/set`,
`set_volume`, `set_brightness`, `media_key`, `system_status`, `power`, `shell`, `notify`,
`action_log`.

- `desktop.py` implements them with pyautogui / psutil / xdotool / osascript / PowerShell,
  lazily imported so a missing GUI library never breaks import time.
- `sandbox.py` implements the same interface against an in-memory simulated desktop
  (running apps, windows, tabs, volume, power state, clipboard, action log) and writes real
  PNG screenshots so the vision tools have something to look at.
- `apps.py` holds the alias table (`"VS Code"` → `vscode`, `"the browser"` → `chrome`) and
  the per-platform launch commands.
- `get_backend(settings)` picks desktop when a GUI session and the libraries exist, sandbox
  otherwise, and records *why* in `describe()`. The dashboard shows that reason.

### `jarvis/automation/`
- `scheduler.py` — `ReminderScheduler`: APScheduler `DateTrigger` jobs when available, an
  asyncio poller when not, plus a sweeper that catches reminders missed while JARVIS was
  down. Firing a reminder marks it in SQLite, publishes `reminder_fired`, calls the
  notifier, publishes a `speech` event, and rolls recurring reminders forward. Jobs are
  spawned on the loop that owns the scheduler (APScheduler callbacks arrive on worker
  threads) and every future is strongly referenced so nothing is garbage-collected
  unawaited.
- `workflows.py` — named routines (`coding_setup`, `study_setup`, `morning_routine`,
  `meeting_prep`, `wrap_up`, …) as declarative step lists with `requires` variables pulled
  from memory. `build_workflow_plan()` turns one into a validated `Plan`; steps whose
  variable is unknown are skipped and JARVIS says what it needs to finish them.

### `jarvis/api/`
- `runtime.py` — builds the object graph once (`build_runtime()`): agent, event bus,
  scheduler wired to `agent.notify`, STT, TTS, optional mic listener; `status()` is the
  payload behind `/api/status` and the WebSocket `hello`.
- `websocket.py` — one subscriber per connection, serialising every `AgentEvent` plus a
  final `turn` message; inbound messages accept `chat`, `confirm`, `tool`, `workflow`,
  `state`, `reset`, `ping`.
- `server.py` — FastAPI app created with a lifespan handler: 36 REST routes, optional
  token auth (`X-Jarvis-Token` or `?token=`), CORS, the built dashboard mounted at `/`,
  screenshot and audio file serving, and SSE at `/api/events/stream` for clients that can't
  do WebSockets.

### `jarvis/cli/`
`main.py` is a thin shim onto `cli/commands.py`: `doctor`, `demo`, `ask`, `confirm`, `repl`,
`listen`, `serve`, `tools`, `reminders`, `plan`, `safety`. `doctor` is the honest one — it
probes the machine and prints exactly what will run, what will be simulated, and why.

## Design decisions worth knowing

- **SQLite, not Mongo.** One file, WAL mode, zero setup, easy to back up, and every query
  JARVIS makes is a primary-key lookup or a small scan. Postgres would only add friction to
  a single-user local agent.
- **Sandbox-first.** Being able to run the whole agent with no display, no key and no
  network is what makes it testable — 211 hermetic tests — and what makes `doctor`/`demo`
  safe to run on any machine.
- **Schemas from code.** A hand-written tool schema drifts from its implementation the day
  someone adds a parameter. Inferring it from the signature makes drift impossible.
- **Named workflows before free-form planning.** A deterministic, reviewed 5-step routine
  beats an LLM improvising one, and it's cheaper. The planner still exists for goals that
  have no workflow.
- **Events, not polling.** The core publishes; the API, the dashboard, the scheduler and
  the speaker are all just subscribers. Adding a new surface (a Telegram bridge, say) means
  subscribing, not editing the agent.
- **No proactive agent yet.** Ambient initiative multiplies the ways JARVIS can be wrong
  while you're not looking. Explicit commands first; `docs/ROADMAP.md` Phase 8 later.

## Extending JARVIS

| I want to… | Touch |
|---|---|
| add a new action | `jarvis/tools/<category>.py` (+ `_load_tools` in `core/agent.py`) — see [TOOLS.md](TOOLS.md#adding-a-tool) |
| support a new app name | `jarvis/backends/apps.py::APPLICATIONS` |
| make something ask first | its `@tool(permission=Permission.CONFIRM)` or a rule in `core/safety.py` |
| add a phrase the offline engine understands | a `Rule(...)` in `jarvis/llm/offline.py` |
| add a multi-step routine | `jarvis/automation/workflows.py` |
| add a dashboard panel | `frontend/src/hooks/useJarvis.ts` + a tab in `frontend/src/components/SidePanel.tsx` |
| swap the model | `OPENAI_BASE_URL` / `JARVIS_LLM_MODEL`, or a new class in `jarvis/llm/` |
| swap the database | `jarvis/store/database.py` (one class, ~30 methods) |
