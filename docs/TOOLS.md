# Tool catalogue

> Generated from the live registry by `python scripts/export_tools.py`.
> The JSON schema sent to the model is inferred from each function's signature and
> docstring — there is no separate schema file to keep in sync.

**92 tools in 9 categories.** `L1` runs immediately,
`L2` asks for confirmation first, `L3` does not exist in the catalogue at all.

Every tool receives a `ToolContext` (settings, action backend, database, memory,
event bus, scheduler, browser session, session id, the user's utterance) that is
**never** exposed in the schema, and returns a `ToolResult`:

```python
@dataclass
class ToolResult:
    ok: bool                 # did it work
    message: str             # what happened, for logs and the console
    speech: str              # what JARVIS says out loud
    data: dict               # structured payload for the model / dashboard
    artifacts: list[dict]    # screenshots, files, audio ({type, path, url})
    error: str | None        # set when ok is False
    duration_ms: int
```

---

## computer · 25 tools

### `click` L1

Click the mouse at screen coordinates (or wherever it currently is).

`click(x?, y?, button?, clicks?)`

| argument | type | notes |
|---|---|---|
| `x` | integer \| null | Horizontal pixel coordinate. Omit to click at the current mouse position. |
| `y` | integer \| null | Vertical pixel coordinate. |
| `button` | `left` \| `right` \| `middle` | default `"left"` — Mouse button: "left", "right" or "middle". |
| `clicks` | `1` \| `2` | default `1` — 1 for a single click, 2 for a double click. |

### `close_application` L2

Close a running application. Unsaved work may be lost.

`close_application(**application**, force?)`

| argument | type | notes |
|---|---|---|
| `application` | string | **required** — App name or alias to close. |
| `force` | boolean | default `false` — Force-kill the process instead of asking it to quit gracefully. |

Say:

- “Close Chrome”
- “Quit VS Code”

### `close_current_tab` L1

Close the currently focused browser tab (sends Ctrl/Cmd+W).

`close_current_tab()`

Say:

- “Jarvis, close this tab.”
- “Close the current tab”

### `drag` L1

Drag the mouse from one point to another (selection, moving windows, sliders).

`drag(**x1**, **y1**, **x2**, **y2**, duration?)`

| argument | type | notes |
|---|---|---|
| `x1` | integer | **required** — Starting X coordinate. |
| `y1` | integer | **required** — Starting Y coordinate. |
| `x2` | integer | **required** — Ending X coordinate. |
| `y2` | integer | **required** — Ending Y coordinate. |
| `duration` | number | default `0.4` — Seconds the drag should take. |

### `get_screen_info` L1

Report the screen resolution, mouse position and focused window.

`get_screen_info()`

### `list_applications` L1

List the applications JARVIS knows how to open on this machine.

`list_applications()`

### `list_windows` L1

List the windows currently open on the desktop.

`list_windows()`

### `media_control` L1

Control media playback with the hardware media keys.

`media_control(action?)`

| argument | type | notes |
|---|---|---|
| `action` | `play` \| `pause` \| `playpause` \| `next` \| `previous` \| `stop` \| `mute` | default `"playpause"` — One of play, pause, playpause, next, previous, stop, mute. |

### `minimise_window` L1

Minimise the current window, or all windows (show desktop).

`minimise_window(all_windows?)`

| argument | type | notes |
|---|---|---|
| `all_windows` | boolean | default `false` — When true, minimise everything and show the desktop. |

### `move_mouse` L1

Move the mouse pointer to absolute screen coordinates.

`move_mouse(**x**, **y**, duration?)`

| argument | type | notes |
|---|---|---|
| `x` | integer | **required** — Horizontal pixel coordinate. |
| `y` | integer | **required** — Vertical pixel coordinate. |
| `duration` | number | default `0.2` — Seconds the movement should take (0 = instant). |

### `new_tab` L1

Open a new browser tab, optionally at a URL.

`new_tab(url?)`

| argument | type | notes |
|---|---|---|
| `url` | string | default `""` — Optional URL to open in the new tab. |

### `next_tab` L1

Switch to the next or previous browser tab.

`next_tab(direction?)`

| argument | type | notes |
|---|---|---|
| `direction` | `next` \| `previous` | default `"next"` — "next" (Ctrl+Tab) or "previous" (Ctrl+Shift+Tab). |

### `open_application` L1

Open (launch) an application on this computer.

`open_application(**application**, args?)`

| argument | type | notes |
|---|---|---|
| `application` | string | **required** — App name or alias, e.g. "vscode", "chrome", "terminal", "spotify", "calculator". |
| `args` | string | default `""` — Optional extra arguments passed to the app (a folder path for an editor, a URL for a browser). |

Say:

- “Jarvis, open VS Code.”
- “Open Chrome”
- “Launch the terminal”

### `paste_text` L1

Put text on the clipboard and paste it into the focused window.

`paste_text(**text**)`

| argument | type | notes |
|---|---|---|
| `text` | string | **required** — Text to paste. |

### `press_hotkey` L1

Press a key combination such as Ctrl+S, Alt+Tab or Ctrl+Shift+N.

`press_hotkey(**keys**)`

| argument | type | notes |
|---|---|---|
| `keys` | array<string> | **required** — Keys to hold together, e.g. ["ctrl", "s"] or ["alt", "tab"]. |

### `press_key` L1

Press a single keyboard key.

`press_key(**key**, times?)`

| argument | type | notes |
|---|---|---|
| `key` | string | **required** — Key name, e.g. "enter", "esc", "tab", "space", "backspace", "up", "down", "f5". |
| `times` | integer | default `1` — How many times to press it (1-50). |

### `reopen_closed_tab` L1

Re-open the most recently closed browser tab (Ctrl/Cmd+Shift+T).

`reopen_closed_tab()`

### `scroll` L1

Scroll the mouse wheel.

`scroll(amount?, direction?)`

| argument | type | notes |
|---|---|---|
| `amount` | integer | default `-3` — Number of scroll clicks. Positive scrolls up, negative scrolls down. |
| `direction` | `up` \| `down` | Optional explicit "up" or "down" (overrides the sign of amount). |

### `select_all_and_copy` L1

Select everything in the focused window and copy it to the clipboard.

`select_all_and_copy()`

### `set_brightness` L1

Set the screen brightness.

`set_brightness(**percent**)`

| argument | type | notes |
|---|---|---|
| `percent` | integer | **required** — Brightness 0-100. |

### `set_volume` L1

Set, mute or adjust the system volume.

`set_volume(percent?, muted?, delta?)`

| argument | type | notes |
|---|---|---|
| `percent` | integer \| null | Absolute volume 0-100. |
| `muted` | boolean \| null | true to mute, false to unmute. |
| `delta` | integer \| null | Relative change, e.g. +10 or -20. |

Say:

- “Set the volume to 30%”

### `switch_window` L1

Bring a window to the front, by title, or cycle to the next window.

`switch_window(title?)`

| argument | type | notes |
|---|---|---|
| `title` | string | default `""` — Part of the window or app title to focus. Empty = cycle (Alt+Tab). |

### `take_screenshot` L1

Capture the whole screen and save it as a PNG.

`take_screenshot(note?)`

| argument | type | notes |
|---|---|---|
| `note` | string | default `""` — Optional label saved with the screenshot so it is easy to find later. |

Say:

- “Jarvis, take a screenshot.”

### `type_text` L1

Type text into whatever currently has keyboard focus.

`type_text(**text**, interval?, press_enter?)`

| argument | type | notes |
|---|---|---|
| `text` | string | **required** — The exact characters to type. |
| `interval` | number | default `0.0` — Delay in seconds between keystrokes (0 = as fast as possible). |
| `press_enter` | boolean | default `false` — Press Enter after typing (useful for searches and forms). |

Say:

- “Type hello world”

### `wait` L1

Wait for a number of seconds — useful between steps of a plan.

`wait(seconds?)`

| argument | type | notes |
|---|---|---|
| `seconds` | number | default `1.0` — How long to wait (max 30). |

---

## productivity · 19 tools

### `cancel_calendar_event` L1

Cancel a calendar event by id or by matching its title.

`cancel_calendar_event(event_id?, title?)`

| argument | type | notes |
|---|---|---|
| `event_id` | integer \| null | Numeric event id. |
| `title` | string | default `""` — Part of the event title to match. |

### `cancel_reminder` L1

Cancel a reminder by id or by matching its text.

`cancel_reminder(reminder_id?, needle?, all_pending?)`

| argument | type | notes |
|---|---|---|
| `reminder_id` | integer \| null | Numeric reminder id. |
| `needle` | string | default `""` — Text to match against reminder tasks, e.g. "assignment". |
| `all_pending` | boolean | default `false` — Cancel every pending reminder (use with care). |

### `communication_status` L1

Report which outbound channels are configured (email, webhook).

`communication_status()`

### `complete_reminder` L1

Mark a reminder as done.

`complete_reminder(needle?, reminder_id?)`

| argument | type | notes |
|---|---|---|
| `needle` | string | default `""` — Text to match, e.g. "assignment". |
| `reminder_id` | integer \| null | Numeric reminder id. |

### `complete_task` L1

Mark a task as done (or change its status).

`complete_task(needle?, task_id?, status?)`

| argument | type | notes |
|---|---|---|
| `needle` | string | default `""` — Text to match against task titles. |
| `task_id` | integer \| null | Numeric task id. |
| `status` | `done` \| `doing` \| `todo` \| `cancelled` | default `"done"` — New status: done, doing, todo or cancelled. |

### `create_calendar_event` L1

Create a calendar event, optionally with a reminder before it starts.

`create_calendar_event(**title**, starts_at?, ends_at?, duration_minutes?, location?, notes?, remind_minutes_before?)`

| argument | type | notes |
|---|---|---|
| `title` | string | **required** — What the event is, e.g. "Interview at Google". |
| `starts_at` | string | default `""` — ISO datetime or natural language ("tomorrow at 3pm"). |
| `ends_at` | string | default `""` — Optional ISO datetime or natural language end time. |
| `duration_minutes` | integer | default `0` — Length in minutes (used when ends_at is missing). |
| `location` | string | default `""` — Optional place or meeting link. |
| `notes` | string | default `""` — Optional description. |
| `remind_minutes_before` | integer | default `15` — Create a reminder this many minutes before the event (0 = none). |

Say:

- “Schedule a meeting with the team tomorrow at 3pm for an hour”

### `create_reminder` L1

Create a reminder that JARVIS will announce at a specific time.

`create_reminder(**task**, when?, recurrence?, channel?, notes?)`

| argument | type | notes |
|---|---|---|
| `task` | string | **required** — What to be reminded about, e.g. "submit my assignment". |
| `when` | string | default `""` — ISO datetime ("2026-10-01T09:00") or natural language ("tomorrow at 9am", "in 30 minutes"). |
| `recurrence` | `` \| `daily` \| `weekdays` \| `weekly` \| `weekend` | default `""` — "" for one-off, or daily / weekdays / weekly / weekend. |
| `channel` | `all` \| `notification` \| `voice` \| `dashboard` | default `"all"` — How to deliver it: notification, voice, dashboard, or all. |
| `notes` | string | default `""` — Optional extra detail stored with the reminder. |

Say:

- “Remind me tomorrow at 9 AM to submit my assignment”

### `create_task` L1

Add a to-do item to the task list.

`create_task(**title**, project?, priority?, due?, notes?)`

| argument | type | notes |
|---|---|---|
| `title` | string | **required** — The task, e.g. "finish DBMS notes". |
| `project` | string | default `""` — Optional project name to group it under. |
| `priority` | `1` \| `2` \| `3` \| `4` \| `5` | default `3` — 1 (highest) to 5 (lowest); default 3. |
| `due` | string | default `""` — Optional due date/time, ISO or natural language ("friday", "tomorrow 5pm"). |
| `notes` | string | default `""` — Optional detail. |

Say:

- “Add a task to finish the DBMS notes”

### `delete_task` L1

Remove a task from the list entirely.

`delete_task(**task_id**)`

| argument | type | notes |
|---|---|---|
| `task_id` | integer | **required** — Numeric task id to delete. |

### `draft_email` L2

Write an email draft to disk without sending it (no confirmation needed to write).

`draft_email(**to**, **subject**, body?, cc?)`

| argument | type | notes |
|---|---|---|
| `to` | string | **required** — Recipient address. |
| `subject` | string | **required** — Subject line. |
| `body` | string | default `""` — Message body. |
| `cc` | string | default `""` — Optional CC address. |

### `find_free_slot` L1

Suggest the next free slot in the calendar for a given duration.

`find_free_slot(duration_minutes?, prefer?)`

| argument | type | notes |
|---|---|---|
| `duration_minutes` | integer | default `60` — How long the meeting needs. |
| `prefer` | `morning` \| `afternoon` | default `"morning"` — "morning" or "afternoon". |

### `get_agenda` L1

Build the daily briefing: reminders, calendar events and open tasks.

`get_agenda(window?)`

| argument | type | notes |
|---|---|---|
| `window` | `today` \| `tomorrow` \| `week` | default `"today"` — "today", "tomorrow" or "week". |

### `list_calendar_events` L1

List calendar events.

`list_calendar_events(window?, limit?)`

| argument | type | notes |
|---|---|---|
| `window` | `today` \| `tomorrow` \| `week` \| `month` \| `all` | default `"today"` — today, tomorrow, week, month or all. |
| `limit` | integer | default `25` — Maximum number of events to return. |

### `list_drafts` L1

List the email drafts JARVIS has written but not sent.

`list_drafts(limit?)`

| argument | type | notes |
|---|---|---|
| `limit` | integer | default `10` — Maximum drafts to return. |

### `list_reminders` L1

List reminders.

`list_reminders(status?, limit?, window?)`

| argument | type | notes |
|---|---|---|
| `status` | `pending` \| `fired` \| `cancelled` \| `all` | default `"pending"` — Which reminders: pending, fired, cancelled or all. |
| `limit` | integer | default `20` — Maximum number to return. |
| `window` | `` \| `today` \| `week` \| `month` | default `""` — Optional filter: today, week or month. |

Say:

- “What are my reminders?”

### `list_tasks` L1

List to-do items.

`list_tasks(status?, project?, limit?)`

| argument | type | notes |
|---|---|---|
| `status` | `todo` \| `doing` \| `done` \| `all` | default `"todo"` — todo, doing, done or all. |
| `project` | string | default `""` — Optional project filter. |
| `limit` | integer | default `25` — Maximum number of tasks. |

### `post_webhook` L2

POST a JSON message to your configured webhook (Slack/Discord/ntfy/IFTTT).

`post_webhook(**message**, event?)`

| argument | type | notes |
|---|---|---|
| `message` | string | **required** — Text payload to send. |
| `event` | string | default `"jarvis"` — Event name included in the JSON body. |

### `send_email` L2

Send an email over SMTP. Always asks for confirmation first.

`send_email(**to**, **subject**, body?, cc?)`

| argument | type | notes |
|---|---|---|
| `to` | string | **required** — Recipient address. |
| `subject` | string | **required** — Subject line. |
| `body` | string | default `""` — Message body. |
| `cc` | string | default `""` — Optional CC address. |

### `snooze_reminder` L1

Postpone a reminder by a number of minutes.

`snooze_reminder(minutes?, needle?, reminder_id?)`

| argument | type | notes |
|---|---|---|
| `minutes` | integer | default `10` — How many minutes to postpone (default 10). |
| `needle` | string | default `""` — Text to match, e.g. "assignment". |
| `reminder_id` | integer \| null | Numeric reminder id. |

---

## browser · 10 tools

### `browser_click` L1

Click an element in the automation browser, by CSS selector or visible text.

`browser_click(selector?, text?)`

| argument | type | notes |
|---|---|---|
| `selector` | string | default `""` — CSS selector to click. |
| `text` | string | default `""` — Visible text to click (used when selector is empty), e.g. "Sign in". |

### `browser_close` L1

Close JARVIS's automation browser session and free its memory.

`browser_close()`

### `browser_extract` L1

Extract text, links or an attribute from the current automation page.

`browser_extract(selector?, attribute?, max_items?)`

| argument | type | notes |
|---|---|---|
| `selector` | string | default `"body"` — CSS selector to read (default "body" = the whole page text). |
| `attribute` | string | default `"text"` — "text" for inner text, "href"/"src"/any attribute name to read that attribute. |
| `max_items` | integer | default `20` — Maximum number of matching elements to return. |

### `browser_navigate` L1

Navigate JARVIS's own automation browser to a URL (headless by default).

`browser_navigate(**url**, wait_seconds?)`

| argument | type | notes |
|---|---|---|
| `url` | string | **required** — Destination URL. |
| `wait_seconds` | number | default `1.5` — How long to wait for the page to settle. |

### `browser_screenshot` L1

Screenshot the automation browser page (not the user's desktop).

`browser_screenshot(full_page?, note?)`

| argument | type | notes |
|---|---|---|
| `full_page` | boolean | default `false` — Capture the entire scrollable page instead of the viewport. |
| `note` | string | default `""` — Optional label stored with the artifact. |

### `browser_status` L1

Report the state of JARVIS's automation browser.

`browser_status()`

### `browser_type` L1

Type into a form field in the automation browser.

`browser_type(**selector**, **text**, submit?)`

| argument | type | notes |
|---|---|---|
| `selector` | string | **required** — CSS selector of the input field. |
| `text` | string | **required** — Text to type. |
| `submit` | boolean | default `false` — Press Enter after typing (submits the form). |

### `open_in_default_browser` L1

Open a URL with the OS default handler (works for mailto:, files, apps).

`open_in_default_browser(**url**)`

| argument | type | notes |
|---|---|---|
| `url` | string | **required** — Any URL or URI scheme the OS understands. |

### `open_url` L1

Open a URL in the user's real browser.

`open_url(**url**, browser?)`

| argument | type | notes |
|---|---|---|
| `url` | string | **required** — URL or domain to open ("github.com" becomes "https://github.com"). |
| `browser` | string | default `""` — Optional browser to use ("chrome", "firefox", "edge"). Defaults to the system browser. |

Say:

- “Open github.com”
- “Jarvis, open localhost:3000”

### `search_and_open` L1

Search the web and immediately open the best result in the browser.

`search_and_open(**query**)`

| argument | type | notes |
|---|---|---|
| `query` | string | **required** — What to look for. |

---

## system · 9 tools

### `environment_info` L1

Report JARVIS's own runtime: platform, backend, model, database and tool count.

`environment_info()`

### `get_date` L1

Report today's date and weekday.

`get_date()`

### `get_system_status` L1

Report CPU, memory, disk, battery, screen, volume and running apps.

`get_system_status()`

Say:

- “What's my system status?”
- “How's my battery?”

### `get_time` L1

Report the current local time.

`get_time()`

Say:

- “What time is it?”

### `lock_screen` L1

Lock the screen immediately (no confirmation needed — it is reversible).

`lock_screen()`

### `notify_user` L1

Show a desktop notification (and optionally speak it).

`notify_user(**title**, **body**, speak?)`

| argument | type | notes |
|---|---|---|
| `title` | string | **required** — Notification title. |
| `body` | string | **required** — Notification text. |
| `speak` | boolean | default `false` — Also say the message out loud. |

### `power` L2

Shut down, restart, sleep or lock the computer.

`power(action?, delay_seconds?)`

| argument | type | notes |
|---|---|---|
| `action` | `shutdown` \| `restart` \| `sleep` \| `hibernate` \| `lock` \| `logout` \| `cancel_shutdown` | default `"shutdown"` — One of shutdown, restart, sleep, hibernate, lock, logout, cancel_shutdown. |
| `delay_seconds` | integer | default `5` — Grace period before a shutdown/restart happens (cancel with cancel_shutdown). |

Say:

- “Jarvis, shut down.”
- “Lock my screen”

### `run_command` L1

Run an allow-listed shell command (read-only by default). Destructive commands need confirmation.

`run_command(**command**, cwd?, timeout?)`

| argument | type | notes |
|---|---|---|
| `command` | string | **required** — The command to run, e.g. "git status" or "python --version". |
| `cwd` | string | default `""` — Working directory (defaults to the JARVIS workspace). |
| `timeout` | integer | default `30` — Maximum seconds to wait (1-600). |

### `run_project` L1

Run a known project task (npm start / pytest / build) inside a project folder.

`run_project(**project**, command?, cwd?)`

| argument | type | notes |
|---|---|---|
| `project` | string | **required** — Project folder name, e.g. "smart-water-grid". |
| `command` | `start` \| `install` \| `test` \| `build` | default `"start"` — What to run: start, install, test or build. |
| `cwd` | string | default `""` — Optional explicit project path (overrides `project`). |

---

## files · 8 tools

### `create_file` L1

Create (or append to) a text file.

`create_file(**path**, content?, append?)`

| argument | type | notes |
|---|---|---|
| `path` | string | **required** — Where to write. Relative paths land in the JARVIS workspace. |
| `content` | string | default `""` — Text to write. |
| `append` | boolean | default `false` — Append instead of overwriting. |

### `delete_file` L2

Delete a file. By default it is moved to the JARVIS trash folder first.

`delete_file(**path**, permanent?)`

| argument | type | notes |
|---|---|---|
| `path` | string | **required** — File or folder to delete. |
| `permanent` | boolean | default `false` — Skip the trash folder and delete for real. |

### `file_info` L1

Report size, type and timestamps for a file or folder.

`file_info(**path**)`

| argument | type | notes |
|---|---|---|
| `path` | string | **required** — File or folder to inspect. |

### `find_file` L1

Find files by (partial) name across the usual folders.

`find_file(**query**, extension?, limit?, search_root?)`

| argument | type | notes |
|---|---|---|
| `query` | string | **required** — Part of the file name, e.g. "resume", "invoice 2026". |
| `extension` | string | default `""` — Optional extension filter without the dot, e.g. "pdf". |
| `limit` | integer | default `15` — Maximum number of matches to return. |
| `search_root` | string | default `""` — Optional folder to search instead of the defaults. |

Say:

- “Find my resume pdf”

### `list_directory` L1

List the contents of a folder.

`list_directory(path?, limit?, show_hidden?)`

| argument | type | notes |
|---|---|---|
| `path` | string | default `""` — Folder to list. Empty = the JARVIS workspace root. |
| `limit` | integer | default `60` — Maximum entries to return. |
| `show_hidden` | boolean | default `false` — Include dot-files. |

### `move_file` L2

Move or rename a file or folder.

`move_file(**source**, **destination**)`

| argument | type | notes |
|---|---|---|
| `source` | string | **required** — Existing path. |
| `destination` | string | **required** — New path (or a folder to move into). |

### `open_file` L1

Open a file or folder with the operating system's default application.

`open_file(**path**)`

| argument | type | notes |
|---|---|---|
| `path` | string | **required** — Absolute path, "~/..." path, or a file name that will be searched for. |

### `read_file` L1

Read a text file's contents.

`read_file(**path**, max_chars?, line_start?)`

| argument | type | notes |
|---|---|---|
| `path` | string | **required** — File to read. |
| `max_chars` | integer | default `6000` — Maximum characters to return. |
| `line_start` | integer | default `1` — First line number to return (1-based). |

---

## memory · 7 tools

### `forget_fact` L2

Delete memories matching a query.

`forget_fact(**query**, category?)`

| argument | type | notes |
|---|---|---|
| `query` | string | **required** — What to forget, e.g. "phone number". |
| `category` | string | default `""` — Optional category filter. |

### `memory_stats` L1

Report how much JARVIS remembers and how memory is configured.

`memory_stats()`

### `recall_fact` L1

Look something up in long-term memory.

`recall_fact(query?, limit?, category?)`

| argument | type | notes |
|---|---|---|
| `query` | string | default `""` — What to recall, e.g. "my projects" or "preferred language". |
| `limit` | integer | default `5` — Maximum number of memories to return. |
| `category` | string | default `""` — Optional category filter (profile, preference, project, goal, general). |

### `remember_fact` L1

Store something in long-term memory so JARVIS remembers it later.

`remember_fact(key?, value?, note?, category?)`

| argument | type | notes |
|---|---|---|
| `key` | string | default `""` — Short label, e.g. "preferred_ide" or "project". |
| `value` | string | default `""` — The fact itself, e.g. "VS Code". |
| `note` | string | default `""` — Free-form sentence when there is no obvious key/value split. |
| `category` | `profile` \| `preference` \| `project` \| `goal` \| `workflow` \| `general` | default `"general"` — profile, preference, project, goal, workflow or general. |

Say:

- “Remember that my favourite IDE is VS Code”

### `search_memory` L1

Full-text search across every stored memory.

`search_memory(**query**, limit?)`

| argument | type | notes |
|---|---|---|
| `query` | string | **required** — Text to search for. |
| `limit` | integer | default `10` — Maximum results. |

### `set_user_name` L1

Set the name JARVIS calls the user.

`set_user_name(**name**)`

| argument | type | notes |
|---|---|---|
| `name` | string | **required** — The user's name. |

### `show_profile` L1

Show everything JARVIS currently knows about the user.

`show_profile()`

---

## meta · 6 tools

### `jarvis_status` L1

Report JARVIS's configuration: model, backend, safety mode and tool count.

`jarvis_status()`

### `list_tools` L1

List the tools JARVIS can use, grouped by category.

`list_tools(category?)`

| argument | type | notes |
|---|---|---|
| `category` | string | default `""` — Optional filter: computer, browser, web, files, system, productivity, memory, vision, meta. |

Say:

- “What can you do?”
- “Jarvis, help”

### `list_workflows` L1

List the built-in multi-step routines JARVIS can run.

`list_workflows()`

### `make_plan` L1

Break a goal into steps and (optionally) execute them in order.

`make_plan(**goal**, execute?)`

| argument | type | notes |
|---|---|---|
| `goal` | string | **required** — What you want achieved, e.g. "prepare my workspace for coding". |
| `execute` | boolean | default `true` — Run the plan immediately (false = just show it). |

Say:

- “Plan how to set up my environment for today”

### `run_workflow` L1

Run a built-in multi-step routine.

`run_workflow(**name**, variables?, execute?)`

| argument | type | notes |
|---|---|---|
| `name` | string | **required** — Workflow name, e.g. "coding_setup", "morning_routine", "study_setup", "meeting_prep", "wrap_up". |
| `variables` | object | Optional overrides, e.g. {"project": "smart-water-grid"}. |
| `execute` | boolean | default `true` — Run the steps now (false = just return the plan). |

Say:

- “Prepare my workspace for coding”

### `show_audit_trail` L1

Show what JARVIS has recently done, including anything the safety layer blocked.

`show_audit_trail(limit?)`

| argument | type | notes |
|---|---|---|
| `limit` | integer | default `15` — How many entries to return. |

---

## vision · 4 tools

### `click_element` L1

Find a UI element on screen by its text and click it.

`click_element(**label**, button?, double?)`

| argument | type | notes |
|---|---|---|
| `label` | string | **required** — Visible text of the element, e.g. "Login" or "Save changes". |
| `button` | string | default `"left"` — "left" or "right". |
| `double` | boolean | default `false` — Double-click instead of a single click. |

Say:

- “Jarvis, click the login button.”

### `describe_screen` L1

Look at the screen and describe what is on it.

`describe_screen(focus?)`

| argument | type | notes |
|---|---|---|
| `focus` | string | default `""` — Optional question about the screen, e.g. "is the build failing?". |

Say:

- “What's on my screen?”
- “Describe the screen”

### `find_on_screen` L1

Locate a UI element (button, link, field, icon) on screen and return its coordinates.

`find_on_screen(**label**, take_screenshot?)`

| argument | type | notes |
|---|---|---|
| `label` | string | **required** — Visible text or description, e.g. "Login", "Submit", "search box". |
| `take_screenshot` | boolean | default `true` — Capture a fresh screenshot first (set false to reuse the last one). |

### `read_screen_text` L1

Read all visible text on the screen (OCR). Useful for forms and error messages.

`read_screen_text(max_chars?)`

| argument | type | notes |
|---|---|---|
| `max_chars` | integer | default `3000` — Maximum characters to return. |

---

## web · 4 tools

### `check_site` L1

Check whether a website or local dev server is reachable, and how fast.

`check_site(**url**)`

| argument | type | notes |
|---|---|---|
| `url` | string | **required** — URL to probe (e.g. http://localhost:3000). |

### `download_file` L2

Download a file from a URL into the JARVIS downloads folder.

`download_file(**url**, destination?)`

| argument | type | notes |
|---|---|---|
| `url` | string | **required** — The file URL to download. |
| `destination` | string | default `""` — Optional file name or path. Defaults to the URL's file name. |

### `read_page` L1

Fetch a web page and return its readable text content.

`read_page(url?, max_chars?)`

| argument | type | notes |
|---|---|---|
| `url` | string | default `""` — The page URL. Leave empty to re-read the last opened URL. |
| `max_chars` | integer | default `4000` — Maximum characters of text to return (default 4000). |

### `search_web` L1

Search the web and return the top results.

`search_web(**query**, max_results?, open_top?, answer?)`

| argument | type | notes |
|---|---|---|
| `query` | string | **required** — What to search for. |
| `max_results` | integer | default `5` — How many results to return (1-10). |
| `open_top` | boolean | default `false` — Also open the best result in the browser. |
| `answer` | boolean | default `false` — Include a short direct answer extracted from the results. |

Say:

- “Search the web for FastAPI tutorials”
- “Jarvis, what is Python?”

---

## Adding a tool

```python
# jarvis/tools/computer.py
from ..core.types import Permission, ToolCategory, ToolResult
from .registry import ToolContext, tool


@tool(permission=Permission.SAFE, category=ToolCategory.COMPUTER, examples=["Pin the window"])
def pin_window(ctx: ToolContext, title: str = "") -> ToolResult:
    """Pin the focused window so it stays on top.

    Args:
        title: Optional window title to pin (defaults to the focused window).
    """
    message = ctx.backend.pin_window(title)          # the backend does the work
    return ToolResult(ok=True, message=message, speech="Pinned.",
                      data={"title": title})
```

Then add it to the module's loader list in `jarvis/core/agent.py::_load_tools`.
That's the whole integration: the schema, the prompt catalogue, the offline
intent rules, the safety gate, the audit log, the dashboard's Tools tab and the
REST endpoint all pick it up automatically.

Rules worth following:

1. **Prefer a specific tool over a general one.** `close_current_tab()` beats
   `run_command("xdotool key ctrl+w")`.
2. **Never shell out to something destructive.** The gate will block it anyway,
   and the audit log will show why.
3. **Return a `ToolResult`, never raise.** The registry catches exceptions and
   turns them into failed observations the model can react to.
4. **Say something speakable.** `speech` is what the user hears; `message` is
   what the console shows.
5. **Declare the honest permission level.** If it leaves the machine or can't be
   undone, that's `Permission.CONFIRM`.
