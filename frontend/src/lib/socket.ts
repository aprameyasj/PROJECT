/**
 * WebSocket client with automatic reconnect and event fan-out.
 *
 * The socket is the dashboard's nervous system: everything JARVIS does
 * (thinking, tool calls, results, confirmations, reminders, speech) arrives
 * here as an event and is rendered live.
 */

import type { AgentEvent } from "../types";

export type SocketState = "connecting" | "open" | "closed" | "error";

export interface SocketMessage {
  type: string;
  payload: Record<string, unknown>;
  session_id?: string;
  ts?: number;
  id?: string;
}

type Listener = (message: SocketMessage) => void;
type StateListener = (state: SocketState) => void;

export class JarvisSocket {
  private socket: WebSocket | null = null;
  private listeners = new Set<Listener>();
  private stateListeners = new Set<StateListener>();
  private retries = 0;
  private timer: number | null = null;
  private manualClose = false;
  state: SocketState = "closed";
  url = "";

  constructor(private readonly urlFactory: () => string) {}

  connect(): void {
    this.manualClose = false;
    this.url = this.urlFactory();
    this.setState("connecting");
    try {
      this.socket = new WebSocket(this.url);
    } catch {
      this.scheduleReconnect();
      return;
    }

    this.socket.onopen = () => {
      this.retries = 0;
      this.setState("open");
    };

    this.socket.onmessage = (event: MessageEvent<string>) => {
      let message: SocketMessage;
      try {
        message = JSON.parse(event.data) as SocketMessage;
      } catch {
        return;
      }
      this.listeners.forEach((listener) => listener(message));
    };

    this.socket.onerror = () => this.setState("error");

    this.socket.onclose = () => {
      this.setState("closed");
      if (!this.manualClose) this.scheduleReconnect();
    };
  }

  private scheduleReconnect(): void {
    if (this.timer !== null) return;
    this.retries += 1;
    const delay = Math.min(15000, 600 * 2 ** Math.min(this.retries, 5));
    this.timer = window.setTimeout(() => {
      this.timer = null;
      this.connect();
    }, delay);
  }

  private setState(state: SocketState): void {
    this.state = state;
    this.stateListeners.forEach((listener) => listener(state));
  }

  onMessage(listener: Listener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  onState(listener: StateListener): () => void {
    this.stateListeners.add(listener);
    listener(this.state);
    return () => this.stateListeners.delete(listener);
  }

  send(message: Record<string, unknown>): boolean {
    if (this.socket && this.socket.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify(message));
      return true;
    }
    return false;
  }

  close(): void {
    this.manualClose = true;
    if (this.timer !== null) {
      clearTimeout(this.timer);
      this.timer = null;
    }
    this.socket?.close();
    this.socket = null;
    this.setState("closed");
  }
}

export type { AgentEvent };
