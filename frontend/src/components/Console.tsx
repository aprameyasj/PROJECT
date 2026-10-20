import { useEffect, useRef } from "react";
import type { ConsoleItem, ConsoleStep } from "../hooks/useJarvis";
import type { PendingAction } from "../types";

interface Props {
  items: ConsoleItem[];
  busy: boolean;
  pending: PendingAction | null;
  onConfirm: (token: string, approved: boolean) => void;
}

const GLYPH: Record<ConsoleStep["status"], string> = {
  queued: "·",
  running: "▶",
  ok: "✓",
  failed: "✗",
  blocked: "⛔",
  denied: "⊘",
  awaiting_confirmation: "…",
};

const clock = (ts: number): string =>
  new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });

function argsLabel(args: Record<string, unknown>): string {
  const entries = Object.entries(args ?? {}).filter(([key]) => !key.startsWith("_"));
  if (!entries.length) return "";
  const rendered = entries
    .map(([key, value]) => `${key}=${typeof value === "string" ? `"${value}"` : JSON.stringify(value)}`)
    .join(", ");
  return rendered.length > 120 ? `${rendered.slice(0, 117)}…` : rendered;
}

function Trace({ steps }: { steps: ConsoleStep[] }) {
  if (!steps?.length) return null;
  return (
    <div className="trace">
      {steps.map((step, index) => (
        <div className={`trace-row ${step.status}`} key={`${step.tool}-${index}`}>
          <span className="glyph">{GLYPH[step.status]}</span>
          <span>
            <span className="trace-tool">{step.tool}</span>
            <span className="trace-args"> {argsLabel(step.arguments)}</span>
            {step.permission ? <span className={`perm ${step.permission}`} style={{ marginLeft: 8 }}>{step.permission}</span> : null}
            {step.message ? <span className="trace-msg">{step.message}</span> : null}
          </span>
          <span className="trace-time">
            {step.simulated ? <span className="sim-tag">sim </span> : null}
            {typeof step.duration_ms === "number" ? `${step.duration_ms}ms` : ""}
          </span>
        </div>
      ))}
    </div>
  );
}

function ConfirmationCard({ action, onConfirm }: { action: PendingAction; onConfirm: Props["onConfirm"] }) {
  return (
    <div className="confirm-card">
      <h4>Level-2 action · confirmation required</h4>
      <p>{action.prompt}</p>
      <div className="meta">
        <span>tool: {action.tool}</span>
        <span>args: {argsLabel(action.arguments) || "none"}</span>
      </div>
      <div className="confirm-actions">
        <button className="btn primary small" onClick={() => onConfirm(action.token, true)}>
          Yes, do it
        </button>
        <button className="btn danger small" onClick={() => onConfirm(action.token, false)}>
          No, cancel
        </button>
        <span className="meta" style={{ alignSelf: "center" }}>
          or just say “yes” / “no”
        </span>
      </div>
    </div>
  );
}

export default function Console({ items, busy, pending, onConfirm }: Props) {
  const bottom = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [items, pending, busy]);

  return (
    <>
      <div className="console-scroll">
        {items.length === 0 ? (
          <div className="empty">
            Say or type a command — “Jarvis, open VS Code”, “close this tab”, “remind me tomorrow at 9am to
            submit my assignment”, “take a screenshot”, “prepare my workspace for coding”.
          </div>
        ) : null}

        {items.map((item) => (
          <div className={`msg ${item.kind}`} key={item.id}>
            <div className={`bubble ${item.level ?? ""}${item.streaming && !item.text ? " streaming" : ""}`}>
              {item.text || (item.streaming ? "" : "…")}
            </div>
            {item.steps?.length ? <Trace steps={item.steps} /> : null}
            <div className="meta">
              <span>{clock(item.at)}</span>
              {item.intent ? <span>intent: {item.intent}</span> : null}
              {item.provider ? <span>{item.provider}</span> : null}
              {item.simulated ? <span className="sim-tag">simulated</span> : null}
            </div>
          </div>
        ))}

        {pending ? <ConfirmationCard action={pending} onConfirm={onConfirm} /> : null}
        {busy && !pending ? <div className="meta" style={{ alignSelf: "flex-start" }}>working…</div> : null}
        <div ref={bottom} />
      </div>
    </>
  );
}
