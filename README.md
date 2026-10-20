# JARVIS — a personal AI agent that actually *does* things

> Speak naturally → JARVIS understands the intent → picks a **named tool** → a separate
> **execution layer** performs the action on your computer or services → JARVIS reports back.

The core design rule, repeated everywhere in this codebase:

**The language model never touches your machine.** It is handed a fixed catalogue of typed
tools (`close_current_tab`, `create_reminder`, `take_screenshot`, …). It can only *request*
one of them. A separate executor validates the request against a permission model, then
performs it through an action backend. There is no `execute_any_terminal_command` in the
catalogue, no `eval`, no raw shell handed to the model.

```
 ┌──────────┐   ┌───────────────────────────────────────────┐   ┌──────────────┐
 │  Voice / │   │              JARVIS CORE                  │   │   Action     │
 │  text in │──▶│  context + memory ── intent/plan ── LLM   │──▶│   Executor   │
 └──────────┘   └───────────────────────────────────────────┘   └──────┬───────┘
                        ▲                                    ▲         │ safety gate
                        │                                    │         ▼
                 ┌──────┴──────┐                     ┌───────┴───┐  ┌──────────┐
                 │   Result /  │◀────── observations │  92 named │  │ desktop /│
                 │   speech    │                     │   tools   │  │ sandbox  │
                 └─────────────┘                     └───────────┘  └──────────┘
```

Everything below runs today, on a laptop, with **or without** an LLM API key.

---

## Quickstart (5 minutes)

```bash
git clone <this repo> && cd PROJECT

python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python main.py doctor      # what will run on THIS machine
python main.py demo        # the whole MVP command list, end to end
python main.py ask "Jarvis, remind me tomorrow at 9 AM to submit my assignment"
```

Now start the agent + dashboard:

```bash
python main.py serve                  # REST + WebSocket + built dashboard on :8000

# optionally, the hot-reloading dev dashboard:
cd frontend && npm install && npm run dev   # http://localhost:5173 (proxies /api + /ws to :8000)
```

Open <http://localhost:8000> (or :5173 while developing), press the mic or type, and say
something. The console shows every stage of the turn: what you said, what JARVIS decided,
which tool ran, what came back, and what it says out loud.

> **No API key?** Perfect. JARVIS falls back to a built-in intent engine that handles the
> entire MVP command set offline. Add `OPENAI_API_KEY` (or any OpenAI-compatible endpoint —
> Ollama, LM Studio, OpenRouter) later for open-ended multi-step reasoning.
>
> **No display / not your desktop yet?** JARVIS detects it and runs the **sandbox backend**:
> a simulated desktop that records every action instead of performing it. Same code path,
> same tools, same safety rules — nothing pretends to work that doesn't.

### What it can do right now

| You say | Intent | Tool that runs | Level |
|---|---|---|---|
| “Jarvis, open Chrome.” | open_app | `open_application(application="chrome")` | L1 |
| “Jarvis, close this tab.” | close_tab | `close_current_tab()` | L1 |
| “Jarvis, open VS Code.” | open_app | `open_application(application="vscode")` | L1 |
| “Jarvis, take a screenshot.” | screenshot | `take_screenshot()` → saved + logged | L1 |
| “Search the web for AWS Lambda.” | web_search | `search_web(query="aws lambda")` | L1 |
| “Remind me tomorrow at 9 AM to submit my assignment.” | reminder | `create_reminder(task=…, when="tomorrow at 9 AM")` | L1 |
| “What time is it?” | time | `get_time()` | L1 |
| “What’s my system status?” | status | `get_system_status()` | L1 |
| “Prepare my workspace for coding.” | workflow | `run_workflow("coding_setup")` → 5-step plan | L1 |
| “What’s on my screen?” | vision | `describe_screen()` | L1 |
| “Remember that my project is called Smart Water Grid.” | memory | `remember_fact(…)` | L1 |
| “Jarvis, shut down.” | power | `power(action="shutdown")` → **asks first** | **L2** |
| “Delete /etc/passwd” | — | **blocked, audited** | **L3** |
| “rm -rf /” | — | **blocked, audited** | **L3** |

