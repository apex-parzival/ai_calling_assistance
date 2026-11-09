"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { StatusBadge, UrgencyBadge } from "@/components/Badges";
import { TopBar } from "@/components/TopBar";
import { api, type CallFilters, type CallSummary } from "@/lib/api";
import { RequireAuth } from "@/lib/auth";
import { duration, outcome, phone, when } from "@/lib/format";
import { chime, unlockAudio, useLiveEvents, type LiveEvent } from "@/lib/live";

type Tab = "all" | "new" | "urgent" | "today";
const TABS: { id: Tab; label: string }[] = [
  { id: "all", label: "All" },
  { id: "new", label: "New" },
  { id: "urgent", label: "Urgent" },
  { id: "today", label: "Today" },
];

function filtersFor(tab: Tab, q: string): CallFilters {
  return {
    status: tab === "new" ? "New" : undefined,
    urgent: tab === "urgent" || undefined,
    today: tab === "today" || undefined,
    q: q.trim() || undefined,
  };
}

function matches(c: CallSummary, tab: Tab, q: string): boolean {
  if (tab === "new" && c.status !== "New") return false;
  if (tab === "urgent" && c.urgency === "routine") return false;
  if (tab === "today" && new Date(c.started_at).toDateString() !== new Date().toDateString()) return false;
  const needle = q.trim().toLowerCase();
  if (needle && ![c.caller_name, c.caller_phone, c.reason].some((v) => v?.toLowerCase().includes(needle))) return false;
  return true;
}

function readSoundPref(): boolean {
  try {
    return localStorage.getItem("sound") === "on";
  } catch {
    return false;
  }
}

function CallList() {
  const router = useRouter();
  const [tab, setTab] = useState<Tab>("all");
  const [q, setQ] = useState("");
  const [calls, setCalls] = useState<CallSummary[]>([]);
  const [next, setNext] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [sound, setSound] = useState(false);
  const [fresh, setFresh] = useState<Set<string>>(new Set());
  const view = useRef({ tab, q, sound });
  view.current = { tab, q, sound };

  useEffect(() => setSound(readSoundPref()), []);

  const load = useCallback(async (before?: string) => {
    setLoading(true);
    setError(null);
    try {
      const page = await api.calls({ ...filtersFor(view.current.tab, view.current.q), before });
      setCalls((prev) => (before ? [...prev, ...page.items] : page.items));
      setNext(page.next_before);
    } catch {
      setError("Couldn't load calls.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const t = setTimeout(() => load(), q ? 300 : 0);
    return () => clearTimeout(t);
  }, [tab, q, load]);

  const live = useLiveEvents((e: LiveEvent) => {
    const call = e.data;
    const { tab, q, sound } = view.current;
    setCalls((prev) => {
      const i = prev.findIndex((c) => c.id === call.id);
      if (i >= 0) {
        const before = prev[i];
        if (sound && before.urgency === "routine" && call.urgency !== "routine") chime(true);
        const copy = prev.slice();
        copy[i] = call;
        return copy;
      }
      return matches(call, tab, q) ? [call, ...prev] : prev;
    });
    if (e.type === "call.ended" || e.type === "call.started") {
      setFresh((s) => new Set(s).add(call.id));
      setTimeout(() => setFresh((s) => { const n = new Set(s); n.delete(call.id); return n; }), 3000);
    }
    if (sound && e.type === "call.ended") chime(call.urgency !== "routine");
  });

  function toggleSound() {
    const on = !sound;
    if (on) unlockAudio();
    setSound(on);
    try {
      localStorage.setItem("sound", on ? "on" : "off");
    } catch {
      /* storage unavailable */
    }
  }

  return (
    <main>
      <div className="toolbar">
        <div className="tabs" role="group" aria-label="Filter calls">
          {TABS.map((t) => (
            <button key={t.id} aria-pressed={tab === t.id} onClick={() => setTab(t.id)}>{t.label}</button>
          ))}
        </div>
        <input className="search" type="search" placeholder="Search name, number or reason" value={q}
               onChange={(e) => setQ(e.target.value)} aria-label="Search calls" />
        <div className="status-line">
          <span><span className={`dot ${live === "live" ? "live" : live === "offline" ? "offline" : ""}`} />
            {live === "live" ? "Live" : live === "offline" ? "Reconnecting…" : "Connecting…"}</span>
          <button className="btn small" onClick={toggleSound} aria-pressed={sound}>
            {sound ? "🔔 Sound on" : "🔕 Sound off"}
          </button>
        </div>
      </div>

      <div className="card">
        {error && <p className="empty">{error} <button className="btn small" onClick={() => load()}>Retry</button></p>}
        {!error && !loading && calls.length === 0 && <p className="empty">No calls here yet.</p>}
        {calls.length > 0 && (
          <table className="calls">
            <thead>
              <tr>
                <th>Caller</th>
                <th>Reason</th>
                <th className="hide-sm">Outcome</th>
                <th className="hide-sm">When</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {calls.map((c) => (
                <tr key={c.id}
                    className={[fresh.has(c.id) && "fresh", c.urgency !== "routine" && `row-${c.urgency}`].filter(Boolean).join(" ")}
                    onClick={() => router.push(`/calls/${c.id}`)}>
                  <td>
                    <a href={`/calls/${c.id}`} className="caller" onClick={(e) => e.preventDefault()}>
                      {c.caller_name ?? "Unknown caller"}
                    </a>
                    <div className="sub">{phone(c.caller_phone)}</div>
                  </td>
                  <td className="reason">
                    <div>{c.reason ?? <span className="sub">—</span>} <UrgencyBadge urgency={c.urgency} /></div>
                    {c.summary && <div className="sub">{c.summary}</div>}
                  </td>
                  <td className="hide-sm">
                    {outcome(c.outcome)}
                    {(c.outcome === "follow_up_promised" || c.outcome === "system_error" ||
                      c.appointment_requests.some((a) => a.booking_status === "requested" || a.booking_status === "cancel_requested")) &&
                      <div><span className="badge urgent">Needs action</span></div>}
                  </td>
                  <td className="hide-sm">{when(c.started_at)}<div className="sub">{duration(c.duration)}</div></td>
                  <td>
                    <StatusBadge status={c.status} live={!c.ended_at} />
                    {c.assigned_to && <div className="sub">{c.assigned_to.name}</div>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      {next && (
        <p style={{ textAlign: "center" }}>
          <button className="btn" disabled={loading} onClick={() => load(next)}>Load older calls</button>
        </p>
      )}
    </main>
  );
}

export default function Page() {
  return (
    <RequireAuth>
      <TopBar />
      <CallList />
    </RequireAuth>
  );
}
