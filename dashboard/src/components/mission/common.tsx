import { Check, CircleDashed, Hand, X } from "lucide-react";
import type { ReactNode } from "react";
import { withToken } from "../../lib/api";
import type { ObservationView } from "../../types/incident";
import { Badge } from "../ui";

export const PROV_COLOR: Record<string, string> = {
  live: "var(--good)",
  replay: "var(--warning)",
  simulated: "var(--s7)",
  manual: "var(--s4)",
};

/** LIVE / REPLAY / SIMULATED / MANUAL, always as a word (never colour alone). */
export function ProvBadge({ p, className = "" }: { p: string | undefined | null; className?: string }) {
  const k = (p ?? "simulated").toLowerCase();
  return (
    <Badge color={PROV_COLOR[k] ?? "var(--ink-3)"} className={className}>
      {k.toUpperCase()}
    </Badge>
  );
}

export function ReviewChip({ status }: { status: string }) {
  const map: Record<string, [string, ReactNode, string]> = {
    pending: ["var(--warning)", <CircleDashed size={11} key="p" />, "pending review"],
    confirmed: ["var(--good)", <Check size={11} key="c" />, "confirmed"],
    rejected: ["var(--ink-3)", <X size={11} key="r" />, "rejected"],
    manual: ["var(--s4)", <Hand size={11} key="m" />, "manual"],
  };
  const [c, icon, label] = map[status] ?? map.pending;
  return (
    <Badge color={c}>
      {icon}
      {label}
    </Badge>
  );
}

export function secondsSince(iso: string | null | undefined): number | null {
  if (!iso) return null;
  return Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
}

export function ago(iso: string | null | undefined): string {
  const s = secondsSince(iso);
  if (s === null) return "–";
  if (s < 60) return `${s} s ago`;
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  return `${Math.floor(s / 3600)} h ago`;
}

export function clockTime(iso: string | null | undefined): string {
  if (!iso) return "–";
  return new Date(iso).toLocaleTimeString([], { hour12: false });
}

export function evidenceUrl(ref: string | null | undefined): string | null {
  if (!ref || !ref.startsWith("evidence/")) return null;
  const name = ref.slice("evidence/".length).split("#")[0];
  return withToken(`/api/evidence/${encodeURIComponent(name)}`);
}

export function locationText(v: ObservationView): string {
  const o = v.observation;
  if (!o.world_position) return "location not yet verified";
  const u = o.horizontal_uncertainty_m;
  return `±${u != null ? u.toFixed(1) : "?"} m · ${o.position_estimation_method?.replaceAll("_", " ")}`;
}

export function sideOf(v: ObservationView): string | null {
  const s = (v.observation.attributes as Record<string, unknown> | undefined)?.side;
  return typeof s === "string" ? s : null;
}

/** Deterministic responder briefing. Every number comes from a computed field; nothing is invented. */
export function briefingFor(v: ObservationView): { message: string; severity: "info" | "attention" | "warning" } {
  const o = v.observation;
  const age = secondsSince(o.observed_at);
  const ageText = age !== null ? ` Last observed ${age} seconds ago.` : "";
  const side = sideOf(v);
  const sideText = side ? `, Side ${side}` : "";
  const status = v.review_status === "confirmed" ? "confirmed" : v.review_status === "rejected" ? "rejected" : "pending review";
  if (o.category === "person") {
    if (v.review_status !== "confirmed") return { message: `Possible person detected${sideText}. Awaiting confirmation.`, severity: "attention" };
    if (o.world_position && o.horizontal_uncertainty_m != null)
      return { message: `Confirmed person marker${sideText}. Position plus or minus ${o.horizontal_uncertainty_m.toFixed(1)} metres.${ageText}`, severity: "warning" };
    return { message: `Person confirmed by the operator${sideText}. Position unavailable. Refer to video.${ageText}`, severity: "warning" };
  }
  if (o.category === "opening") {
    const blocked = (o.attributes as Record<string, unknown> | undefined)?.blocked === true;
    return { message: `${capital(o.label)}${sideText}, ${blocked ? "marked blocked" : status}.`, severity: blocked ? "warning" : "info" };
  }
  if (o.category === "hazard") return { message: `${capital(o.label)}${sideText}, ${o.provenance === "manual" ? "manually marked" : status}.`, severity: "warning" };
  if (o.category === "supply") return { message: `${capital(o.label)} marked${o.world_position ? " on the map" : ""}.`, severity: "info" };
  return { message: `${capital(o.label)}${sideText}, ${status}.`, severity: "info" };
}

const capital = (s: string) => (s ? s[0].toUpperCase() + s.slice(1) : s);

export function Section({ title, right, children, className = "" }: { title: ReactNode; right?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`flex min-h-0 flex-col rounded-lg border border-line bg-surface ${className}`}>
      <header className="flex h-8 shrink-0 items-center gap-2 border-b border-line px-2.5">
        <h2 className="truncate text-[11px] font-bold uppercase tracking-wider text-ink-2">{title}</h2>
        <div className="ml-auto flex items-center gap-1">{right}</div>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
    </section>
  );
}
