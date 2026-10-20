/**
 * REST client for the JARVIS agent.
 *
 * All URLs are relative so the Vite proxy (or the FastAPI static mount) can
 * serve both the UI and the API from the same origin.
 */

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
  ToolResult,
  ToolSpec,
  Turn,
  Workflow,
} from "../types";

const TOKEN_KEY = "jarvis.token";

export function getToken(): string {
  try {
    return localStorage.getItem(TOKEN_KEY) ?? "";
  } catch {
    return "";
  }
}

export function setToken(token: string): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* private browsing */
  }
}

function headers(extra: Record<string, string> = {}): Record<string, string> {
  const token = getToken();
  return {
    "Content-Type": "application/json",
    ...(token ? { "X-Jarvis-Token": token } : {}),
    ...extra,
  };
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, { ...init, headers: headers(init.headers as Record<string, string>) });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      detail = body.detail ? JSON.stringify(body.detail) : detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

const withToken = (path: string): string => {
  const token = getToken();
  if (!token) return path;
  return path.includes("?") ? `${path}&token=${encodeURIComponent(token)}` : `${path}?token=${encodeURIComponent(token)}`;
};

export const api = {
  health: () => request<{ ok: boolean }>("/api/health"),
  status: () => request<Status>("/api/status"),

  chat: (text: string, sessionId = "", speak = true) =>
    request<Turn>("/api/chat", { method: "POST", body: JSON.stringify({ text, session_id: sessionId, speak }) }),
  confirm: (token: string, approved: boolean, sessionId = "") =>
    request<Turn>("/api/confirm", { method: "POST", body: JSON.stringify({ token, approved, session_id: sessionId }) }),
  pending: (sessionId = "") =>
    request<{ pending: PendingAction[] }>(`/api/pending${sessionId ? `?session_id=${sessionId}` : ""}`),
  resetSession: (sessionId = "") =>
    request<{ ok: boolean }>(`/api/session/reset?session_id=${sessionId}`, { method: "POST" }),

  tools: () => request<{ count: number; categories: Record<string, ToolSpec[]>; catalogue: string }>("/api/tools"),
  runTool: (name: string, args: Record<string, unknown> = {}, sessionId = "") =>
    request<{ status: string; result: ToolResult | null; pending: PendingAction | null }>(
      `/api/tools/${encodeURIComponent(name)}/run`,
      { method: "POST", body: JSON.stringify({ arguments: args, session_id: sessionId }) },
    ),

  workflows: () => request<{ workflows: Workflow[] }>("/api/workflows"),
  runWorkflow: (name: string, variables: Record<string, unknown> = {}) =>
    request<ToolResult>(`/api/workflows/${encodeURIComponent(name)}/run`, {
      method: "POST",
      body: JSON.stringify(variables),
    }),
  plan: (goal: string, execute = false) =>
    request<{ plan: unknown; run?: unknown }>("/api/plan", {
      method: "POST",
      body: JSON.stringify({ goal, execute }),
    }),

  reminders: (status = "pending") =>
    request<{ reminders: Reminder[] }>(`/api/reminders?status=${status}`),
  addReminder: (task: string, when: string, recurrence = "") =>
    request<Reminder>("/api/reminders", { method: "POST", body: JSON.stringify({ task, when, recurrence }) }),
  deleteReminder: (id: number) => request<{ ok: boolean }>(`/api/reminders/${id}`, { method: "DELETE" }),
  completeReminder: (id: number) => request<Reminder>(`/api/reminders/${id}/complete`, { method: "POST" }),
  snoozeReminder: (id: number, minutes = 10) =>
    request<Reminder>(`/api/reminders/${id}/snooze?minutes=${minutes}`, { method: "POST" }),

  calendar: (window = "week") => request<{ events: CalendarEvent[] }>(`/api/calendar?window=${window}`),
  addEvent: (payload: Partial<CalendarEvent> & { remind_minutes_before?: number }) =>
    request<CalendarEvent>("/api/calendar", { method: "POST", body: JSON.stringify(payload) }),
  cancelEvent: (id: number) => request<{ ok: boolean }>(`/api/calendar/${id}`, { method: "DELETE" }),

  tasks: (status = "todo") => request<{ tasks: Task[] }>(`/api/tasks?status=${status}`),
  addTask: (title: string, project = "", priority = 3) =>
    request<Task>("/api/tasks", { method: "POST", body: JSON.stringify({ title, project, priority }) }),
  setTaskStatus: (id: number, status: string) =>
    request<Task>(`/api/tasks/${id}/status?status=${status}`, { method: "POST" }),

  agenda: (window = "today") => request<{ data?: { timeline?: string[] } }>(`/api/agenda?window=${window}`),

  memory: () =>
    request<{ profile: Record<string, string>; categories: Record<string, Fact[]>; stats: unknown }>("/api/memory"),
  remember: (key: string, value: string, category = "general") =>
    request<Fact>("/api/memory", { method: "POST", body: JSON.stringify({ key, value, category }) }),
  forget: (key: string, category = "") =>
    request<{ ok: boolean }>(`/api/memory?key=${encodeURIComponent(key)}&category=${category}`, { method: "DELETE" }),

  audit: (limit = 60) => request<{ entries: AuditEntry[]; stats: Record<string, number> }>(`/api/audit?limit=${limit}`),
  events: (limit = 60) => request<{ events: AgentEvent[] }>(`/api/events?limit=${limit}`),
  screenshots: (limit = 24) => request<{ screenshots: Screenshot[] }>(`/api/screenshots?limit=${limit}`),

  voice: (text: string, sessionId = "") =>
    request<{ turn: Turn; text: string }>("/api/voice", {
      method: "POST",
      body: JSON.stringify({ text, session_id: sessionId }),
    }),
  ttsUrl: (text: string) => withToken(`/api/tts?text=${encodeURIComponent(text)}`),
  screenshotUrl: (name: string) => withToken(`/api/screenshots/${encodeURIComponent(name)}`),
  wsUrl: () => `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${withToken("/ws")}`,
};
