import type { Status } from "../types";
import type { SocketState } from "../lib/socket";

interface Props {
  status: Status | null;
  connected: SocketState;
  busy: boolean;
  speechEnabled: boolean;
  listening: boolean;
  toolCount: number;
  onToggleSpeech: () => void;
  onRefresh: () => void;
}

const backendLabel = (name?: string): string => {
  switch (name) {
    case "desktop":
      return "desktop (live)";
    case "sandbox":
      return "sandbox (simulated)";
    default:
      return name ?? "unknown";
  }
};

export default function Header({
  status,
  connected,
  busy,
  speechEnabled,
  listening,
  toolCount,
  onToggleSpeech,
  onRefresh,
}: Props) {
  const agent = status?.agent;
  const settings = agent?.settings;
  const brainName = agent?.brain?.name ?? "…";
  const brainModel = (agent?.brain?.model as string | undefined) ?? settings?.llm_model ?? "";
  const connChip =
    connected === "open" ? "ok" : connected === "connecting" ? "warn" : "bad";

  return (
    <header className="header">
      <div className={`reactor${connected === "open" ? "" : " off"}`} aria-hidden>
        <span />
        <span />
        <span />
      </div>

      <div className="brand">
        <h1>J.A.R.V.I.S</h1>
        <p>
          {settings?.agent_name ?? "JARVIS"} · local agent
          {settings?.user_name ? ` for ${settings.user_name}` : ""}
          {busy ? " · thinking…" : ""}
        </p>
      </div>

      <div className="chips">
        <span className={`chip ${connChip}`}>
          <i className="dot" />
          {connected === "open" ? "connected" : connected}
        </span>
        <span className={`chip ${agent?.backend?.simulated ? "warn" : "ok"}`}>
          backend <strong>{backendLabel(agent?.backend?.name)}</strong>
        </span>
        <span className={`chip ${settings?.llm_available ? "info" : "warn"}`}>
          brain <strong>{brainName}</strong>
          {brainModel ? ` · ${brainModel}` : ""}
        </span>
        <span className="chip">
          tools <strong>{toolCount || agent?.tools || 0}</strong>
        </span>
        <span className={`chip ${settings?.safety_mode === "yolo" ? "bad" : "ok"}`}>
          safety <strong>{settings?.safety_mode ?? "normal"}</strong>
        </span>
        {listening ? <span className="chip bad">● listening</span> : null}
        <button className={`chip ${speechEnabled ? "ok" : ""}`} onClick={onToggleSpeech} title="Toggle spoken replies">
          {speechEnabled ? "🔊 voice on" : "🔇 voice off"}
        </button>
        <button className="chip" onClick={onRefresh} title="Refresh panels">
          ↻ refresh
        </button>
      </div>
    </header>
  );
}
