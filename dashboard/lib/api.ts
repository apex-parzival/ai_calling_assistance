// Typed client for the FastAPI backend. Auth is an HttpOnly session cookie set by
// the backend, so every request sends credentials.

export const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000";

export type Urgency = "routine" | "urgent" | "emergency";
export type CallStatus = "New" | "In progress" | "Handled";
export type BookingStatus = "requested" | "confirmed" | "declined" | "cancel_requested" | "cancelled";

export interface Staff {
  id: string;
  name: string;
  email: string;
  role: "admin" | "staff";
}

export interface Appointment {
  id: string;
  call_id: string | null;
  kind: "book" | "cancel";
  patient_name: string | null;
  patient_phone: string;
  requested_date: string;
  requested_time: string | null;
  service: string | null;
  booking_status: BookingStatus;
  created_at: string;
}

export interface Message {
  id: string;
  call_id: string | null;
  caller_name: string | null;
  caller_phone: string | null;
  reason: string;
  urgency: Urgency;
  status: "New" | "Handled";
  created_at: string;
}

export interface CallSummary {
  id: string;
  caller_name: string | null;
  caller_phone: string | null;
  started_at: string;
  ended_at: string | null;
  duration: number | null;
  reason: string | null;
  urgency: Urgency;
  status: CallStatus;
  outcome: string | null;
  summary: string | null;
  assigned_to: Staff | null;
  appointment_requests: Appointment[];
}

export interface TranscriptTurn {
  role: "user" | "assistant" | "system";
  text: string;
  at: string | null;
}

export interface LatencySample {
  turn: number;
  total: number | null;
  eou?: number;
  stt?: number;
  llm_ttft?: number;
  tts_ttfb?: number;
}

export interface CallDetail extends CallSummary {
  room_name: string | null;
  transcript: TranscriptTurn[] | null;
  notes: string | null;
  recording_url: string | null;
  latency: LatencySample[] | null;
  messages: Message[];
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${BACKEND_URL}${path}`, {
    ...init,
    credentials: "include",
    cache: "no-store",
    headers: { "Content-Type": "application/json", ...(init.headers ?? {}) },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, detail);
  }
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

export interface CallFilters {
  status?: CallStatus;
  urgent?: boolean;
  today?: boolean;
  q?: string;
  before?: string;
}

export const api = {
  login: (email: string, password: string) =>
    request<Staff>("/auth/login", { method: "POST", body: JSON.stringify({ email, password }) }),
  logout: () => request<void>("/auth/logout", { method: "POST" }),
  me: () => request<Staff>("/auth/me"),
  staff: () => request<Staff[]>("/staff"),
  calls: (f: CallFilters) => {
    const p = new URLSearchParams();
    if (f.status) p.set("status", f.status);
    if (f.urgent) p.set("urgent", "true");
    if (f.today) p.set("today", "true");
    if (f.q) p.set("q", f.q);
    if (f.before) p.set("before", f.before);
    return request<{ items: CallSummary[]; next_before: string | null }>(`/calls?${p}`);
  },
  call: (id: string) => request<CallDetail>(`/calls/${id}`),
  updateCall: (id: string, body: Partial<{ status: CallStatus; notes: string | null; assigned_to_id: string | null }>) =>
    request<CallDetail>(`/calls/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  updateAppointment: (id: string, booking_status: BookingStatus) =>
    request<Appointment>(`/appointments/${id}`, { method: "PATCH", body: JSON.stringify({ booking_status }) }),
  updateMessage: (id: string, status: "New" | "Handled") =>
    request<Message>(`/messages/${id}`, { method: "PATCH", body: JSON.stringify({ status }) }),
};

export function wsUrl(): string {
  return BACKEND_URL.replace(/^http/, "ws") + "/ws";
}
