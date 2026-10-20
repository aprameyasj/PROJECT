import { useCallback, useState } from "react";
import Composer from "./components/Composer";
import Console from "./components/Console";
import Header from "./components/Header";
import SidePanel from "./components/SidePanel";
import { useJarvis } from "./hooks/useJarvis";

interface Toast {
  id: number;
  message: string;
  level: "info" | "warn" | "error";
}

export default function App() {
  const { state, actions } = useJarvis();
  const [toasts, setToasts] = useState<Toast[]>([]);

  const notice = useCallback((message: string, level: "info" | "warn" | "error" = "info") => {
    const id = Date.now() + Math.random();
    setToasts((current) => [...current.slice(-3), { id, message, level }]);
    window.setTimeout(() => setToasts((current) => current.filter((toast) => toast.id !== id)), 4200);
  }, []);

  const backend = state.status?.agent?.backend;
  const settings = state.status?.agent?.settings;
  const nextReminder = state.reminders[0];

  return (
    <div className="app">
      <Header
        status={state.status}
        connected={state.connected}
        busy={state.busy}
        speechEnabled={state.speechEnabled}
        listening={state.listening}
        toolCount={state.toolCount}
        onToggleSpeech={actions.toggleSpeech}
        onRefresh={() => void actions.refreshPanels()}
      />

      <div className="workspace">
        {/* ---------------- left: conversation + live tool trace ---------------- */}
        <div className="column">
          <section className="panel console">
            <div className="panel-head">
              <span>Conversation · live execution trace</span>
              <span style={{ display: "flex", gap: 6 }}>
                <button className="btn small ghost" onClick={() => actions.send("give me my agenda for today")}>
                  briefing
                </button>
                <button className="btn small ghost" onClick={actions.clearConsole}>
                  clear
                </button>
                <button className="btn small ghost" onClick={() => void actions.resetSession()}>
                  new session
                </button>
              </span>
            </div>

            {state.error ? (
              <div style={{ padding: "10px 14px 0" }}>
                <div className="banner">{state.error}</div>
              </div>
            ) : null}

            {backend?.simulated ? (
              <div style={{ padding: "10px 14px 0" }}>
                <div className="banner" style={{ borderColor: "rgba(251,191,36,.4)", background: "rgba(45,35,12,.55)", color: "#ffeab5" }}>
                  Simulation mode — {String(backend.reason ?? "no GUI session detected")}. Actions are recorded and
                  reported exactly as they would be, but nothing is executed on a real desktop. Set{" "}
                  <code>JARVIS_BACKEND=desktop</code> on your own machine to make them real.
                </div>
              </div>
            ) : null}

            <Console
              items={state.items}
              busy={state.busy}
              pending={state.pending}
              onConfirm={(token, approved) => void actions.confirm(token, approved)}
            />

            <Composer
              onSend={actions.send}
              onToggleMic={actions.toggleMic}
              listening={state.listening}
              interim={state.interim}
              micAvailable={state.micAvailable}
              busy={state.busy}
            />
          </section>
        </div>

        {/* ---------------- right: state of the agent ---------------- */}
        <div className="column">
          <section className="panel">
            <div className="panel-head">
              <span>Agent state</span>
              <span className="meta">{settings?.platform ?? "…"}</span>
            </div>
            <div className="panel-body">
              <dl className="kv">
                <dt>next reminder</dt>
                <dd>
                  {nextReminder
                    ? `${nextReminder.task} · ${new Date(nextReminder.remind_at).toLocaleString([], {
                        month: "short",
                        day: "numeric",
                        hour: "2-digit",
                        minute: "2-digit",
                      })}`
                    : "none"}
                </dd>
                <dt>open tasks</dt>
                <dd>{state.tasks.length}</dd>
                <dt>events (week)</dt>
                <dd>{state.events.length}</dd>
                <dt>memories</dt>
                <dd>{Object.values(state.facts).reduce((total, list) => total + list.filter((f) => f.value).length, 0)}</dd>
                <dt>actions logged</dt>
                <dd>{state.status?.agent?.database?.tool_calls ?? 0}</dd>
                <dt>blocked</dt>
                <dd style={{ color: state.status?.agent?.database?.blocked ? "var(--red)" : undefined }}>
                  {state.status?.agent?.database?.blocked ?? 0}
                </dd>
                <dt>scheduler</dt>
                <dd>
                  {state.status?.scheduler?.engine ?? "—"} · fired {state.status?.scheduler?.fired_total ?? 0}
                </dd>
                <dt>wake words</dt>
                <dd>{settings?.wake_words?.join(", ") ?? "jarvis"}</dd>
                <dt>data dir</dt>
                <dd style={{ fontSize: 11, color: "var(--muted)" }}>{settings?.home ?? "…"}</dd>
              </dl>
              <p className="footer-note">
                The dashboard is a client — the Python agent owns the brain, the tools and the safety gate.
              </p>
            </div>
          </section>

          <SidePanel state={state} onChanged={() => void actions.refreshPanels()} onNotice={notice} />
        </div>
      </div>

      {/* ---------------- toasts ---------------- */}
      <div style={{ position: "fixed", right: 18, bottom: 18, display: "flex", flexDirection: "column", gap: 8, zIndex: 50 }}>
        {toasts.map((toast) => (
          <div
            key={toast.id}
            className="bubble"
            style={{
              borderColor:
                toast.level === "error" ? "rgba(248,113,113,.45)" : toast.level === "warn" ? "rgba(251,191,36,.45)" : "var(--line-strong)",
              maxWidth: 340,
              fontSize: 13,
              boxShadow: "0 12px 30px rgba(0,0,0,.5)",
            }}
          >
            {toast.message}
          </div>
        ))}
      </div>
    </div>
  );
}
