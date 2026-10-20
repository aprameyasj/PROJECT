# Roadmap

The build order is deliberate: **be excellent at explicit commands before being clever.**
Each phase ships something you can use that day, and nothing later phase depends on guessing.

| # | Phase | Status |
|---|---|---|
| 1 | Voice → intent → speech | ✅ done |
| 2 | Voice → tool → computer | ✅ done |
| 3 | Memory + reminders + scheduler | ✅ done |
| 4 | Multi-step planning & workflows | ✅ done |
| 5 | Web agent (Playwright) | 🟡 code complete, needs `pip install -e ".[browser]"` |
| 6 | Computer vision: screenshot → element → click | 🟡 wired, needs a vision model or tesseract |
| 7 | Controlled terminal | 🟡 allow-listed only, by design |
| 8 | Proactive JARVIS | ⬜ intentionally not built |

---

## ✅ Phase 1 — Voice in, voice out

**Goal:** say something, JARVIS understands and answers aloud.

- Microphone loop with wake words (`jarvis`, `hey jarvis`, `hi jarvis`) — `main.py listen`.
- Three STT paths: browser Web Speech API (zero setup), OpenAI Whisper API, local
  `faster-whisper`, plus `vosk`. Chosen by availability, reported by `doctor`.
- Three TTS paths: browser `speechSynthesis` (prefers an `en-GB` voice), OpenAI TTS, system
  voice (`say` / PowerShell / `espeak`).
- The offline intent engine means Phase 1 works with **no API key at all**.

**Prove it:** `python main.py listen` → “hey jarvis, what time is it” → spoken answer.

## ✅ Phase 2 — Voice → tool → computer

**Goal:** the model picks a *named tool*; a separate executor performs the action.

- 92 tools across 9 categories, schemas inferred from code.
- `ActionBackend` protocol with a real desktop implementation (pyautogui/psutil/xdotool) and
  a simulated one, chosen automatically, always reporting which is active.
- Safety gate with three levels, deny patterns, shell allow-list, confirmations, audit log.
- Executor that never raises, emits events, enforces timeouts and a step budget.

**Prove it:** `python main.py demo` runs the MVP list — open Chrome, close this tab, open
VS Code, screenshot, web search, reminder, time, shutdown (which asks).

## ✅ Phase 3 — Memory & time

**Goal:** JARVIS remembers you and keeps your schedule.

- Short-term: per-session windows persisted in SQLite (survive restarts).
- Long-term: facts by category (profile / preference / project / goal), upserted;
  `absorb()` extracts durable facts from ordinary conversation; keyword recall by default,
  embedding-based recall when a key exists, hashed vectors when it doesn't.
- Task memory: reminders, calendar events, to-dos in one database, merged by `get_agenda`.
- Natural-language time parsing — “tomorrow at 9 AM”, “in two hours”, “every weekday at
  7:30”, “on 12 october”, “from 3pm to 4pm”, ISO timestamps — with recurrence roll-forward
  and humanised readback (“Got it, I’ll remind you tomorrow at 9:00 AM”).
- Scheduler: APScheduler jobs + an asyncio poller fallback + a sweeper that fires reminders
  missed while JARVIS was off.

**Prove it:** “remind me tomorrow at 9 AM to submit my assignment” → row in SQLite, job
armed, event + notification + speech when it fires.

## ✅ Phase 4 — Multi-step tasks

**Goal:** one sentence, several coordinated actions.

- `Planner.create_plan(goal)` tries a **named workflow** first (deterministic and reviewed),
  then the model, then validates: unknown tools and hallucinated arguments are dropped.
- `Executor.run_plan()` runs steps in order, stopping at the first failure/block/confirm and
  reporting progress (“5 of 6 steps done; skipped run_project — tell me your project”).
- Workflows: `coding_setup`, `study_setup`, `morning_routine`, `meeting_prep`, `wrap_up`,
  with variables (`project`, `subject`) pulled from memory.
- `make_plan(goal, execute=false)` lets you review a plan before it runs.

**Prove it:** `python main.py plan "prepare my workspace for coding"`.

## 🟡 Phase 5 — Web agent

**Status:** all 10 browser tools exist — `browser_navigate`, `browser_click`, `browser_type`,
`browser_extract`, `browser_screenshot`, `browser_status`, `browser_close`, plus
`open_url`, `open_in_default_browser` and `search_and_open` — behind a lazy Playwright
import. Without Playwright they return a clear “not installed” result instead of failing, and
the three that only need your default browser keep working.

