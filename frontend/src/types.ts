/** Shared shapes mirrored from the Python agent (`jarvis/core/types.py`). */

export type Permission = "safe" | "confirm" | "forbidden";

export interface ToolCall {
  name: string;
  arguments: Record<string, unknown>;
  id: string;
  source: string;
  reasoning?: string | null;
}

export interface ToolResult {
  ok: boolean;
  tool: string;
  call_id: string;
  message: string;
  data?: unknown;
  error?: string | null;
  speech?: string | null;
  duration_ms: number;
  simulated: boolean;
  artifacts?: Array<{ type: string; path?: string; url?: string; note?: string }>;
  steps?: ToolResult[];
}

export interface TurnStep {
  tool: string;
  arguments?: Record<string, unknown>;
  status: "ok" | "failed" | "blocked" | "denied" | "awaiting_confirmation";
  message?: string;
  duration_ms?: number;
  simulated?: boolean;
}

export interface PendingAction {
  token: string;
  session_id: string;
  tool: string;
  arguments: Record<string, unknown>;
  permission: Permission;
  prompt: string;
  created_at: number;
  expires_at: number;
  plan_id?: string | null;
  plan_index?: number;
}

export interface PlanStep {
  tool: string;
  arguments: Record<string, unknown>;
  status: string;
  critical: boolean;
  result?: ToolResult | null;
}

export interface Plan {
  id: string;
  title: string;
  goal: string;
  status: string;
  steps: PlanStep[];
}

export interface Turn {
  id: string;
  session_id: string;
  user_text: string;
  response: string;
  speech: string;
  intent: string;
  ok: boolean;
  steps: TurnStep[];
  tool_calls: ToolCall[];
  tool_results: ToolResult[];
  plan?: Plan | null;
  pending?: PendingAction | null;
  simulated: boolean;
  provider: string;
  model: string;
  duration_ms: number;
  created_at: number;
  error?: string | null;
}

export type EventName =
  | "session_start"
  | "transcript"
  | "user_message"
  | "thinking"
  | "plan"
  | "tool_call"
  | "tool_result"
  | "confirmation_required"
  | "confirmation_resolved"
  | "agent_message"
  | "speech"
  | "reminder_fired"
  | "notification"
  | "error"
  | "status";

export interface AgentEvent<P = Record<string, unknown>> {
  id: string;
  type: EventName;
  session_id: string;
  ts: number;
  payload: P;
}

export interface ToolSpec {
  name: string;
  description: string;
  permission: Permission;
  category: string;
  parameters: {
    type: string;
    properties: Record<string, { type?: string; description?: string; default?: unknown; enum?: unknown[] }>;
    required?: string[];
  };
  required: string[];
  examples: string[];
  enabled: boolean;
  mutating: boolean;
  needs: string[];
}

export interface Reminder {
  id: number;
  task: string;
  remind_at: string;
  status: string;
  recurrence?: string;
  channel?: string;
  notes?: string;
  created_at?: string;
}

export interface CalendarEvent {
  id: number;
  title: string;
  starts_at: string;
  ends_at?: string | null;
  location?: string;
  notes?: string;
  status?: string;
}

export interface Task {
  id: number;
  title: string;
  status: string;
  priority: number;
  project?: string;
  due_at?: string | null;
  notes?: string;
}

export interface Fact {
  id: number;
  key: string;
  value: string;
  category: string;
  source?: string;
  updated_at?: string;
}

export interface AuditEntry {
  id: number;
  ts: number;
  tool: string;
  arguments: Record<string, unknown>;
  permission: string;
  decision: string;
  reason: string;
  ok: number;
  message: string;
  duration_ms: number;
  simulated: number;
}

export interface Screenshot {
  id: number;
  kind: string;
  path: string;
  meta: Record<string, unknown>;
  created_at: string;
  exists: boolean;
  url?: string | null;
}

export interface Workflow {
  name: string;
  title: string;
  description: string;
  triggers: string[];
  steps: Array<{ tool: string; arguments?: Record<string, unknown>; why?: string }>;
  parameters: Record<string, unknown>;
}

export interface Status {
  agent: {
    settings: {
      agent_name: string;
      user_name: string;
      platform: string;
      python: string;
      llm_provider: string;
      llm_model?: string | null;
      llm_available: boolean;
      backend: string;
      dry_run: boolean;
      safety_mode: string;
      max_steps: number;
      timezone: string;
      wake_words: string[];
      home: string;
    };
    backend: Record<string, unknown> & { name: string; simulated?: boolean; reason?: string };
    brain: Record<string, unknown> & { name: string; model?: string };
    tools: number;
    tool_categories: Record<string, number>;
    memory: { facts: number; by_category: Record<string, number> };
    database: { tool_calls: number; blocked: number; confirmations: number; pending_reminders: number };
    safety: { mode: string; blocked: number };
    turns: number;
  };
  scheduler: { engine: string; running: boolean; fired_total: number; pending: number };
  stt: { provider: string; available: boolean; microphone: boolean };
  tts: { provider: string; available: boolean; voice?: string };
  listener: Record<string, unknown> & { available?: boolean };
  clients: number;
}
