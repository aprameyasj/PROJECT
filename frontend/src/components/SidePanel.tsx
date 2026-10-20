import { useState } from "react";
import { api } from "../lib/api";
import type { JarvisState } from "../hooks/useJarvis";
import type { Fact, Reminder, ToolSpec } from "../types";

type TabId = "reminders" | "calendar" | "tasks" | "memory" | "workflows" | "tools" | "screens" | "audit";

const TABS: Array<{ id: TabId; label: string }> = [
  { id: "reminders", label: "Reminders" },
  { id: "calendar", label: "Calendar" },
  { id: "tasks", label: "Tasks" },
  { id: "memory", label: "Memory" },
  { id: "workflows", label: "Routines" },
  { id: "tools", label: "Tools" },
  { id: "screens", label: "Screens" },
  { id: "audit", label: "Audit" },
];

interface Props {
  state: JarvisState;
  onChanged: () => void;
  onNotice: (message: string, level?: "info" | "warn" | "error") => void;
}

const when = (iso: string): string => {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
};

/* ------------------------------ reminders ------------------------------ */

function Reminders({ state, onChanged, onNotice }: Props) {
  const [task, setTask] = useState("");
  const [whenText, setWhenText] = useState("tomorrow at 9am");

  const add = async () => {
    if (!task.trim()) return;
    try {
      await api.addReminder(task.trim(), whenText.trim());
      setTask("");
      onChanged();
      onNotice("Reminder added.");
    } catch (error) {
      onNotice((error as Error).message, "error");
    }
  };

  return (
    <div className="list">
      <div className="field">
        <input
          value={task}
          placeholder="Remind me to…"
          onChange={(event) => setTask(event.target.value)}
          onKeyDown={(event) => event.key === "Enter" && void add()}
        />
        <input
          value={whenText}
          placeholder="tomorrow at 9am"
          onChange={(event) => setWhenText(event.target.value)}
          style={{ flex: "0 1 150px" }}
        />
        <button className="btn small primary" onClick={() => void add()}>
          Add
        </button>
      </div>

      {state.reminders.length === 0 ? <div className="empty">No pending reminders.</div> : null}

      {state.reminders.map((reminder: Reminder) => (
        <div className="card" key={reminder.id}>
          <div className="row">
            <span className="title">{reminder.task}</span>
            <span className="sub">#{reminder.id}</span>
          </div>
          <div className="sub">
            {when(reminder.remind_at)}
            {reminder.recurrence ? ` · ${reminder.recurrence}` : ""} · {reminder.status}
          </div>
          <div className="actions">
            <button className="btn small" onClick={() => void api.snoozeReminder(reminder.id, 10).then(onChanged)}>
              Snooze 10m
            </button>
            <button className="btn small" onClick={() => void api.completeReminder(reminder.id).then(onChanged)}>
              Done
            </button>
            <button
              className="btn small danger"
              onClick={() => void api.deleteReminder(reminder.id).then(onChanged)}
            >
              Delete
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}

/* ------------------------------- calendar ------------------------------ */

function Calendar({ state, onChanged, onNotice }: Props) {
  const [title, setTitle] = useState("");
  const [starts, setStarts] = useState("");

  const add = async () => {
    if (!title.trim() || !starts.trim()) return;
    try {
      await api.addEvent({ title: title.trim(), starts_at: starts.trim(), remind_minutes_before: 15 });
      setTitle("");
      setStarts("");
      onChanged();
      onNotice("Event added to the calendar.");
    } catch (error) {
      onNotice((error as Error).message, "error");
    }
  };

  return (
    <div className="list">
      <div className="field">
        <input value={title} placeholder="Meeting with…" onChange={(event) => setTitle(event.target.value)} />
        <input
          value={starts}
          placeholder="tomorrow at 3pm  (or 2026-10-01T15:00)"
          onChange={(event) => setStarts(event.target.value)}
        />
        <button className="btn small primary" onClick={() => void add()}>
          Add
        </button>
      </div>
      {state.events.length === 0 ? <div className="empty">Nothing scheduled this week.</div> : null}
      {state.events.map((event) => (
        <div className="card" key={event.id}>
          <div className="row">
            <span className="title">{event.title}</span>
            <span className="sub">#{event.id}</span>
          </div>
          <div className="sub">
            {when(event.starts_at)}
            {event.ends_at ? ` → ${new Date(event.ends_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}` : ""}
            {event.location ? ` · ${event.location}` : ""}
          </div>
          <div className="actions">
            <button className="btn small danger" onClick={() => void api.cancelEvent(event.id).then(onChanged)}>
              Cancel
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}

/* -------------------------------- tasks -------------------------------- */

function Tasks({ state, onChanged }: Props) {
  const [title, setTitle] = useState("");
  const [project, setProject] = useState("");

  const add = async () => {
    if (!title.trim()) return;
    await api.addTask(title.trim(), project.trim());
    setTitle("");
    onChanged();
  };

  return (
    <div className="list">
      <div className="field">
        <input
          value={title}
          placeholder="Add a task…"
          onChange={(event) => setTitle(event.target.value)}
          onKeyDown={(event) => event.key === "Enter" && void add()}
        />
        <input
          value={project}
          placeholder="project"
          style={{ flex: "0 1 110px" }}
          onChange={(event) => setProject(event.target.value)}
        />
        <button className="btn small primary" onClick={() => void add()}>
          Add
        </button>
      </div>
      {state.tasks.length === 0 ? <div className="empty">Task list is clear.</div> : null}
      {state.tasks.map((task) => (
        <div className="card" key={task.id}>
          <div className="row">
            <span className="title">{task.title}</span>
            <span className="sub">
              P{task.priority}
              {task.project ? ` · ${task.project}` : ""}
            </span>
          </div>
          <div className="actions">
            <button
              className="btn small"
              onClick={() => void api.setTaskStatus(task.id, "doing").then(onChanged)}
            >
              Start
            </button>
            <button
              className="btn small primary"
              onClick={() => void api.setTaskStatus(task.id, "done").then(onChanged)}
            >
              Complete
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}

/* -------------------------------- memory ------------------------------- */

function MemoryPanel({ state, onChanged, onNotice }: Props) {
  const [key, setKey] = useState("");
  const [value, setValue] = useState("");
  const [category, setCategory] = useState("general");

  const save = async () => {
    if (!key.trim() || !value.trim()) return;
    try {
      await api.remember(key.trim(), value.trim(), category);
      setKey("");
      setValue("");
      onChanged();
      onNotice("Stored in long-term memory.");
    } catch (error) {
      onNotice((error as Error).message, "error");
    }
  };

  const categories = Object.keys(state.facts).sort();

  return (
    <div className="list">
      <div className="field">
        <input value={key} placeholder="key (e.g. preferred_ide)" onChange={(event) => setKey(event.target.value)} />
        <input value={value} placeholder="value (e.g. VS Code)" onChange={(event) => setValue(event.target.value)} />
        <select value={category} onChange={(event) => setCategory(event.target.value)} style={{ flex: "0 1 120px" }}>
          {["profile", "preference", "project", "goal", "workflow", "general"].map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
        <button className="btn small primary" onClick={() => void save()}>
          Remember
        </button>
      </div>

      {Object.keys(state.profile).length ? (
        <>
          <div className="group-title">Profile</div>
          <dl className="kv">
            {Object.entries(state.profile).map(([name, val]) => (
              <div key={name} style={{ display: "contents" }}>
                <dt>{name}</dt>
                <dd>{val || <span style={{ color: "var(--dim)" }}>—</span>}</dd>
              </div>
            ))}
          </dl>
        </>
      ) : null}

      {categories.length === 0 ? <div className="empty">No long-term memories yet.</div> : null}
      {categories.map((name) => (
        <div key={name}>
          <div className="group-title">{name}</div>
          {state.facts[name].filter((fact: Fact) => fact.value).map((fact: Fact) => (
            <div className="card" key={`${fact.category}-${fact.key}`}>
              <div className="row">
                <span className="title">{fact.key}</span>
                <button
                  className="btn small ghost"
                  onClick={() => void api.forget(fact.key, fact.category).then(onChanged)}
                  title="Forget this"
                >
                  forget
                </button>
              </div>
              <div className="sub">{fact.value}</div>
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

/* ------------------------------- workflows ----------------------------- */

function Workflows({ state, onNotice }: Props) {
  const run = async (name: string) => {
    try {
      await api.runWorkflow(name);
      onNotice(`Running routine “${name}”…`);
    } catch (error) {
      onNotice((error as Error).message, "error");
    }
  };

  return (
    <div className="list">
      {state.workflows.map((workflow) => (
        <div className="card" key={workflow.name}>
          <div className="row">
            <span className="title">{workflow.title}</span>
            <button className="btn small primary" onClick={() => void run(workflow.name)}>
              Run
            </button>
          </div>
          <div className="sub">{workflow.description}</div>
          <div className="sub" style={{ color: "var(--dim)" }}>
            {workflow.steps.map((step) => step.tool).join(" → ")}
          </div>
        </div>
      ))}
      <p className="footer-note">
        Routines are data, not code: add one in <code>jarvis/automation/workflows.py</code> and it appears here.
      </p>
    </div>
  );
}

/* --------------------------------- tools ------------------------------- */

function Tools({ state }: Props) {
  const [filter, setFilter] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);
  const categories = Object.keys(state.tools).sort();
  const needle = filter.trim().toLowerCase();

  const matches = (spec: ToolSpec): boolean =>
    !needle || spec.name.includes(needle) || spec.description.toLowerCase().includes(needle);

  return (
    <div>
      <div className="field">
        <input value={filter} placeholder={`Filter ${state.toolCount} tools…`} onChange={(e) => setFilter(e.target.value)} />
      </div>
      {categories.map((category) => {
        const specs = state.tools[category].filter(matches);
        if (!specs.length) return null;
        return (
          <div key={category}>
            <div className="group-title">
              {category} · {specs.length}
            </div>
            <div className="tool-grid">
              {specs.map((spec) => (
                <div className="tool-row" key={spec.name}>
                  <header>
                    <span className={`perm ${spec.permission}`}>{spec.permission}</span>
                    <code>{spec.name}</code>
                    {!spec.enabled ? <span className="perm">disabled</span> : null}
                    <button
                      className="btn small ghost"
                      style={{ marginLeft: "auto" }}
                      onClick={() => setExpanded(expanded === spec.name ? null : spec.name)}
                    >
                      {expanded === spec.name ? "hide" : "schema"}
                    </button>
                  </header>
                  <p>{spec.description.split("\n")[0]}</p>
                  {expanded === spec.name ? (
                    <pre style={{ margin: "6px 0 0", fontSize: 11, color: "var(--muted)", overflow: "auto" }}>
                      {JSON.stringify(spec.parameters, null, 2)}
                    </pre>
                  ) : null}
                </div>
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}

/* ------------------------------- screenshots --------------------------- */

function Screens({ state, onNotice, onChanged }: Props) {
  const capture = async () => {
    try {
      await api.runTool("take_screenshot", { note: "from dashboard" });
      onNotice("Screenshot captured.");
      onChanged();
    } catch (error) {
      onNotice((error as Error).message, "error");
    }
  };

  return (
    <div>
      {state.screenshots.length === 0 ? (
        <div className="empty">No screenshots yet — say “take a screenshot”.</div>
      ) : (
        <div className="shots">
          {state.screenshots.map((shot) => {
            const name = shot.path.split("/").pop() ?? "";
            return (
              <figure key={shot.id}>
                {shot.exists ? (
                  <img src={api.screenshotUrl(name)} alt={name} loading="lazy" />
                ) : (
                  <div className="empty" style={{ padding: 24 }}>
                    missing
                  </div>
                )}
                <figcaption>
                  {name}
                  {shot.meta?.simulated ? " · sim" : ""}
                </figcaption>
              </figure>
            );
          })}
        </div>
      )}
      <button className="btn small" style={{ marginTop: 10 }} onClick={() => void capture()}>
        Capture screen now
      </button>
    </div>
  );
}

/* --------------------------------- audit ------------------------------- */

function Audit({ state }: Props) {
  const stats = state.status?.agent?.database;
  return (
    <div>
      {stats ? (
        <dl className="kv" style={{ marginBottom: 10 }}>
          <dt>tool calls</dt>
          <dd>{stats.tool_calls}</dd>
          <dt>confirmations</dt>
          <dd>{stats.confirmations}</dd>
          <dt>blocked</dt>
          <dd style={{ color: stats.blocked ? "var(--red)" : undefined }}>{stats.blocked}</dd>
        </dl>
      ) : null}
      <div className="list">
        {state.audit.length === 0 ? <div className="empty">No actions recorded yet.</div> : null}
        {state.audit.map((entry) => (
          <div className="card" key={entry.id}>
            <div className="audit-row">
              <span className={`decision ${entry.decision}`}>{entry.decision}</span>
              <span style={{ color: "var(--cyan)" }}>{entry.tool}</span>
              <span style={{ color: "var(--dim)" }}>{new Date(entry.ts * 1000).toLocaleTimeString()}</span>
            </div>
            {entry.reason ? <div className="sub">{entry.reason}</div> : null}
            {entry.message ? <div className="sub">{entry.message.slice(0, 140)}</div> : null}
          </div>
        ))}
      </div>
    </div>
  );
}

/* --------------------------------- shell ------------------------------- */

export default function SidePanel(props: Props) {
  const [tab, setTab] = useState<TabId>("reminders");

  return (
    <section className="panel" style={{ flex: "1 1 auto", minHeight: 320 }}>
      <div className="tabs">
        {TABS.map((entry) => (
          <button key={entry.id} className={tab === entry.id ? "active" : ""} onClick={() => setTab(entry.id)}>
            {entry.label}
          </button>
        ))}
      </div>
      <div className="side-body">
        {tab === "reminders" ? <Reminders {...props} /> : null}
        {tab === "calendar" ? <Calendar {...props} /> : null}
        {tab === "tasks" ? <Tasks {...props} /> : null}
        {tab === "memory" ? <MemoryPanel {...props} /> : null}
        {tab === "workflows" ? <Workflows {...props} /> : null}
        {tab === "tools" ? <Tools {...props} /> : null}
        {tab === "screens" ? <Screens {...props} /> : null}
        {tab === "audit" ? <Audit {...props} /> : null}
      </div>
    </section>
  );
}