**To finish:**

```bash
pip install -e ".[browser]" && playwright install chromium
JARVIS_BROWSER_HEADLESS=false python main.py serve   # watch it work
```

**Next steps, in order:**
1. Session persistence across turns (a page you opened stays open) — the `browser` service is
   already in `ToolContext`.
2. `extract` → structured data the model can reason over (tables, prices, headlines).
3. Form-filling as a Level-2 action: show the fields and values, ask before submitting.
4. Auth: never let the browser tool touch a stored password; ask the human to log in once.

## 🟡 Phase 6 — Computer vision

**Status:** `describe_screen`, `find_on_screen`, `read_screen_text`, `click_element` exist.
They take a screenshot through the backend, then either call a vision model (when
`OPENAI_API_KEY` + a vision-capable model are configured) or fall back to OCR via
`pytesseract`. Coordinates come back as pixels the executor feeds to `move_mouse`/`click` —
the model still never moves the mouse itself.

**To finish:**
```bash
pip install -e ".[vision]" && sudo apt install tesseract-ocr
# or: OPENAI_API_KEY=… with a vision-capable JARVIS_LLM_MODEL
```

**Next steps:**
1. Element cache per screenshot, so “click the second one” works without re-analysing.
2. Confidence thresholds: below ~0.7, ask instead of clicking.
3. Verification loop: screenshot → click → screenshot → confirm the UI changed.

## 🟡 Phase 7 — Controlled terminal

**Status:** `run_command` exists but is deliberately narrow: allow-listed binaries only,
per-segment checks, destructive sub-commands escalate to Level 2, 60 s timeout, output
captured as an observation, simulated in the sandbox backend.

**Next steps (each behind an explicit setting, off by default):**
1. Per-command policy file: `{"git push": "confirm", "npm test": "allow"}`.
2. Working-directory jail — refuse anything that escapes the project folder.
3. Output budgeting so a noisy command can't flood the model's context.
4. A “dry run first” mode that echoes the command and waits for approval every time.

**Never:** handing the model unrestricted shell. If a command is worth running often, it
deserves a named tool with its own permission level.

## ⬜ Phase 8 — Proactive JARVIS

**Deliberately last.** Ambient initiative multiplies the ways JARVIS can be wrong while
you're not looking, and it only earns trust after the explicit-command path is boring and
reliable.

When the time comes, the pieces are already in place: the event bus (subscribe and react),
the scheduler (time-based triggers), the agenda (what's coming up), memory (what you care
about), and the permission model (a nudge is Level 1; acting on your behalf is still Level 2).

Planned shape:
1. **Morning briefing** — a workflow, triggered at a set time, that *speaks* only.
2. **Pre-meeting prep** — 10 minutes before a calendar event, offer (don't perform) the
   `meeting_prep` routine.
3. **Habit nudges** — reminders that adapt to whether you complete them.
4. **Anomaly notices** — disk almost full, battery low, a build failing: notify, never act.

Every proactive behaviour will be individually switchable and logged, and none of it will be
able to bypass a confirmation.

---

## Standing constraints (from the original brief)

These are decisions, not open questions — they don't get revisited as the project grows:

- The **LLM never controls the machine directly**. Tools in, executor out.
- **No unrestricted shell.** Specific tools over `execute_any_terminal_command`; controlled
  terminal access only later, with permission checks.
- **SQLite, not MongoDB.** One user, one machine, one file.
- **Ship a working increment per phase.** Never “build the whole Iron Man JARVIS at once”.
- **Explicit commands before proactive behaviour.**
- The **React dashboard is a control panel**, not the agent. JARVIS is the Python process.

## Definition of done for the MVP

All eight of these work from voice *and* from the dashboard, on a real desktop, with the
safety levels above:

- [x] “Open Chrome.”
- [x] “Close this tab.”
- [x] “Open VS Code.”
- [x] “Take a screenshot.”
- [x] “Search the web for X.”
- [x] “Remind me tomorrow at 9 AM to X.”
- [x] “What time is it?”
- [x] “Shut down.” *(Level 2 — asks first)*

On a machine with no display (like CI) every one of them runs against the sandbox backend
and the audit trail shows exactly what would have happened.