Reminders are real: the phrase is parsed into a timestamp, stored in SQLite, armed in the
scheduler, and when it fires JARVIS publishes an event, pushes a notification, speaks it,
and rolls recurring reminders forward. If JARVIS was asleep when one was due, it fires the
missed reminder on startup and says so.

---

## The architecture, layer by layer

| Layer | Where | What it does |
|---|---|---|
| **Voice** | `jarvis/voice/` | Mic loop + wake word, speech→text (browser / OpenAI Whisper / faster-whisper / Vosk), text→speech (browser / OpenAI / system voice) |
| **Core** | `jarvis/core/` | `Jarvis` orchestrator: builds context, asks the brain for a decision, runs tools, collects observations, loops until an answer, speaks the result |
| **Brain** | `jarvis/llm/` | `HybridBrain` → OpenAI-compatible tool-calling model when a key exists, otherwise the offline rule engine. Prompts live in `prompts.py` |
| **Tools** | `jarvis/tools/` | 92 `@tool`-decorated functions across 9 categories. The registry infers each JSON schema from the signature + docstring, so the schema can never drift from the code |
| **Safety** | `jarvis/core/safety.py` | Three permission levels, deny patterns for commands/paths/URLs, shell allow-list, loop guard, confirmation tokens with a TTL |
| **Executor** | `jarvis/core/executor.py` | The only place a tool actually runs. Emits events, records the audit trail, returns observations to the brain |
| **Backends** | `jarvis/backends/` | `DesktopBackend` (pyautogui/psutil/xdotool — really moves your mouse) and `SandboxBackend` (simulated desktop that records everything). One interface, chosen at startup |
| **Memory** | `jarvis/core/memory.py`, `jarvis/store/` | Short-term conversation windows (SQLite-backed, survive restarts), long-term facts/profile/projects, task memory, optional embeddings for semantic recall |
| **Automation** | `jarvis/automation/` | APScheduler-driven reminder engine + named multi-step workflows (plans the executor can run) |
| **API** | `jarvis/api/` | FastAPI: 36 REST routes + one WebSocket that streams every event; serves the built React dashboard |
| **Dashboard** | `frontend/` | React 18 + Vite + TypeScript control panel. It is **not** the agent — it is a window onto it |

### How a single turn flows

```python
turn = await jarvis.process("Jarvis, close this tab.")
```

1. **Context** — session history, user profile, today’s agenda, active apps, pending confirmations.
2. **Decide** — the brain returns either prose or one-or-more `ToolCall`s with typed arguments.
3. **Gate** — `SafetyGate.evaluate()` inspects the tool name, the declared permission *and*
   every string argument. Level 3 → blocked. Level 2 → a `PendingAction` with a token; the
   turn ends with a question. Level 1 → proceed.
4. **Execute** — the executor calls the tool with a `ToolContext` (settings, backend, db,
   memory, event bus, scheduler, session id). Tools return a `ToolResult`; nothing raises.
5. **Observe & repeat** — results are appended as observations and the brain may call another
   tool, up to `JARVIS_MAX_STEPS` (default 8), guarded against repeated identical calls.
6. **Respond** — a summary is written to the database, published as events, spoken aloud, and
   returned as a `Turn` (intent, steps, tool calls, results, response, speech, duration).

---

## Safety model

Three levels, enforced in code — not in the prompt.

| Level | Meaning | Examples | Behaviour |
|---|---|---|---|
| **L1 · safe** | reversible, read-only, or cosmetic | open an app, close a tab, screenshot, search, read a file, create a reminder | runs immediately |
| **L2 · confirm** | outbound, destructive, or hard to undo | shutdown/restart, delete a file, write outside the workspace, install software, send an email/message, raw shell commands | asks, waits for `yes`/`no`, expires after `JARVIS_CONFIRM_TTL` (300 s) |
| **L3 · forbidden** | never autonomous | `rm -rf /`, `mkfs`, `dd` to a device, fork bombs, disabling the firewall, deleting system files, reading `/etc/shadow` or `~/.ssh` | blocked and audited; the tool doesn’t even exist in the catalogue |

