import { Route } from "lucide-react";
import { useStore } from "../lib/store";
import type { PursuitCandidate, PursuitHypothesis, PursuitState } from "../types/contracts";
import { ProvBadge, Section } from "./mission/common";
import { Badge } from "./ui";

/** Hypothesis colours, in a fixed order: the subject's own cloud first (the subject colour), then look-alikes. The
 *  same hex values are used for the particle clouds on the map and in the 3D view. */
export const HYP_COLORS = ["#d95926", "#c98500", "#d55181", "#199e70"];
export const hypColor = (i: number) => HYP_COLORS[i % HYP_COLORS.length];

const pct = (v: number | null | undefined) => (v == null ? "–" : `${Math.round(100 * v)}%`);

/** Route reasoning: the belief's identity hypotheses with their weights and evidence, the candidate viewpoints with
 *  the share of the belief each would cover, and the route chosen (rules baseline vs model, confidence). This is
 *  what explains why the drone flies where it does while the subject is lost. */
export function PursuitCard({ className = "" }: { className?: string }) {
  const p = useStore((s) => s.state?.pursuit ?? null);
  const fsm = useStore((s) => s.state?.autonomy.fsm ?? null);
  if (!p) return null;
  return (
    <Section
      title={<span className="flex items-center gap-1.5"><Route size={12} /> Route reasoning{p.active && p.lost_s != null ? ` · subject unseen ${p.lost_s.toFixed(0)} s` : ""}</span>}
      right={<ProvBadge p={p.provenance} />}
      className={className}
    >
      {p.active ? <Active p={p} fsm={fsm} /> : <Ended p={p} />}
    </Section>
  );
}

function Ended({ p }: { p: PursuitState }) {
  return (
    <div className="px-2.5 py-2 text-[11.5px] leading-snug text-ink-3">
      Last pursuit ended: <span className="text-ink-2">{p.ended}</span>
      {p.route && <> · last route {p.route.candidate_id} ({p.route.label})</>}
      {p.hypotheses.length > 1 && <> · final weights {p.hypotheses.map((h) => `${h.id} ${pct(h.weight)}`).join(", ")}</>}
    </div>
  );
}

function Active({ p, fsm }: { p: PursuitState; fsm: string | null }) {
  const picked = p.route?.candidate_id ?? null;
  const shown = p.candidates.slice(0, 5);
  return (
    <div className="flex flex-col gap-2 px-2.5 py-2 text-[12px]">
      <div>
        <div className="mb-1 flex items-baseline justify-between text-[10.5px] font-semibold uppercase tracking-[0.1em] text-ink-3">
          <span>Where the subject could be</span>
          <span className="normal-case tracking-normal">{p.particles_per_hypothesis} particles per hypothesis</span>
        </div>
        <ul className="flex flex-col gap-1">
          {p.hypotheses.map((h, i) => <HypRow key={h.id} h={h} i={i} />)}
        </ul>
        {p.description && (
          <div className="mt-1 rounded bg-surface-2 px-2 py-1 text-[11px] leading-snug text-ink-2">
            <Badge color="var(--s4)">MANUAL</Badge> {p.description.by}: "{p.description.text}" → {p.description.parsed}
            <span className="text-ink-3"> · likelihood {Object.entries(p.description.likelihoods).map(([k, v]) => `${k} ×${v.toFixed(2)}`).join(", ")}</span>
          </div>
        )}
        {p.dropped.length > 0 && (
          <div className="mt-1 text-[10.5px] text-ink-3">Dropped: {p.dropped.map((d) => `${d.hid} (${d.why})`).join("; ")}</div>
        )}
      </div>
      <div>
        <div className="mb-1 text-[10.5px] font-semibold uppercase tracking-[0.1em] text-ink-3">Viewpoints, by belief covered on arrival</div>
        {shown.length === 0 ? (
          <p className="text-[11px] text-ink-3">No viewpoint yet.</p>
        ) : (
          <ul className="flex flex-col gap-1">
            {shown.map((c) => <CandRow key={c.id} c={c} hyps={p.hypotheses} picked={c.id === picked} />)}
          </ul>
        )}
      </div>
      {p.route && (
        <div className="rounded-md border border-line px-2 py-1.5 text-[11.5px] leading-snug">
          <div>
            <span className="font-semibold">{p.route.candidate_id === "hold" ? "Holding" : `Flying ${p.route.candidate_id}`}</span>
            <span className="text-ink-2"> · {p.route.label}</span>
          </div>
          <div className="text-[11px] text-ink-3">
            rules baseline {p.route.baseline_pick ?? "–"} · {p.route.engine === "rules" ? "answered by the rules (no model call)" : `model ${p.route.model_pick ?? "–"}${p.route.confidence != null ? ` (confidence ${p.route.confidence.toFixed(2)})` : ""}${p.route.acted ? "" : " · below the gate: the rules' pick is flown"}`}
          </div>
        </div>
      )}
      {(p.holding || fsm === "VERIFY") && (
        <p className="text-[11px] leading-snug text-[var(--warning)]">{p.holding ?? "Holding: a re-binding waits for a person"}</p>
      )}
      <p className="text-[10.5px] leading-snug text-ink-3">
        Particles move along the street map ({p.map_source}); where the camera looks and sees nobody they lose weight. Operator words count only as clothing evidence on the hypotheses. Simulator: real flight stays manual.
      </p>
    </div>
  );
}

