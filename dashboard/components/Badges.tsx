import type { CallStatus, Urgency } from "@/lib/api";

export function UrgencyBadge({ urgency }: { urgency: Urgency }) {
  if (urgency === "routine") return null;
  return <span className={`badge ${urgency}`}>{urgency === "emergency" ? "Emergency" : "Urgent"}</span>;
}

export function StatusBadge({ status, live }: { status: CallStatus; live?: boolean }) {
  if (live) return <span className="badge live">● Live</span>;
  const cls = status === "New" ? "new" : status === "Handled" ? "ok" : "";
  return <span className={`badge ${cls}`}>{status}</span>;
}