Defence in depth, because a prompt is not a security boundary:

- The gate scans **argument strings** for deny patterns, so a model can’t smuggle
  `rm -rf /` through an innocent-looking tool name.
- Raw shell runs against an **allow-list** of read-only binaries (`ls`, `git status`,
  `python --version`, …). `rm`, `sudo`, `curl … | sh` are refused outright.
- Writes are confined to the JARVIS workspace; anything outside asks first.
- URLs are checked against cloud-metadata and private-network ranges.
- Closing OS-critical processes (explorer, Finder, systemd) is blocked.
- Every decision — allow, confirm, block — is written to an **audit log** you can read with
  `python main.py ask "show me the audit trail"` or `GET /api/audit`.
- `JARVIS_SAFETY=strict` asks for anything above L1; `yolo` skips L2 prompts but **never**
  enables L3.

Try it yourself: `python main.py safety`.

---

## Running it on your own machine

The sandbox backend exists so development never needs a display. To let JARVIS really drive
your desktop, install the extras and set `JARVIS_BACKEND=desktop`.

**Windows / macOS**

```bash
pip install -e ".[desktop]"        # pyautogui, psutil, pyperclip
copy .env.example .env             # Windows     (or: cp on macOS/Linux)
python main.py serve
```

**Linux (X11)**

```bash
sudo apt install python3-xlib scrot xdotool
pip install -e ".[desktop]"
cp .env.example .env
python main.py serve
```

Optional phases:

```bash
pip install -e ".[browser]" && playwright install chromium   # Phase 4: web agent
pip install -e ".[voice]"                                    # local mic + offline Whisper STT
pip install -e ".[vision]" && sudo apt install tesseract-ocr # OCR fallback for screen reading
```

`python main.py doctor` tells you exactly what is available, what is simulated, and why.

### Voice

Two independent paths — use whichever suits you:

- **Browser (zero setup, default in the dashboard).** The Web Speech API transcribes your
  mic and speaks JARVIS back with an `en-GB` voice. If your browser lacks it, the dashboard
  records with MediaRecorder and posts the audio to `POST /api/voice`.
- **Server-side.** `JARVIS_STT=openai` (Whisper API) or `faster-whisper` (fully offline),
  `JARVIS_TTS=openai` or `system` (`say` / PowerShell / `espeak`). Then `python main.py listen`
  runs a wake-word loop in your terminal: *“hey jarvis, …”*.

---

## The dashboard

React 18 + Vite + TypeScript, dark HUD theme. It is a **control panel**, not the agent —
kill the browser and JARVIS keeps running.

- **Console** — your utterances, JARVIS’s replies, and an inline trace of every tool call
  with its permission badge, arguments, result and duration.
- **Confirm card** — Level-2 actions pause the turn and render Approve / Deny buttons;
  the decision goes back over the WebSocket with its token.
- **Composer + mic** — type, or push-to-talk; live transcript while you speak.
- **Agent state** — current brain, backend, simulated/real, safety mode, step budget.
- **Side panel** — eight tabs: Reminders, Calendar, Tasks, Memory, Workflows, Tools (all 92,
  with schemas), Screens (screenshot gallery), Audit (the safety log).
- **Toasts** — reminders firing, notifications, errors.
- A banner appears whenever the backend is **simulated**, so you never mistake a dry run
  for a real action.

All URLs are relative and the dev server binds `0.0.0.0` with `allowedHosts: true`, so it
works behind a proxy or a tunnel. `/api` and `/ws` are proxied to `127.0.0.1:8000`.

---

## CLI