function HypRow({ h, i }: { h: PursuitHypothesis; i: number }) {
  const prior = h.evidence.find((e) => e.kind === "prior");
  const desc = [...h.evidence].reverse().find((e) => e.kind === "description");
  const search = h.evidence.find((e) => e.kind === "search");
  return (
    <li className="flex items-start gap-2">
      <span className="mt-1 h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: hypColor(i) }} aria-hidden />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5">
          <span className="font-mono text-[11px] font-semibold">{h.id}</span>
          <span className="min-w-0 truncate text-ink-1">{h.label}</span>
          {h.visible && <Badge color="var(--good)">in view</Badge>}
          <span className="tnum ml-auto text-[11.5px] font-semibold">{pct(h.weight)}</span>
        </div>
        <div className="mt-0.5 h-1.5 overflow-hidden rounded-full bg-surface-3">
          <div className="h-full rounded-full" style={{ width: `${Math.max(2, 100 * h.weight)}%`, background: hypColor(i) }} />
        </div>
        <div className="mt-0.5 text-[10.5px] leading-snug text-ink-3">
          {h.colors ? `${h.colors[0]} top, ${h.colors[1]} lower` : "clothing unknown"}
          {prior?.why ? ` · ${prior.why}` : ""}
          {desc?.likelihood != null ? ` · description ×${desc.likelihood.toFixed(2)}` : ""}
          {search?.factor != null ? ` · looked, not seen ×${search.factor.toFixed(2)}` : ""}
        </div>
      </div>
    </li>
  );
}

function CandRow({ c, hyps, picked }: { c: PursuitCandidate; hyps: PursuitHypothesis[]; picked: boolean }) {
  return (
    <li className={`rounded px-1.5 py-1 ${picked ? "bg-surface-3" : ""} ${c.unsafe ? "opacity-60" : ""}`}>
      <div className="flex items-baseline gap-1.5">
        <span className="font-mono text-[11px] font-semibold">{c.id}</span>
        <span className={`min-w-0 flex-1 truncate ${c.unsafe ? "line-through" : ""}`}>{c.label}</span>
        <span className="tnum text-[11px] text-ink-3">{c.eta_s.toFixed(0)} s</span>
        <span className="tnum w-9 text-right text-[11.5px] font-semibold">{pct(c.coverage)}</span>
      </div>
      <div className="mt-0.5 flex h-1.5 overflow-hidden rounded-full bg-surface-3" title="share of the belief covered, by hypothesis">
        {hyps.map((h, i) => (
          <div key={h.id} className="h-full" style={{ width: `${100 * (c.by_hypothesis[h.id] ?? 0)}%`, background: hypColor(i) }} />
        ))}
      </div>
      {(c.unsafe || c.why || picked) && (
        <div className="mt-0.5 text-[10.5px] text-ink-3">
          {c.unsafe ? `removed by the safety supervisor: ${c.unsafe}` : c.why ?? ""}
          {picked && !c.unsafe ? `${c.why ? " · " : ""}score ${c.score?.toFixed(2)} (coverage − flight time${c.penalty ? " − safety" : ""})` : ""}
        </div>
      )}
    </li>
  );
}
