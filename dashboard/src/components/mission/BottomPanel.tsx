import { History, Pause, Play, Radio } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { withToken } from "../../lib/api";
import { actions, useIncident } from "../../lib/incident";
import { EMPTY, useStore } from "../../lib/store";
import type { IncidentEvent } from "../../types/incident";
import { Badge, Btn } from "../ui";
import { clockTime } from "./common";

const KIND_COLOR: Record<string, string> = {
  observation: "var(--warning)",
  review: "var(--good)",
  alert: "var(--serious)",
  alert_acknowledged: "var(--s1)",
  alert_delivered: "var(--s1)",
  mission_changed: "var(--s7)",
  source_changed: "var(--s4)",
  source_updated: "var(--s4)",
  anchor: "var(--s3)",
  decision: "var(--s7)",
  decision_proposal: "var(--s7)",
  error: "var(--critical)",
};
const OPERATOR_KINDS = new Set(["incident_created", "observation", "review", "mission_changed", "alert", "alert_acknowledged",
  "source_changed", "anchor", "replay_control", "decision_proposal", "error", "track_lost", "track_started"]);

function summary(e: IncidentEvent): string {
  const p = e.payload as Record<string, unknown>;
  switch (e.kind) {
    case "observation": {
      const o = p.observation as Record<string, unknown>;
      return `${String(o.label)} (${String(o.provenance)})`;
    }
    case "review":
      return `${String(p.status)} by ${String(p.reviewer)}`;
    case "mission_changed":
      return `${String(p.from)} → ${String(p.to)}`;
    case "alert":
      return String(p.message);
    case "alert_acknowledged":
      return `acknowledged by ${String(p.by)}`;
    case "source_changed":
      return `${String(p.from)} → ${String(p.to)} (${String(p.status)})`;
    case "pipeline_event":
      return `${String(p.kind)}: ${String(p.text ?? "")}`;
    case "decision":
      return `${String(p.task)} · ${String(p.engine)} · ${String(p.selected ?? p.status)}${p.acted ? " · acted" : ""}`;
    case "decision_proposal":
      return `${String(p.proposed_by)}: ${String((p.parameters as Record<string, unknown> | undefined)?.message ?? p.action_type)}`;
    case "proposal_rejected":
      return `${String(p.action_type)} rejected (${String(p.code)}): ${String(p.reason)}`;
    case "action_approved":
    case "action_rejected":
    case "action_done":
      return `${String(p.action_id)} · ${String(p.approval)} · ${String(p.execution)}`;
    case "sensor_status":
      return `${String(p.source)}: ${String(p.from ?? "start")} → ${String(p.to)}`;
    case "operator_command":
      return `${String(p.path)} (${String(p.status_code)})${p.override ? " · override" : ""}`;
    case "track_started":
    case "track_lost":
    case "track_reacquired":
    case "track_ended":
    case "track_designated":
      return `${String(p.track_id)} · ${String(p.status)} · identity ${String(p.identity)}`;
    case "error":
      return String(p.message);
    case "incident_created":
      return String(p.title);
    default:
      return Object.keys(p).slice(0, 4).join(", ");
  }
}

export function BottomPanel() {
  const [tab, setTab] = useState<"timeline" | "audit" | "health" | "metrics" | "calibration">("timeline");
  return (
    <section className="flex h-full min-h-0 flex-col rounded-lg border border-line bg-surface">
      <header className="flex h-8 shrink-0 items-center gap-1 border-b border-line px-2" role="tablist">
        {(["timeline", "audit", "health", "metrics", "calibration"] as const).map((t) => (
          <button
            key={t}
            type="button"
            role="tab"
            aria-selected={tab === t}
            onClick={() => setTab(t)}
            className={`h-6 rounded px-2 text-[11px] font-bold uppercase tracking-wider ${tab === t ? "bg-surface-3 text-ink-1" : "text-ink-3 hover:text-ink-1"}`}
          >
            {t === "health" ? "source health" : t === "audit" ? "audit log" : t}
          </button>
        ))}
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {tab === "timeline" ? <Timeline /> : tab === "audit" ? <Audit /> : tab === "health" ? <Health /> : tab === "metrics" ? <MetricsView /> : <Calibration />}
      </div>
    </section>
  );
}

