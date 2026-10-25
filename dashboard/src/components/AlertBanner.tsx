import { AlertOctagon, AlertTriangle, Check, Info, ShieldAlert, Waves } from "lucide-react";
import { api } from "../lib/api";
import { SEVERITY } from "../lib/severity";
import { EMPTY, useStore } from "../lib/store";
import type { Alert } from "../types/contracts";

export function SeverityIcon({ a, size = 16 }: { a: Pick<Alert, "severity">; size?: number }) {
  switch (a.severity) {
    case "critical":
      return <AlertOctagon size={size} />;
    case "local_proximity":
      return <Waves size={size} />;
    case "warning":
    case "attention":
      return <AlertTriangle size={size} />;
    case "sensor_fault":
      return <ShieldAlert size={size} />;
    default:
      return <Info size={size} />;
  }
}

/** The single most important current alert, big enough to read from across the room. */
export function AlertBanner() {
  const active = useStore((s) => s.state?.alerts_active ?? EMPTY);
  const top = active.find((a) => !a.acked && ["critical", "local_proximity", "warning"].includes(a.severity));
  if (!top) return null;
  const sev = SEVERITY[top.severity];
  return (
    <div
      className={`flex shrink-0 items-center gap-3 px-4 py-2 ${top.severity === "critical" ? "pulse-critical" : ""}`}
      style={{ background: sev.color, color: sev.ink }}
      role="alert"
    >
      <SeverityIcon a={top} size={22} />
      <span className="rounded px-1.5 text-[11px] font-black tracking-widest" style={{ boxShadow: `inset 0 0 0 1.5px ${sev.ink}` }}>
        {sev.label}
      </span>
      <span className="tnum truncate text-[17px] font-bold">{top.text}</span>
      <span className="hidden text-[11px] opacity-80 md:inline">via {top.message_source}</span>
      {top.ack_required && (
        <button
          onClick={() => api.post("/api/alerts/ack", { alert_id: top.alert_id }).catch(() => undefined)}
          className="ml-auto inline-flex items-center gap-1 rounded-md px-3 py-1 text-[13px] font-bold"
          style={{ background: sev.ink, color: sev.color }}
        >
          <Check size={15} /> ACK
        </button>
      )}
    </div>
  );
}
