"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { StatusBadge, UrgencyBadge } from "@/components/Badges";
import { TopBar } from "@/components/TopBar";
import { api, type BookingStatus, type CallDetail, type CallStatus, type Staff } from "@/lib/api";
import { RequireAuth, useStaff } from "@/lib/auth";
import { BOOKING_LABELS, clock, day, duration, outcome, phone, when } from "@/lib/format";
import { useLiveEvents } from "@/lib/live";

const STATUSES: CallStatus[] = ["New", "In progress", "Handled"];
const SLOW_SECONDS = 1.5;

function Detail({ id }: { id: string }) {
  const me = useStaff();
  const [call, setCall] = useState<CallDetail | null>(null);
  const [staff, setStaff] = useState<Staff[]>([]);
  const [notes, setNotes] = useState("");
  const [savedAt, setSavedAt] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const c = await api.call(id);
      setCall(c);
      setNotes(c.notes ?? "");
    } catch {
      setError("Couldn't load this call.");
    }
  }, [id]);

  useEffect(() => {
    load();
    api.staff().then(setStaff).catch(() => {});
  }, [load]);

  // Refresh when the agent posts the summary or another staff member changes this call.
  useLiveEvents((e) => {
    if (e.data.id === id) load();
  });

  async function update(body: Parameters<typeof api.updateCall>[1]) {
    try {
      setCall(await api.updateCall(id, body));
      setSavedAt(new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }));
    } catch {
      setError("Couldn't save. Try again.");
    }
  }

  async function setBooking(reqId: string, status: BookingStatus) {
    await api.updateAppointment(reqId, status).catch(() => setError("Couldn't update the request."));
    load();
  }

  async function toggleMessage(msgId: string, handled: boolean) {
    await api.updateMessage(msgId, handled ? "Handled" : "New").catch(() => setError("Couldn't update the message."));
    load();
  }

  if (error && !call) return <p className="page-message error">{error}</p>;
  if (!call) return <p className="page-message">Loading…</p>;

  const latency = call.latency ?? [];
  const totals = latency.map((l) => l.total).filter((t): t is number => t != null).sort((a, b) => a - b);
  const median = totals.length ? totals[Math.floor(totals.length / 2)] : null;

  return (
    <main>
      <Link href="/" className="back">← All calls</Link>
      <div className="detail">
        <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
          <div className="card">
            <section>
              <div className="headline">
                <h1>{call.caller_name ?? "Unknown caller"}</h1>
                <UrgencyBadge urgency={call.urgency} />
                <StatusBadge status={call.status} live={!call.ended_at} />
              </div>
              <p className="sub" style={{ margin: 0 }}>
                {call.caller_phone ? <a href={`tel:${call.caller_phone}`}>{phone(call.caller_phone)}</a> : "Unknown number"}
                {" · "}{when(call.started_at)}{" · "}{duration(call.duration)}
              </p>
            </section>
            <section>
              <h2>Summary</h2>
              <p style={{ margin: 0 }}>{call.summary ?? <span className="sub">{call.ended_at ? "Summary is being written…" : "Call in progress."}</span>}</p>
            </section>
            <section>
              <dl className="facts">
                <dt>Reason</dt><dd>{call.reason ?? "—"}</dd>
                <dt>Outcome</dt><dd>{outcome(call.outcome)}</dd>
              </dl>
            </section>
          </div>

          {call.appointment_requests.length > 0 && (
            <div className="card">
              <section>
                <h2>Appointment requests</h2>
                {call.appointment_requests.map((a) => (
                  <div className="request" key={a.id}>
                    <div>
                      <strong>{a.kind === "cancel" ? "Cancel" : a.service ?? "Appointment"}</strong>{" "}
                      {day(a.requested_date)}{a.requested_time && ` at ${clock(a.requested_time)}`}
                      <div className="sub">{a.patient_name ?? "—"} · {phone(a.patient_phone)} · {BOOKING_LABELS[a.booking_status]}</div>
                    </div>
                    <div className="actions">
                      {a.booking_status === "requested" && <>
                        <button className="btn small primary" onClick={() => setBooking(a.id, "confirmed")}>Confirmed with patient</button>
                        <button className="btn small" onClick={() => setBooking(a.id, "declined")}>Decline</button>
                      </>}
                      {a.booking_status === "cancel_requested" &&
                        <button className="btn small primary" onClick={() => setBooking(a.id, "cancelled")}>Cancelled in booking system</button>}
                    </div>
                  </div>
                ))}
              </section>
            </div>
          )}

          {call.messages.length > 0 && (
            <div className="card">
              <section>
                <h2>Messages</h2>
                {call.messages.map((m) => (
                  <div className="request" key={m.id}>
                    <div>
                      {m.reason} <UrgencyBadge urgency={m.urgency} />
                      <div className="sub">{m.caller_name ?? "—"} · {phone(m.caller_phone)}</div>
                    </div>
                    <label className="sub" style={{ display: "flex", gap: 6, alignItems: "center" }}>
                      <input type="checkbox" checked={m.status === "Handled"} onChange={(e) => toggleMessage(m.id, e.target.checked)} />
                      Handled
                    </label>
                  </div>
                ))}
              </section>
            </div>
          )}

          <div className="card">
            <section>
              <h2>Transcript</h2>
              {call.transcript?.length ? (
                <div className="transcript">
                  {call.transcript.map((t, i) => (
                    <div key={i} className={`turn ${t.role}`}>
                      {t.role !== "system" && <span className="who">{t.role === "user" ? "Caller" : "AI receptionist"}</span>}
                      {t.text}
                    </div>
                  ))}
                </div>
              ) : <p className="sub" style={{ margin: 0 }}>No transcript.</p>}
            </section>
          </div>
        </div>

        <aside style={{ display: "flex", flexDirection: "column", gap: 20 }}>
          <div className="card">
            <section>
              <h2>Follow-up</h2>
              {error && <p className="page-message error" style={{ padding: 0 }}>{error}</p>}
              <div className="field">
                <label htmlFor="status">Status</label>
                <select id="status" value={call.status} onChange={(e) => update({ status: e.target.value as CallStatus })}>
                  {STATUSES.map((s) => <option key={s}>{s}</option>)}
                </select>
              </div>
              <div className="field">
                <label htmlFor="assign">Assigned to</label>
                <select id="assign" value={call.assigned_to?.id ?? ""}
                        onChange={(e) => update({ assigned_to_id: e.target.value || null })}>
                  <option value="">Nobody</option>
                  {staff.map((s) => <option key={s.id} value={s.id}>{s.id === me.id ? `${s.name} (me)` : s.name}</option>)}
                </select>
              </div>
              <div className="field">
                <label htmlFor="notes">Notes</label>
                <textarea id="notes" value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="Called back, left voicemail…" />
              </div>
              <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                <button className="btn primary" disabled={notes === (call.notes ?? "")} onClick={() => update({ notes: notes || null })}>Save note</button>
                {call.status !== "Handled" && <button className="btn" onClick={() => update({ status: "Handled" })}>Mark handled</button>}
                {savedAt && <span className="saved">Saved {savedAt}</span>}
              </div>
            </section>
          </div>

          {latency.length > 0 && (
            <div className="card">
              <section>
                <h2>Reply latency{median != null && ` · median ${median.toFixed(2)}s`}</h2>
                <table className="latency">
                  <thead><tr><th>Turn</th><th>Total</th><th>EOU</th><th>LLM</th><th>TTS</th></tr></thead>
                  <tbody>
                    {latency.map((l) => (
                      <tr key={l.turn}>
                        <td>{l.turn}</td>
                        <td className={l.total != null && l.total > SLOW_SECONDS ? "slow" : ""}>{l.total?.toFixed(2) ?? "—"}</td>
                        <td>{l.eou?.toFixed(2) ?? "—"}</td>
                        <td>{l.llm_ttft?.toFixed(2) ?? "—"}</td>
                        <td>{l.tts_ttfb?.toFixed(2) ?? "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </section>
            </div>
          )}
        </aside>
      </div>
    </main>
  );
}

export default function Page() {
  const { id } = useParams<{ id: string }>();
  return (
    <RequireAuth>
      <TopBar />
      <Detail id={id} />
    </RequireAuth>
  );
}