function Timeline() {
  const events = useIncident((s) => s.events);
  const cursor = useIncident((s) => s.cursorSeq);
  const setCursor = useIncident((s) => s.setCursor);
  const [playing, setPlaying] = useState(false);
  const shown = useMemo(() => events.filter((e) => e.kind !== "pipeline_event" && e.kind !== "decision"), [events]);
  const timer = useRef<number | null>(null);
  const t0 = shown.length ? new Date(shown[0].at!).getTime() : 0;
  const t1 = shown.length ? new Date(shown[shown.length - 1].at!).getTime() : 1;
  const span = Math.max(1, t1 - t0);

  useEffect(() => {
    if (!playing) return;
    let i = cursor === null ? 0 : Math.max(0, shown.findIndex((e) => e.seq! > cursor));
    const step = () => {
      if (i >= shown.length) {
        setPlaying(false);
        return;
      }
      setCursor(shown[i].seq!);
      i += 1;
      timer.current = window.setTimeout(step, 450);
    };
    step();
    return () => {
      if (timer.current) window.clearTimeout(timer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playing]);

  const at = cursor !== null ? shown.find((e) => e.seq === cursor) ?? null : null;
  return (
    <div className="flex h-full flex-col gap-1.5 p-2">
      <div className="flex items-center gap-1.5">
        <Btn tone={playing ? "default" : "primary"} disabled={shown.length === 0} onClick={() => setPlaying(!playing)} title="Replay the incident timeline">
          {playing ? <Pause size={13} /> : <History size={13} />} {playing ? "Pause replay" : "Replay timeline"}
        </Btn>
        <Btn tone={cursor === null ? "default" : "primary"} onClick={() => { setPlaying(false); setCursor(null); }} title="Back to live">
          <Radio size={13} /> Live
        </Btn>
        <span className="truncate text-[12px] text-ink-2">
          {at ? `${clockTime(at.at)} · ${at.kind.replaceAll("_", " ")} · ${summary(at)}` : `${shown.length} incident events · live`}
        </span>
      </div>
      <div className="relative h-10 rounded-md bg-surface-2" aria-label="Incident timeline">
        {shown.map((e) => {
          const x = ((new Date(e.at!).getTime() - t0) / span) * 100;
          const past = cursor === null || e.seq! <= cursor;
          return (
            <button
              key={e.seq}
              type="button"
              title={`${clockTime(e.at)} · ${e.kind} · ${summary(e)}`}
              onClick={() => { setPlaying(false); setCursor(e.seq!); }}
              className="absolute top-1.5 h-7 w-[5px] -translate-x-1/2 rounded-sm"
              style={{ left: `${Math.min(99.5, Math.max(0.5, x))}%`, background: KIND_COLOR[e.kind] ?? "var(--ink-3)", opacity: past ? 1 : 0.25,
                outline: e.seq === cursor ? "2px solid var(--ink-1)" : undefined }}
            />
          );
        })}
      </div>
      <div className="flex flex-wrap gap-2 text-[10.5px] text-ink-3">
        {["observation", "review", "alert", "alert_acknowledged", "mission_changed", "source_changed"].map((k) => (
          <span key={k} className="inline-flex items-center gap-1">
            <span className="inline-block h-2 w-2 rounded-sm" style={{ background: KIND_COLOR[k] }} />
            {k.replaceAll("_", " ")}
          </span>
        ))}
        {!playing && <Play size={10} className="hidden" />}
      </div>
    </div>
  );
}

function Audit() {
  const events = useIncident((s) => s.events);
  const [all, setAll] = useState(false);
  const rows = [...events].filter((e) => all || OPERATOR_KINDS.has(e.kind)).reverse().slice(0, 300);
  return (
    <div>
      <label className="flex items-center gap-1.5 px-2 py-1 text-[11px] text-ink-3">
        <input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} /> include pipeline events and decisions
      </label>
      <table className="w-full text-[11.5px]">
        <tbody>
          {rows.map((e) => (
            <tr key={e.seq} className="border-t border-line">
              <td className="tnum w-[70px] px-2 py-0.5 text-ink-3">{clockTime(e.at)}</td>
              <td className="w-[150px] px-1 py-0.5" style={{ color: KIND_COLOR[e.kind] ?? "var(--ink-2)" }}>{e.kind.replaceAll("_", " ")}</td>
              <td className="truncate px-1 py-0.5 text-ink-1">{summary(e)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Near-IR enhancement on a recorded frame: raw next to processed, labelled image processing (never thermal). */
function NearIRPreview() {
  const [url, setUrl] = useState<string | null>(null);
  const [steps, setSteps] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const send = async (f: File) => {
    setErr(null);
    const res = await fetch(withToken("/api/near-ir/preview"), { method: "POST", body: f, headers: { "Content-Type": "application/octet-stream" } });
    if (!res.ok) {
      setErr(`preview failed (${res.status})`);
      return;
    }
    setSteps(res.headers.get("X-Image-Processing") ?? "");
    setUrl((old) => {
      if (old) URL.revokeObjectURL(old);
      return null;
    });
    setUrl(URL.createObjectURL(await res.blob()));
  };
  return (
    <div className="border-t border-line p-2 text-[11.5px]">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-semibold text-ink-1">Near-IR enhancement preview</span>
        <span className="text-ink-3">near-IR is reflected light, not heat: no temperatures</span>
        <Btn className="ml-auto" onClick={() => fileRef.current?.click()}>Choose a frame…</Btn>
        <input ref={fileRef} type="file" accept="image/*" className="hidden" onChange={(e) => { const f = e.target.files?.[0]; if (f) void send(f); e.target.value = ""; }} />
      </div>
      {err && <p className="mt-1 text-[var(--serious)]">{err}</p>}
      {url && (
        <figure className="mt-1.5">
          <img src={url} alt="Raw near-IR next to the processed frame (image processing)" className="w-full rounded border border-line" />
          <figcaption className="mt-0.5 text-ink-3">image processing: {steps || "none"}</figcaption>
        </figure>
      )}
    </div>
  );
}

function Health() {
  const sources = useStore((s) => s.state?.sources ?? EMPTY);
  return (
    <div>
    <table className="w-full text-[11.5px]">
      <thead>
        <tr className="text-left text-ink-3">
          <th className="px-2 py-1">source</th><th>status</th><th>data</th><th>age</th><th>fps</th><th>dropped</th><th>reconnects</th><th>detail</th>
        </tr>
      </thead>
      <tbody>
        {sources.map((s) => (
          <tr key={s.source_id} className="border-t border-line">
            <td className="px-2 py-0.5 text-ink-1">{s.source_id}</td>
            <td><Badge color={s.status === "OK" ? "var(--good)" : s.status === "NOT_CONFIGURED" ? "var(--ink-3)" : "var(--warning)"}>{s.status}</Badge></td>
            <td className="text-ink-2">{s.provenance ?? "–"}</td>
            <td className="tnum text-ink-2">{s.last_age_ms != null ? `${Math.round(s.last_age_ms)} ms` : "–"}</td>
            <td className="tnum text-ink-2">{s.fps ?? "–"}</td>
            <td className="tnum text-ink-2">{s.dropped}</td>
            <td className="tnum text-ink-2">{s.reconnects}</td>
            <td className="truncate text-ink-3">{s.detail ?? ""}</td>
          </tr>
        ))}
      </tbody>
    </table>
    <NearIRPreview />
    </div>
  );
}

function MetricsView() {
  const m = useIncident((s) => s.metrics);
  if (!m) return <p className="p-3 text-[12px] text-ink-3">No incident metrics yet.</p>;
  const d = (x: { n: number; median_s: number | null }) => (x.median_s == null ? "–" : `${x.median_s.toFixed(1)} s (n=${x.n})`);
  const src = m.sources ?? {};
  const loc = m.localization_error;
  const cells: [string, string, string][] = [
    ["candidates", String(m.candidates.total), src.candidates],
    ["confirmed", String(m.candidates.confirmed), src.candidates],
    ["rejected", String(m.candidates.rejected), src.candidates],
    ["pending", String(m.candidates.pending), src.candidates],
    ["detection → review", d(m.detection_to_review), src.detection_to_review],
    ["confirmation → alert", d(m.confirmation_to_alert), src.confirmation_to_alert],
    ["alert acknowledgment", d(m.alert_ack_latency), src.alert_ack_latency],
    ["with verified position", String(m.observations.with_valid_world_position), src.observations],
    ["without position", String(m.observations.without_world_position), src.observations],
    ["track losses", String(m.tracks.loss_events), src.tracks],
    ["localization error", loc ? `${loc.median_m.toFixed(1)} m (n=${loc.n})` : "no ground truth", loc?.source ?? m.localization_error_note ?? ""],
  ];
  return (
    <div className="p-2">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-6">
        {cells.map(([k, v, from]) => (
          <div key={k} className="rounded-md bg-surface-2 px-2 py-1" title={from ? `Source: ${from}` : undefined}>
            <div className="text-[10px] uppercase tracking-wider text-ink-3">{k}</div>
            <div className="tnum text-[15px] font-semibold text-ink-1">{v}</div>
          </div>
        ))}
      </div>
      <p className="mt-1.5 text-[10.5px] text-ink-3">Computed from {m.computed_from} (hover a value for its source). Localization error is only reported where simulator ground truth exists.</p>
    </div>
  );
}

function Calibration() {
  const cal = useStore((s) => s.state?.calibration ?? null);
  const incident = useIncident((s) => s.incident);
  const gcpMode = useIncident((s) => s.gcpMode);
  const pairs = useIncident((s) => s.gcpPairs);
  const showToast = useStore((s) => s.showToast);
  const ref = incident?.coordinate_reference;
  const [lat, setLat] = useState("");
  const [lon, setLon] = useState("");
  const run = async (f: () => Promise<unknown>, ok?: string) => {
    try {
      await f();
      if (ok) showToast(ok);
    } catch (e) {
      showToast(String((e as Error).message), "error");
    }
  };
  return (
    <div className="grid gap-2 p-2 text-[12px] md:grid-cols-3">
      <div className="rounded-md bg-surface-2 p-2">
        <div className="mb-1 text-[10px] font-bold uppercase tracking-wider text-ink-3">Image → ground transform</div>
        {cal ? (
          <>
            <Badge color={cal.valid ? "var(--good)" : "var(--warning)"}>{cal.valid ? `valid · ${cal.source}` : "not valid"}</Badge>
            <div className="mt-1 text-ink-2">{cal.reason}</div>
            <div className="tnum text-ink-3">
              error bound {cal.error_bound_m != null ? `${cal.error_bound_m.toFixed(2)} m` : "–"} · holdout RMSE{" "}
              {cal.holdout_rmse_m != null ? `${cal.holdout_rmse_m.toFixed(2)} m` : "–"} · markers {cal.markers_used}/{cal.markers_detected}
            </div>
          </>
        ) : (
          <span className="text-ink-3">no drone frame yet</span>
        )}
        <p className="mt-1 text-[10.5px] text-ink-3">Without a valid transform, detections keep no world position ("location not yet verified").</p>
      </div>
      <div className="rounded-md bg-surface-2 p-2">
        <div className="mb-1 text-[10px] font-bold uppercase tracking-wider text-ink-3">Known landmarks (manual ground points)</div>
        <div className="flex flex-wrap gap-1">
          <Btn tone={gcpMode ? "primary" : "default"} onClick={() => useIncident.setState({ gcpMode: gcpMode ? null : "image", gcpPending: null })}>
            {gcpMode === "image" ? "Click the video…" : gcpMode === "map" ? "Click the map…" : "Add ground point"}
          </Btn>
          <Btn disabled={pairs.length < 4} onClick={() => run(() => actions.fitGround(pairs), "Ground transform fitted from landmarks")} title="Needs at least 4 points; every 4th is held out to measure error">
            Fit ({pairs.length})
          </Btn>
          <Btn tone="ghost" disabled={pairs.length === 0} onClick={() => useIncident.setState({ gcpPairs: [] })}>Clear</Btn>
        </div>
        <ul className="mt-1 max-h-16 overflow-y-auto font-mono text-[10.5px] text-ink-2">
          {pairs.map((p, i) => (
            <li key={i}>
              {i + 1}. ({p.u.toFixed(0)}, {p.v.toFixed(0)}) px → ({p.x.toFixed(1)}, {p.y.toFixed(1)}) m{pairs.length >= 5 && i % 4 === 3 ? " · holdout" : ""}
            </li>
          ))}
        </ul>
      </div>
      <div className="rounded-md bg-surface-2 p-2">
        <div className="mb-1 text-[10px] font-bold uppercase tracking-wider text-ink-3">Geographic origin (WGS84)</div>
        <div className="text-ink-2">
          {ref?.origin_lat != null ? `${ref.origin_lat.toFixed(5)}, ${ref.origin_lon?.toFixed(5)}` : "none: GPS cannot be placed on the map"}
        </div>
        <div className="text-[10.5px] text-ink-3">{ref?.note || ""}</div>
        <div className="mt-1 flex gap-1">
          <input aria-label="Origin latitude" placeholder="lat" value={lat} onChange={(e) => setLat(e.target.value)} className="h-7 w-24 rounded border border-line bg-surface px-1.5 text-[12px]" />
          <input aria-label="Origin longitude" placeholder="lon" value={lon} onChange={(e) => setLon(e.target.value)} className="h-7 w-24 rounded border border-line bg-surface px-1.5 text-[12px]" />
          <Btn disabled={!incident || !lat || !lon} onClick={() => run(() => actions.setGeoref(Number(lat), Number(lon), "surveyed by the operator"), "Origin set")}>Set</Btn>
        </div>
      </div>
    </div>
  );
}
