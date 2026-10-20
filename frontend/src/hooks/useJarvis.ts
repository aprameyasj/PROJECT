/**
 * useJarvis — the dashboard's single source of truth.
 *
 * Connects to the agent over WebSocket, keeps the console, the live tool trace,
 * pending confirmations and every side panel in sync, and drives browser speech.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../lib/api";
import { JarvisSocket, type SocketMessage, type SocketState } from "../lib/socket";
import { Speaker, micMode, startRecognition, type RecognitionHandle } from "../lib/speech";
import type {
  AgentEvent,
  AuditEntry,
  CalendarEvent,
  Fact,
  PendingAction,
  Reminder,
  Screenshot,
  Status,
  Task,
  ToolSpec,
  Turn,
  TurnStep,
  Workflow,
} from "../types";

export interface ConsoleStep {
  tool: string;
  arguments: Record<string, unknown>;
  permission?: string;
  status: "queued" | "running" | "ok" | "failed" | "blocked" | "denied" | "awaiting_confirmation";
  message?: string;
  duration_ms?: number;
  simulated?: boolean;
  detail?: unknown;
}

export interface ConsoleItem {
  id: string;
  kind: "user" | "agent" | "system" | "reminder";
  text: string;
  at: number;
  intent?: string;
  steps?: ConsoleStep[];
  turn?: Turn;
  pending?: PendingAction | null;
  provider?: string;
  simulated?: boolean;
  level?: "info" | "warn" | "error";
  streaming?: boolean;
}

const uid = (): string => `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;

export interface JarvisState {
  connected: SocketState;
  status: Status | null;
  items: ConsoleItem[];
  pending: PendingAction | null;
  listening: boolean;
  interim: string;
  micAvailable: boolean;
  speechEnabled: boolean;
  busy: boolean;
  error: string | null;
  reminders: Reminder[];
  tasks: Task[];
  events: CalendarEvent[];
  facts: Record<string, Fact[]>;
  profile: Record<string, string>;
  screenshots: Screenshot[];
  audit: AuditEntry[];
  tools: Record<string, ToolSpec[]>;
  toolCount: number;
  workflows: Workflow[];
  sessionId: string;
}

export function useJarvis() {
  const [connected, setConnected] = useState<SocketState>("closed");
  const [status, setStatus] = useState<Status | null>(null);
  const [items, setItems] = useState<ConsoleItem[]>([]);
  const [pending, setPending] = useState<PendingAction | null>(null);
  const [listening, setListening] = useState(false);
  const [interim, setInterim] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [speechEnabled, setSpeechEnabled] = useState(true);

  const [reminders, setReminders] = useState<Reminder[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [events, setEvents] = useState<CalendarEvent[]>([]);
  const [facts, setFacts] = useState<Record<string, Fact[]>>({});
  const [profile, setProfile] = useState<Record<string, string>>({});
  const [screenshots, setScreenshots] = useState<Screenshot[]>([]);
  const [audit, setAudit] = useState<AuditEntry[]>([]);
  const [tools, setTools] = useState<Record<string, ToolSpec[]>>({});
  const [toolCount, setToolCount] = useState(0);
  const [workflows, setWorkflows] = useState<Workflow[]>([]);

  const sessionId = useRef<string>(`web-${Date.now().toString(36)}`);
  const socket = useRef<JarvisSocket | null>(null);
  const speaker = useRef<Speaker>(new Speaker());
  const recognition = useRef<RecognitionHandle | null>(null);
  const liveItem = useRef<string | null>(null);
  const speechEnabledRef = useRef(true);
  speechEnabledRef.current = speechEnabled;

  /* ---------------------------------------------------------------- */
  /* panel refresh                                                     */
  /* ---------------------------------------------------------------- */
  const refreshPanels = useCallback(async () => {
    const [rem, tsk, cal, mem, shots, aud, toolList, flows, st] = await Promise.allSettled([
      api.reminders("pending"),
      api.tasks("todo"),
      api.calendar("week"),
      api.memory(),
      api.screenshots(18),
      api.audit(60),
      api.tools(),
      api.workflows(),
      api.status(),
    ]);
    if (rem.status === "fulfilled") setReminders(rem.value.reminders);
    if (tsk.status === "fulfilled") setTasks(tsk.value.tasks);
    if (cal.status === "fulfilled") setEvents(cal.value.events);
    if (mem.status === "fulfilled") {
      setFacts(mem.value.categories);
      setProfile(mem.value.profile);
    }
    if (shots.status === "fulfilled") setScreenshots(shots.value.screenshots);
    if (aud.status === "fulfilled") setAudit(aud.value.entries);
    if (toolList.status === "fulfilled") {
      setTools(toolList.value.categories);
      setToolCount(toolList.value.count);
    }
    if (flows.status === "fulfilled") setWorkflows(flows.value.workflows);
    if (st.status === "fulfilled") setStatus(st.value);
  }, []);

  /* ---------------------------------------------------------------- */
  /* console helpers                                                   */
  /* ---------------------------------------------------------------- */
  const pushItem = useCallback((item: ConsoleItem) => {
    setItems((current) => [...current.slice(-299), item]);
  }, []);

  const patchItem = useCallback((id: string, patch: Partial<ConsoleItem>) => {
    setItems((current) => current.map((item) => (item.id === id ? { ...item, ...patch } : item)));
  }, []);

  const patchLastAgentStep = useCallback((id: string, updater: (steps: ConsoleStep[]) => ConsoleStep[]) => {
    setItems((current) =>
      current.map((item) => (item.id === id ? { ...item, steps: updater(item.steps ?? []) } : item)),
    );
  }, []);

  const ensureLiveItem = useCallback((): string => {
    if (liveItem.current) return liveItem.current;
    const id = uid();
    liveItem.current = id;
    pushItem({ id, kind: "agent", text: "", at: Date.now(), steps: [], streaming: true });
    return id;
  }, [pushItem]);

  const speak = useCallback((text: string, audioUrl?: string) => {
    if (!text && !audioUrl) return;
    if (audioUrl) {
      void speaker.current.playAudio(audioUrl);
      return;
    }
    if (speechEnabledRef.current) speaker.current.speak(text);
  }, []);

  /* ---------------------------------------------------------------- */
  /* websocket                                                         */
  /* ---------------------------------------------------------------- */
  useEffect(() => {
    speaker.current.enabled = true;
    speaker.current.init();

    const client = new JarvisSocket(() => api.wsUrl());
    socket.current = client;

    client.onState((state) => setConnected(state));

    client.onMessage((message: SocketMessage) => {
      const payload = message.payload ?? {};

      switch (message.type) {
        case "hello": {
          const snapshot = payload.status as Status | undefined;
          if (snapshot) setStatus(snapshot);
          const waiting = payload.pending as PendingAction[] | undefined;
          if (waiting && waiting.length) setPending(waiting[0]);
          const recent = (payload.recent_events ?? []) as AgentEvent[];
          if (recent.length && items.length === 0) {
            pushItem({
              id: uid(),
              kind: "system",
              level: "info",
              text: `Connected to JARVIS · ${snapshot?.agent.backend.name ?? "?"} backend · ${
                snapshot?.agent.brain.name ?? "?"
              } brain · ${snapshot?.agent.tools ?? 0} tools (${recent.length} recent events replayed)`,
              at: Date.now(),
            });
          }
          break;
        }

        case "user_message": {
          const text = String(payload.text ?? "");
          if (text) pushItem({ id: uid(), kind: "user", text, at: Date.now() });
          setBusy(true);
          liveItem.current = null;
          break;
        }

        case "transcript": {
          const text = String((payload as { text?: string }).text ?? "");
          if (text) pushItem({ id: uid(), kind: "system", level: "info", text: `Heard: “${text}”`, at: Date.now() });
          break;
        }

        case "thinking": {
          const id = ensureLiveItem();
          patchItem(id, {
            intent: String(payload.intent ?? ""),
            provider: String(payload.provider ?? ""),
            streaming: true,
          });
          break;
        }

        case "tool_call": {
          const id = ensureLiveItem();
          const call = payload.call as { name: string; arguments: Record<string, unknown> };
          const decision = payload.decision as { permission?: string; allowed?: boolean; blocked?: boolean };
          patchLastAgentStep(id, (steps) => [
            ...steps,
            {
              tool: call.name,
              arguments: call.arguments,
              permission: decision?.permission,
              status: decision?.blocked ? "blocked" : "running",
            },
          ]);
          break;
        }

        case "tool_result": {
          const id = ensureLiveItem();
          const result = payload.result as {
            ok: boolean;
            message: string;
            duration_ms: number;
            simulated: boolean;
            data?: unknown;
            tool: string;
          };
          patchLastAgentStep(id, (steps) => {
            const index = [...steps].reverse().findIndex((s) => s.tool === result.tool && s.status !== "ok");
            const position = index === -1 ? steps.length - 1 : steps.length - 1 - index;
            const next = [...steps];
            next[position] = {
              ...next[position],
              status: next[position].status === "blocked" ? "blocked" : result.ok ? "ok" : "failed",
              message: result.message,
              duration_ms: result.duration_ms,
              simulated: result.simulated,
              detail: result.data,
            };
            return next;
          });
          break;
        }

        case "plan": {
          const id = ensureLiveItem();
          patchItem(id, { text: "", streaming: true });
          patchLastAgentStep(id, (steps) => steps);
          break;
        }

        case "confirmation_required": {
          const action = payload as unknown as PendingAction;
          setPending(action);
          const id = ensureLiveItem();
          patchItem(id, { text: action.prompt, pending: action, streaming: false });
          speak(action.prompt);
          break;
        }

        case "confirmation_resolved": {
          setPending(null);
          break;
        }

        case "agent_message": {
          const text = String(payload.text ?? "");
          const id = liveItem.current;
          if (id) {
            patchItem(id, { text, streaming: false, pending: (payload.pending as PendingAction) ?? null });
            liveItem.current = null;
          } else if (text) {
            pushItem({ id: uid(), kind: "agent", text, at: Date.now(), streaming: false });
          }
          setBusy(false);
          break;
        }

        case "speech": {
          speak(String(payload.text ?? ""), payload.audio_url ? String(payload.audio_url) : undefined);
          break;
        }

        case "reminder_fired": {
          const text = String(payload.message ?? payload.task ?? "Reminder");
          pushItem({ id: uid(), kind: "reminder", text, at: Date.now() });
          speak(text);
          void refreshPanels();
          break;
        }

        case "notification": {
          const text = String(payload.message ?? payload.body ?? "");
          if (text) {
            pushItem({
              id: uid(),
              kind: "system",
              level: payload.level === "blocked" ? "error" : "info",
              text,
              at: Date.now(),
            });
          }
          break;
        }

        case "error": {
          pushItem({ id: uid(), kind: "system", level: "error", text: String(payload.error ?? "Error"), at: Date.now() });
          setBusy(false);
          break;
        }

        case "turn": {
          const turn = payload as unknown as Turn;
          const id = liveItem.current;
          if (id) {
            patchItem(id, {
              text: turn.response,
              streaming: false,
              intent: turn.intent,
              turn,
              pending: turn.pending ?? null,
              provider: turn.provider,
              simulated: turn.simulated,
              steps: turn.steps.length
                ? (turn.steps as TurnStep[]).map((step) => ({
                    tool: step.tool,
                    arguments: step.arguments ?? {},
                    status: step.status,
                    message: step.message,
                    duration_ms: step.duration_ms,
                    simulated: step.simulated,
                  }))
                : undefined,
            });
            liveItem.current = null;
          }
          setPending(turn.pending ?? null);
          setBusy(false);
          void refreshPanels();
          break;
        }

        case "pong":
        case "status":
        default:
          break;
      }
    });

    client.connect();
    void refreshPanels();
    const poll = window.setInterval(() => void refreshPanels(), 20000);

    return () => {
      window.clearInterval(poll);
      client.close();
      speaker.current.stop();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /* ---------------------------------------------------------------- */
  /* actions                                                           */
  /* ---------------------------------------------------------------- */
  const send = useCallback(
    (text: string) => {
      const clean = text.trim();
      if (!clean) return;
      setError(null);
      const delivered = socket.current?.send({ type: "chat", text: clean, session_id: sessionId.current });
      if (!delivered) {
        // REST fallback keeps the UI usable while the socket reconnects
        pushItem({ id: uid(), kind: "user", text: clean, at: Date.now() });
        setBusy(true);
        void api
          .chat(clean, sessionId.current, speechEnabledRef.current)
          .then((turn) => {
            pushItem({
              id: uid(),
              kind: "agent",
              text: turn.response,
              at: Date.now(),
              intent: turn.intent,
              turn,
              pending: turn.pending,
              steps: turn.steps.map((step) => ({
                tool: step.tool,
                arguments: step.arguments ?? {},
                status: step.status,
                message: step.message,
                duration_ms: step.duration_ms,
                simulated: step.simulated,
              })),
            });
            setPending(turn.pending ?? null);
            if (turn.speech) speak(turn.speech);
            void refreshPanels();
          })
          .catch((err: Error) => setError(err.message))
          .finally(() => setBusy(false));
      }
    },
    [pushItem, refreshPanels, speak],
  );

  const confirm = useCallback(
    async (token: string, approved: boolean) => {
      setPending(null);
      socket.current?.send({ type: "confirm", token, approved, session_id: sessionId.current });
      if (!socket.current || socket.current.state !== "open") {
        try {
          const turn = await api.confirm(token, approved, sessionId.current);
          pushItem({ id: uid(), kind: "agent", text: turn.response, at: Date.now(), turn });
          void refreshPanels();
        } catch (err) {
          setError((err as Error).message);
        }
      }
    },
    [pushItem, refreshPanels],
  );

  const runTool = useCallback(
    async (name: string, args: Record<string, unknown>) => {
      setError(null);
      try {
        const outcome = await api.runTool(name, args, sessionId.current);
        if (outcome.pending) {
          setPending(outcome.pending);
          pushItem({ id: uid(), kind: "agent", text: outcome.pending.prompt, at: Date.now(), pending: outcome.pending });
        } else if (outcome.result) {
          pushItem({
            id: uid(),
            kind: "agent",
            text: outcome.result.message,
            at: Date.now(),
            steps: [
              {
                tool: name,
                arguments: args,
                status: outcome.result.ok ? "ok" : "failed",
                message: outcome.result.message,
                duration_ms: outcome.result.duration_ms,
                simulated: outcome.result.simulated,
              },
            ],
          });
        }
        void refreshPanels();
      } catch (err) {
        setError((err as Error).message);
      }
    },
    [pushItem, refreshPanels],
  );

  const runWorkflow = useCallback(
    async (name: string, variables: Record<string, unknown> = {}) => {
      setError(null);
      try {
        const result = await api.runWorkflow(name, variables);
        pushItem({ id: uid(), kind: "agent", text: result.message, at: Date.now(), intent: `workflow:${name}` });
        void refreshPanels();
      } catch (err) {
        setError((err as Error).message);
      }
    },
    [pushItem, refreshPanels],
  );

  const toggleMic = useCallback(() => {
    const mode = micMode();
    if (mode === "unsupported") {
      setError("This browser cannot capture audio. Type your command instead.");
      return;
    }
    if (listening) {
      recognition.current?.stop();
      setListening(false);
      return;
    }
    setError(null);
    recognition.current = startRecognition({
      lang: "en-US",
      interim: true,
      onStateChange: setListening,
      onError: (message) => setError(message),
      onTranscript: (text, final) => {
        setInterim(final ? "" : text);
        if (final) {
          send(text);
          setListening(false);
        }
      },
    });
    if (recognition.current.mode === "unsupported") {
      setError("Speech recognition is unavailable — use the text box.");
      return;
    }
    recognition.current.start();
  }, [listening, send]);

  const toggleSpeech = useCallback(() => {
    setSpeechEnabled((current) => {
      if (current) speaker.current.stop();
      return !current;
    });
  }, []);

  const clearConsole = useCallback(() => setItems([]), []);

  const resetSession = useCallback(async () => {
    sessionId.current = `web-${Date.now().toString(36)}`;
    setItems([]);
    liveItem.current = null;
    try {
      await api.resetSession(sessionId.current);
      void refreshPanels();
    } catch (err) {
      setError((err as Error).message);
    }
  }, [refreshPanels]);

  const state: JarvisState = useMemo(
    () => ({
      connected,
      status,
      items,
      pending,
      listening,
      interim,
      micAvailable: micMode() !== "unsupported",
      speechEnabled,
      busy,
      error,
      reminders,
      tasks,
      events,
      facts,
      profile,
      screenshots,
      audit,
      tools,
      toolCount,
      workflows,
      sessionId: sessionId.current,
    }),
    [
      connected, status, items, pending, listening, interim, speechEnabled, busy, error,
      reminders, tasks, events, facts, profile, screenshots, audit, tools, toolCount, workflows,
    ],
  );

  return {
    state,
    actions: {
      send,
      confirm,
      runTool,
      runWorkflow,
      toggleMic,
      toggleSpeech,
      clearConsole,
      resetSession,
      refreshPanels,
      speak,
      setError,
    },
  };
}
