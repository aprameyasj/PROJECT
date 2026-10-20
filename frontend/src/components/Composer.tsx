import { useState, type FormEvent } from "react";

interface Props {
  onSend: (text: string) => void;
  onToggleMic: () => void;
  listening: boolean;
  interim: string;
  micAvailable: boolean;
  busy: boolean;
  disabled?: boolean;
}

export const QUICK_COMMANDS: string[] = [
  "Jarvis, open VS Code",
  "Jarvis, close this tab",
  "Jarvis, take a screenshot",
  "Search the web for FastAPI",
  "Remind me tomorrow at 9am to submit my assignment",
  "What time is it?",
  "What's my system status?",
  "Prepare my workspace for coding",
  "What can you do?",
  "Jarvis, shut down",
];

export default function Composer({ onSend, onToggleMic, listening, interim, micAvailable, busy }: Props) {
  const [draft, setDraft] = useState("");

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const text = draft.trim();
    if (!text) return;
    onSend(text);
    setDraft("");
  };

  return (
    <>
      {interim ? <div className="interim">🎙 {interim}…</div> : null}
      <form className="composer" onSubmit={submit}>
        <button
          type="button"
          className={`mic${listening ? " listening" : ""}`}
          onClick={onToggleMic}
          disabled={!micAvailable}
          title={micAvailable ? "Hold a conversation — click to start/stop listening" : "Microphone unavailable in this browser"}
        >
          {listening ? "■" : "🎙"}
        </button>
        <input
          type="text"
          value={draft}
          placeholder={listening ? "Listening… speak now" : "Tell JARVIS what to do…"}
          onChange={(event) => setDraft(event.target.value)}
          autoComplete="off"
          spellCheck={false}
        />
        <button type="submit" className="btn primary" disabled={busy || !draft.trim()}>
          Send
        </button>
      </form>
      <div className="quick">
        {QUICK_COMMANDS.map((command) => (
          <button key={command} type="button" onClick={() => onSend(command)} title={command}>
            {command.replace(/^Jarvis, /, "")}
          </button>
        ))}
      </div>
    </>
  );
}
