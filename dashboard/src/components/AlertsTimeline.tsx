import { Bell, Check, Mic, Vibrate } from "lucide-react";
import { useMemo, useState } from "react";
import { api } from "../lib/api";
import { clockTime } from "../lib/format";
import { SEVERITY } from "../lib/severity";
import { useStore } from "../lib/store";
import type { Severity } from "../types/contracts";
import { SeverityIcon } from "./AlertBanner";
import { Empty, Panel } from "./ui";

type Tab = "alerts" | "events" | "spoken";

function SevChip({ s }: { s: Severity }) {
  const v = SEVERITY[s];
  return (
    <span className="inline-flex shrink-0 items-center gap-1 rounded px-1 text-[10px] font-bold" style={{ background: v.color, color: v.ink }}>
      <SeverityIcon a={{ severity: s }} size={11} />
      {v.label}
    </span>
  );
}

export function AlertsTimeline() {
  const [tab, setTab] = useState<Tab>("alerts");
  const [filter, setFilter] = useState<Severity | "all">("all");
  const alerts = useStore((s) => s.alerts);
  const events = useStore((s) => s.events);
  const transcript = useStore((s) => s.transcript);
  const evs = useMemo(() => [...events].reverse().filter((e) => filter === "all" || e.severity === filter), [events, filter]);
  const tabs: [Tab, string, number][] = [
    ["alerts", "Alerts", alerts.length],
    ["events", "Events", events.length],
    ["spoken", "Spoken", transcript.length],
  ];
  return (
    <Panel
      title={
        <span className="flex gap-1">
          {tabs.map(([k, l, n]) => (
            <button key={k} onClick={() => setTab(k)} className={`rounded px-1.5 py-0.5 ${tab === k ? "bg-surface-3 text-ink-1" : "text-ink-3 hover:text-ink-2"}`}>
              {l} <span className="tnum text-ink-3">{n}</span>
            </button>
          ))}
        </span>
      }
      icon={<Bell size={14} />}
      right={
        tab === "events" && (
          <select value={filter} onChange={(e) => setFilter(e.target.value as Severity | "all")} className="h-6 rounded border border-line-strong bg-surface-2 px-1 text-[11px] text-ink-2">
            <option value="all">all severities</option>
            {Object.keys(SEVERITY).map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        )
      }
      bodyClass="overflow-y-auto scroll-thin"
    >
      {tab === "alerts" &&
        (alerts.length === 0 ? (
          <Empty>No alerts yet. Alerts are kinematic facts or sensor faults, never threat guesses.</Empty>
        ) : (
          <ul className="divide-y divide-[var(--border)]">
            {[...alerts].reverse().map((a) => (
              <li key={a.alert_id} className={`flex items-start gap-2 px-3 py-1.5 ${a.acked ? "opacity-60" : ""}`}>
                <span className="tnum mt-0.5 w-12 shrink-0 text-[11px] text-ink-3">{clockTime(a.ts_ms)}</span>
                <SevChip s={a.severity} />
                <div className="min-w-0 flex-1">
                  <div className="text-[12px] leading-4 text-ink-1">{a.text}</div>
                  <div className="mt-0.5 flex flex-wrap items-center gap-2 text-[10px] text-ink-3">
                    <span>{a.kind}</span>
                    <span title="who decided to deliver it">via {a.message_source}</span>
                    {a.delivery.includes("phone_speech") && <Mic size={10} />}
                    {a.delivery.includes("phone_haptic") && <Vibrate size={10} />}
                    {a.haptic && <span>{a.haptic}</span>}
                  </div>
                </div>
                {a.ack_required && !a.acked && (
                  <button className="shrink-0 rounded border border-line-strong px-1.5 text-[11px] text-ink-1 hover:bg-surface-3" onClick={() => api.post("/api/alerts/ack", { alert_id: a.alert_id }).catch(() => undefined)}>
                    <Check size={11} className="inline" /> ack
                  </button>
                )}
                {a.acked && <span className="text-[10px] text-good-ink">acked</span>}
              </li>
            ))}
          </ul>
        ))}
      {tab === "events" &&
        (evs.length === 0 ? (
          <Empty>No events.</Empty>
        ) : (
          <ul className="divide-y divide-[var(--border)]">
            {evs.map((e) => (
              <li key={e.event_id} className="flex items-start gap-2 px-3 py-1" title={`evidence: ${e.evidence_refs.join(", ")}\n${JSON.stringify(e.data)}`}>
                <span className="tnum mt-0.5 w-12 shrink-0 text-[11px] text-ink-3">{clockTime(e.ts_ms)}</span>
                <SevChip s={e.severity} />
                <div className="min-w-0 flex-1">
                  <div className="text-[12px] leading-4">
                    <span className="font-semibold text-ink-2">{e.kind}</span> <span className="text-ink-1">{e.text}</span>
                  </div>
                  <div className="text-[10px] text-ink-3">
                    {e.geometry_valid ? "validated geometry" : "no metric claim"} · {e.evidence_refs.join(" · ")}
                  </div>
                </div>
              </li>
            ))}
          </ul>
        ))}
      {tab === "spoken" &&
        (transcript.length === 0 ? (
          <Empty>Nothing spoken to the responder yet.</Empty>
        ) : (
          <ul className="divide-y divide-[var(--border)]">
            {[...transcript].reverse().map((t, i) => (
              <li key={i} className="flex items-start gap-2 px-3 py-1.5">
                <span className="tnum mt-0.5 w-12 shrink-0 text-[11px] text-ink-3">{clockTime(t.ts_ms)}</span>
                <Mic size={13} className="mt-0.5 shrink-0 text-ink-3" />
                <div className="min-w-0">
                  <div className="text-[13px] text-ink-1">“{t.text}”</div>
                  <div className="text-[10px] text-ink-3">{t.source}</div>
                </div>
              </li>
            ))}
          </ul>
        ))}
    </Panel>
  );
}
