/**
 * Browser voice: speech recognition (mic → text) and speech synthesis (text → voice).
 *
 * Two capture paths, tried in this order:
 *
 *  1. Web Speech API (`SpeechRecognition`) — instant, no server needed.
 *  2. `MediaRecorder` → POST /api/voice — used when the browser has no
 *     recogniser but JARVIS has a server-side STT provider (Whisper).
 *
 * Both hand the same `onTranscript` callback, so the UI does not care which
 * one worked. If neither is available the composer stays text-only and says so.
 */

/* eslint-disable @typescript-eslint/no-explicit-any */

type RecogniserCtor = new () => any;

function recogniserCtor(): RecogniserCtor | null {
  const w = window as any;
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

export type MicMode = "webspeech" | "recorder" | "unsupported";

export function micMode(): MicMode {
  if (recogniserCtor()) return "webspeech";
  const devices = navigator.mediaDevices as MediaDevices | undefined;
  if (typeof devices?.getUserMedia === "function" && typeof (window as any).MediaRecorder !== "undefined") {
    return "recorder";
  }
  return "unsupported";
}

export interface RecognitionHandle {
  start: () => void;
  stop: () => void;
  readonly mode: MicMode;
}

export function startRecognition(options: {
  lang?: string;
  continuous?: boolean;
  interim?: boolean;
  onTranscript: (text: string, final: boolean) => void;
  onStateChange?: (listening: boolean) => void;
  onError?: (message: string) => void;
}): RecognitionHandle {
  const Ctor = recogniserCtor();
  const lang = options.lang ?? "en-US";
  if (!Ctor) {
    options.onError?.("This browser has no speech recognition. Type your command instead.");
    return { start: () => undefined, stop: () => undefined, mode: "unsupported" };
  }

  const recognition = new Ctor();
  recognition.lang = lang;
  recognition.continuous = options.continuous ?? false;
  recognition.interimResults = options.interim ?? true;
  recognition.maxAlternatives = 1;

  let running = false;

  recognition.onstart = () => {
    running = true;
    options.onStateChange?.(true);
  };
  recognition.onend = () => {
    running = false;
    options.onStateChange?.(false);
  };
  recognition.onerror = (event: any) => {
    running = false;
    options.onStateChange?.(false);
    const code = event?.error ?? "unknown";
    const friendly: Record<string, string> = {
      "not-allowed": "Microphone permission was denied.",
      "service-not-allowed": "The browser blocked the speech service.",
      "no-speech": "I didn't hear anything.",
      network: "Speech recognition needs network access; try the text box.",
      aborted: "",
    };
    const message = friendly[code] ?? `Speech recognition error: ${code}`;
    if (message) options.onError?.(message);
  };
  recognition.onresult = (event: any) => {
    let interim = "";
    let final = "";
    for (let i = event.resultIndex; i < event.results.length; i += 1) {
      const chunk = event.results[i][0]?.transcript ?? "";
      if (event.results[i].isFinal) final += chunk;
      else interim += chunk;
    }
    if (interim) options.onTranscript(interim.trim(), false);
    if (final) options.onTranscript(final.trim(), true);
  };

  return {
    mode: "webspeech",
    start: () => {
      if (running) return;
      try {
        recognition.start();
      } catch {
        /* already started */
      }
    },
    stop: () => {
      try {
        recognition.stop();
      } catch {
        /* ignore */
      }
    },
  };
}

/** Record a short clip and send it to JARVIS for server-side transcription. */
export async function recordAndSend(options: {
  endpoint: string;
  sessionId?: string;
  maxSeconds?: number;
  onStop?: () => void;
}): Promise<{ text: string; turn?: unknown } | null> {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  const recorder = new MediaRecorder(stream);
  const chunks: BlobPart[] = [];
  recorder.ondataavailable = (event) => chunks.push(event.data);

  return new Promise((resolve, reject) => {
    recorder.onstop = async () => {
      stream.getTracks().forEach((track) => track.stop());
      options.onStop?.();
      const blob = new Blob(chunks, { type: recorder.mimeType || "audio/webm" });
      if (!blob.size) return resolve(null);
      const reader = new FileReader();
      reader.onloadend = async () => {
        const base64 = String(reader.result).split(",").pop() ?? "";
        try {
          const response = await fetch(options.endpoint, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ audio: base64, session_id: options.sessionId ?? "" }),
          });
          if (!response.ok) throw new Error(`STT failed (${response.status})`);
          const data = await response.json();
          resolve({ text: data?.transcript?.text ?? "", turn: data?.turn });
        } catch (error) {
          reject(error);
        }
      };
      reader.readAsDataURL(blob);
    };
    recorder.start();
    window.setTimeout(() => recorder.state === "recording" && recorder.stop(), (options.maxSeconds ?? 12) * 1000);
  });
}

/* ------------------------------------------------------------------ */
/* speech synthesis                                                     */
/* ------------------------------------------------------------------ */

export class Speaker {
  enabled = true;
  voice: SpeechSynthesisVoice | null = null;
  rate = 1.0;
  pitch = 1.0;
  private lastSpoken = "";

  get supported(): boolean {
    return typeof window !== "undefined" && "speechSynthesis" in window;
  }

  voices(): SpeechSynthesisVoice[] {
    if (!this.supported) return [];
    return window.speechSynthesis.getVoices();
  }

  /** Prefer a British/en-gb voice — JARVIS, after all. */
  pickPreferred(): SpeechSynthesisVoice | null {
    const voices = this.voices();
    if (!voices.length) return null;
    const ranked = [
      (v: SpeechSynthesisVoice) => /en[-_]GB/i.test(v.lang) && /male|daniel|arthur|oliver/i.test(v.name),
      (v: SpeechSynthesisVoice) => /en[-_]GB/i.test(v.lang),
      (v: SpeechSynthesisVoice) => /google uk english male/i.test(v.name),
      (v: SpeechSynthesisVoice) => /^en/i.test(v.lang),
    ];
    for (const test of ranked) {
      const found = voices.find(test);
      if (found) return found;
    }
    return voices[0];
  }

  init(): void {
    if (!this.supported) return;
    const apply = () => {
      if (!this.voice) this.voice = this.pickPreferred();
    };
    apply();
    window.speechSynthesis.onvoiceschanged = apply;
  }

  speak(text: string, options: { force?: boolean } = {}): void {
    if (!this.supported) return;
    if (!this.enabled && !options.force) return;
    const clean = (text || "").trim();
    if (!clean || clean === this.lastSpoken) return;
    this.lastSpoken = clean;
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(clean.slice(0, 600));
    if (this.voice) utterance.voice = this.voice;
    utterance.rate = this.rate;
    utterance.pitch = this.pitch;
    window.speechSynthesis.speak(utterance);
  }

  stop(): void {
    if (this.supported) window.speechSynthesis.cancel();
  }

  async playAudio(url: string): Promise<void> {
    try {
      const audio = new Audio(url);
      await audio.play();
    } catch {
      /* autoplay blocked — the UI shows the text anyway */
    }
  }
}
