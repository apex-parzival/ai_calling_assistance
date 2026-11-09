"use client";

import { useEffect, useRef, useState } from "react";
import { wsUrl, type CallSummary } from "./api";

export type LiveEvent = { type: "call.started" | "call.ended" | "call.updated"; data: CallSummary };
export type LiveState = "connecting" | "live" | "offline";

/** Subscribe to backend WebSocket events, reconnecting with backoff. */
export function useLiveEvents(onEvent: (e: LiveEvent) => void): LiveState {
  const [state, setState] = useState<LiveState>("connecting");
  const handler = useRef(onEvent);
  handler.current = onEvent;

  useEffect(() => {
    let ws: WebSocket | null = null;
    let retry = 0;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let ping: ReturnType<typeof setInterval> | undefined;

    const connect = () => {
      setState("connecting");
      ws = new WebSocket(wsUrl());
      ws.onopen = () => {
        retry = 0;
        setState("live");
        ping = setInterval(() => ws?.readyState === WebSocket.OPEN && ws.send("ping"), 25_000);
      };
      ws.onmessage = (msg) => {
        try {
          handler.current(JSON.parse(msg.data));
        } catch {
          /* ignore malformed */
        }
      };
      ws.onclose = () => {
        clearInterval(ping);
        if (stopped) return;
        setState("offline");
        timer = setTimeout(connect, Math.min(30_000, 1000 * 2 ** retry++));
      };
    };
    connect();
    return () => {
      stopped = true;
      clearTimeout(timer);
      clearInterval(ping);
      ws?.close();
    };
  }, []);

  return state;
}

let audioCtx: AudioContext | null = null;

/** Short two-tone chime; urgent calls get a higher, repeated tone. Needs a prior user gesture. */
export function chime(urgent: boolean): void {
  try {
    audioCtx ??= new AudioContext();
    const ctx = audioCtx;
    const tones = urgent ? [880, 1175, 880, 1175] : [660, 880];
    tones.forEach((freq, i) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.frequency.value = freq;
      const t = ctx.currentTime + i * 0.18;
      gain.gain.setValueAtTime(0.0001, t);
      gain.gain.exponentialRampToValueAtTime(0.25, t + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.16);
      osc.connect(gain).connect(ctx.destination);
      osc.start(t);
      osc.stop(t + 0.17);
    });
  } catch {
    /* audio unavailable */
  }
}

export function unlockAudio(): void {
  try {
    audioCtx ??= new AudioContext();
    void audioCtx.resume();
  } catch {
    /* audio unavailable */
  }
}
