// Display helpers. Times are shown in the viewer's local time zone (the front desk is in the clinic).

export function phone(e164: string | null | undefined): string {
  if (!e164) return "Unknown number";
  const d = e164.replace(/^\+1/, "");
  return d.length === 10 ? `(${d.slice(0, 3)}) ${d.slice(3, 6)}-${d.slice(6)}` : e164;
}

export function when(iso: string): string {
  const d = new Date(iso);
  const today = new Date();
  const sameDay = d.toDateString() === today.toDateString();
  const time = d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  return sameDay ? time : `${d.toLocaleDateString([], { month: "short", day: "numeric" })}, ${time}`;
}

export function duration(seconds: number | null): string {
  if (seconds == null) return "Live";
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return m ? `${m}m ${s.toString().padStart(2, "0")}s` : `${s}s`;
}

export function day(isoDate: string): string {
  const [y, m, d] = isoDate.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" });
}

export function clock(hhmm: string | null): string {
  if (!hhmm) return "";
  const [h, m] = hhmm.split(":").map(Number);
  const suffix = h < 12 ? "a.m." : "p.m.";
  return `${h % 12 || 12}:${m.toString().padStart(2, "0")} ${suffix}`;
}

const OUTCOMES: Record<string, string> = {
  booking_requested: "Booking requested",
  cancel_requested: "Cancellation",
  message: "Message",
  transferred: "Transferred",
  emergency: "Told to call 911",
  answered: "Answered",
  silence: "No response",
  turn_limit: "Turn limit",
  time_limit: "Time limit",
};

export function outcome(o: string | null): string {
  return o ? (OUTCOMES[o] ?? o) : "—";
}

export const BOOKING_LABELS: Record<string, string> = {
  requested: "Needs confirming",
  confirmed: "Confirmed",
  declined: "Declined",
  cancel_requested: "Cancel requested",
  cancelled: "Cancelled",
};