```
python main.py doctor                       # capability + backend report
python main.py demo                         # MVP command list, end to end
python main.py ask "open vscode"            # one-shot command
python main.py repl                         # interactive console
python main.py listen                       # microphone + wake word
python main.py serve [--host --port]        # REST + WebSocket + dashboard
python main.py tools [--category computer]  # the catalogue
python main.py reminders                    # what's scheduled
python main.py plan "prepare my workspace for coding"
python main.py confirm <token> yes|no       # resolve a pending Level-2 action
python main.py safety                       # demonstrate L1/L2/L3
```

## API

`GET /api/status` · `POST /api/chat` · `POST /api/confirm` · `POST /api/voice` ·
`GET /api/tools` · `POST /api/tools/{name}/run` · `GET|POST|DELETE /api/reminders` ·
`GET|POST /api/calendar` · `GET|POST /api/tasks` · `GET|POST|DELETE /api/memory` ·
`GET /api/agenda` · `GET /api/audit` · `GET /api/events` (+`/api/events/stream` SSE) ·
`POST /api/plan` · `GET /api/workflows` · `POST /api/workflows/{name}/run` ·
`GET /api/screenshots` · `GET /api/health` — interactive docs at `/api/docs`.

```bash
curl -X POST localhost:8000/api/chat -H 'content-type: application/json' \
     -d '{"text":"Jarvis, close this tab","speak":false}'
```

The WebSocket at `/ws` streams every event in real time:

```
hello → user_message → thinking → tool_call → tool_result → agent_message → speech → turn
                       ↘ plan / confirmation_required / reminder_fired / notification / error
```

Set `JARVIS_API_TOKEN` and every request must carry `X-Jarvis-Token` (or `?token=`).

---

## Configuration

Copy `.env.example` to `.env`. Every key is optional. The ones you’ll touch first:

```bash
OPENAI_API_KEY=            # or JARVIS_LLM_API_KEY; any OpenAI-compatible base URL works
JARVIS_BACKEND=auto        # auto | desktop | sandbox
JARVIS_SAFETY=normal       # normal | strict | yolo
JARVIS_NAME=JARVIS
JARVIS_USER_NAME=Aprameya
JARVIS_SPEAK=true
JARVIS_API_PORT=8000
```

Data lives in `./.jarvis/`: `jarvis.db` (SQLite, WAL), `screenshots/`, `audio/`, `logs/`,
`downloads/`. Delete the folder to start from a clean slate.

## Tests

```bash
pytest -q          # 211 tests: registry, safety, time parsing, intent, agent flow,
                   # reminders/scheduler, memory, backends, REST + WebSocket API
```

Tests are hermetic: an isolated database per test, the sandbox backend, no network, no LLM
key, no display.

## Roadmap

| Phase | What | Status |
|---|---|---|
| 1 | Voice → intent → speech (Phase 1 loop) | ✅ done |
| 2 | Voice → tool → computer (real actions, sandbox fallback) | ✅ done |
| 3 | Memory (short/long-term/task) + reminders + scheduler | ✅ done |
| 4 | Multi-step planning + named workflows | ✅ done |
| 5 | Web agent (Playwright): navigate, extract, fill, click | 🟡 tools ready, needs `pip install -e ".[browser]"` |
| 6 | Computer vision: screenshot → locate element → click | 🟡 vision model + OCR hooks wired, needs a key or tesseract |
| 7 | Controlled terminal with per-command permission checks | 🟡 allow-listed `run_command` only, by design |
| 8 | Proactive JARVIS (morning briefings, habit nudges) | ⬜ deliberately not built yet |

Deliberate non-goals for now: unrestricted shell access, MongoDB, an always-listening
proactive agent, and any design where the model calls the OS directly.

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md), [`docs/TOOLS.md`](docs/TOOLS.md),
[`docs/SAFETY.md`](docs/SAFETY.md), [`docs/API.md`](docs/API.md) and
[`docs/ROADMAP.md`](docs/ROADMAP.md) for the long version.

## License

MIT.
