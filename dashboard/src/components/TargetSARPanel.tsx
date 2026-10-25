import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { useStore } from "../lib/store";
import type { MapPath, MapPin } from "./FusedMap";
import { Btn } from "./ui";
import { evidenceUrl, Section } from "./mission/common";

interface Position { frame: string; x: number; y: number }
interface Subject {
  logical_subject_id: string; state: string; last_track_id: string | null;
  position_kind: string; confidence: number; uncertainty_m: number | null;
  measured_position: Position | null; predicted_position: Position | null;
  last_verified_position: Position | null; quality_reasons: string[];
  motion_history: { xy: [number, number] }[];
}
interface Search {
  request_id: string; status: string; reason: string; auto_approved: boolean; engine?: string;
  candidates: { track_id: string; well_supported: boolean; quality_reasons: string[] }[];
}
interface Target { session_id: string; state: string; subject: Subject | null; search: Search | null }
interface Guidance {
  callout: string; distance_m?: number; bearing_deg?: number; clock?: number | null;
  responder_position: Position | null;
  route: { status: string; points: [number, number][]; length_m?: number | null };
}
interface Assistance {
  observation: { id: string; evidence_frame_reference?: string | null; attributes: {
    sar: { logical_subject_id: string; score: { value: number; reasons: { code: string; text: string }[] } }
  }};
  review_status: string; guidance: Guidance;
  target_memory?: Subject | null;
  dispatch: { id: string; status: string; acknowledged_at?: string | null } | null;
}
interface SAR { items: Assistance[] }
export interface TargetSARData { target: Target | null; sar: SAR | null; error: string | null; refresh: () => Promise<void> }

export function useTargetSAR(iid?: string): TargetSARData {
  const [data, setData] = useState<{ target: Target | null; sar: SAR | null; error: string | null }>({ target: null, sar: null, error: null });
  const currentIncident = useRef(iid);
  currentIncident.current = iid;
  const refresh = useCallback(async () => {
    if (!iid) return;
    const [target, sar] = await Promise.all([api.get<Target>(`/api/incidents/${iid}/target`), api.get<SAR>(`/api/incidents/${iid}/sar`)]);
    if (currentIncident.current === iid) setData({ target, sar, error: null });
  }, [iid]);
  useEffect(() => {
    let stop = false;
    setData({ target: null, sar: null, error: null });
    const poll = async () => {
      if (!iid) return;
      try {
        const [target, sar] = await Promise.all([api.get<Target>(`/api/incidents/${iid}/target`), api.get<SAR>(`/api/incidents/${iid}/sar`)]);
        if (!stop) setData({ target, sar, error: null });
      } catch (e) { if (!stop) setData((s) => ({ ...s, error: (e as Error).message })); }
    };
    void poll();
    const timer = window.setInterval(poll, 750);
    return () => { stop = true; window.clearInterval(timer); };
  }, [iid]);
  return { ...data, refresh };
}

export function targetPaths(data: TargetSARData): MapPath[] {
  if (data.error) return [];
  const subject = data.target?.subject;
  const paths: MapPath[] = subject?.motion_history.length ? [{ id: "persistent-subject", points: subject.motion_history.map((p) => p.xy), color: "#79c6f2" }] : [];
  if (subject?.predicted_position && subject.last_verified_position) {
    paths.push({ id: "subject-prediction", dashed: true, color: "#f0b75f", points: [
      [subject.last_verified_position.x, subject.last_verified_position.y], [subject.predicted_position.x, subject.predicted_position.y],
    ] });
  }
  for (const item of data.sar?.items ?? []) {
    const trail = item.target_memory?.motion_history ?? [];
    if (trail.length) paths.push({ id: item.observation.id + "-trail", points: trail.map((p) => p.xy), color: "#79c6f2" });
    if (item.review_status === "confirmed" && item.guidance.route.points.length) paths.push({
      id: item.observation.id + "-access", points: item.guidance.route.points, color: "#73d6a0",
      dashed: item.guidance.route.status === "approach_only",
    });
  }
  return paths;
}

export function targetPins(data: TargetSARData): MapPin[] {
  if (data.error) return [];
  const pins: MapPin[] = [];
  const subject = data.target?.subject;
  if (subject?.predicted_position) pins.push({ id: "subject-prediction", x: subject.predicted_position.x,
    y: subject.predicted_position.y, status: "pending", label: "Predicted subject · not a fresh measurement", uncertainty: subject.uncertainty_m });
  const responder = data.sar?.items.find((i) => i.guidance.responder_position)?.guidance.responder_position;
  if (responder) pins.push({ id: "sar-responder", x: responder.x, y: responder.y, status: "manual", label: "Responder · simulated GPS" });
  for (const item of data.sar?.items ?? []) {
    const memory = item.target_memory;
    const position = memory?.measured_position ?? memory?.predicted_position;
    if (position && memory) pins.push({ id: item.observation.id + "-current", x: position.x, y: position.y,
      status: memory.position_kind === "predicted" ? "pending" : item.review_status as MapPin["status"],
      label: `Assistance subject · ${memory.state} · ${memory.position_kind}`, uncertainty: memory.uncertainty_m });
  }
  return pins;
}

export function TargetSARPanel({ iid, data }: { iid: string; data: TargetSARData }) {
  const [description, setDescription] = useState("Find the person wearing a red shirt.");
  const [busy, setBusy] = useState(false);
  const showToast = useStore((s) => s.showToast);
  const subject = data.target?.subject;
  const request = data.target?.search;
  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    try { await fn(); await data.refresh(); }
    catch (e) { showToast((e as Error).message, "error"); }
    finally { setBusy(false); }
  };
  const review = (item: Assistance, status: "confirmed" | "rejected", dispatch = false) => run(() =>
    api.post(`/api/incidents/${iid}/sar/${item.observation.id}/review`, {
      status, dispatch, reviewer: "dashboard operator", session_id: data.target?.session_id,
    }));
  return <>
    <Section title="Persistent target" className="shrink-0">
      <div className="space-y-2 p-2.5 text-[12px]">
        <form className="flex gap-1" onSubmit={(e) => { e.preventDefault(); void run(() => api.post(`/api/incidents/${iid}/target/search`, { description })); }}>
          <input aria-label="Continuous clothing search" value={description} onChange={(e) => setDescription(e.target.value)} className="min-w-0 flex-1 rounded border border-line bg-surface-2 px-2 py-1" />
          <button type="submit" disabled={busy} className="rounded border border-line px-2 py-1 disabled:opacity-50">Search</button>
        </form>
        <p data-testid="target-state">{data.target?.state ?? "SEARCHING"}{subject ? ` · ${subject.logical_subject_id.split(".").slice(-2).join(".")}` : ""}</p>
        {data.error && <p className="text-[var(--warning)]">Updates unavailable; last received state: {data.error}</p>}
        {subject && <div className="space-y-1 text-ink-2">
          <p>{subject.position_kind} position · continuity score {subject.confidence.toFixed(2)}{subject.uncertainty_m !== null ? ` · ±${subject.uncertainty_m.toFixed(1)} m` : " · unlocalized"}</p>
          <p>Last track: {subject.last_track_id ?? "unknown"} · {subject.motion_history.length} measured trail points</p>
          {!!subject.quality_reasons.length && <p>{subject.quality_reasons.join("; ")}</p>}
        </div>}
        {request && <>
          <p>{request.engine === "muse" ? "Muse Spark" : "Clothing rules · fallback"} · {request.reason}</p>
          {request.auto_approved && <p className="text-ink-3">Simulation selection; this is not human identity confirmation.</p>}
          {request.status === "review" && request.candidates.map((candidate) => <div className="flex items-center justify-between gap-1" key={candidate.track_id}>
            <span>{candidate.track_id}{!candidate.well_supported ? " · waiting for clean measurements" : ""}</span>
            <Btn disabled={busy || !candidate.well_supported} onClick={() => void run(() => api.post(`/api/incidents/${iid}/target/confirm`, { request_id: request.request_id, track_id: candidate.track_id }))}>Confirm candidate</Btn>
          </div>)}
          <Btn disabled={busy} onClick={() => void run(() => api.post(`/api/incidents/${iid}/person-mark/release`))}>Release search / subject</Btn>
        </>}
        <p className="text-[10.5px] text-ink-3">Dashed prediction is not a fresh location. Physical arming and Start Follow remain separate.</p>
      </div>
    </Section>
    {!!data.sar?.items.length && <Section title="Possibly needs assistance · SIMULATED" className="shrink-0">
      <ul className="divide-y divide-[var(--border)]">{data.sar.items.map((item) => {
        const score = item.observation.attributes.sar.score;
        const evidence = evidenceUrl(item.observation.evidence_frame_reference);
        return <li key={item.observation.id} className="space-y-2 p-2.5 text-[12px]" data-testid="assistance-card">
          <p><strong>Possible assistance candidate</strong> · score {score.value} · {item.review_status}</p>
          {item.target_memory && <p className="text-ink-2">{item.target_memory.state} · {item.target_memory.position_kind} position
            {item.target_memory.uncertainty_m !== null ? ` · ±${item.target_memory.uncertainty_m.toFixed(1)} m` : ""}
            {` · subject ${item.target_memory.logical_subject_id.split(".").slice(-2).join(".")}`}</p>}
          <p>{score.reasons.map((r) => r.text).join("; ")}</p>
          {evidence && <a href={evidence} target="_blank" rel="noreferrer" className="underline">View observed evidence</a>}
          <p data-testid="assistance-guidance">{item.guidance.callout}</p>
          <div className="flex flex-wrap gap-1">
            {item.review_status === "pending" && <>
              <Btn disabled={busy} tone="primary" onClick={() => void review(item, "confirmed", true)}>Confirm &amp; send guidance</Btn>
              <Btn disabled={busy} onClick={() => void review(item, "rejected")}>Reject candidate</Btn>
            </>}
            {item.review_status === "confirmed" && !item.dispatch && <Btn disabled={busy} onClick={() => void review(item, "confirmed", true)}>Send guidance</Btn>}
            {item.dispatch && <>
              <span>Dispatch: {item.dispatch.status}</span>
              {!item.dispatch.acknowledged_at && <Btn disabled={busy} onClick={() => void run(() => api.post(`/api/alerts/${item.dispatch!.id}/acknowledge`, { by: "dashboard responder" }))}>Acknowledge guidance</Btn>}
            </>}
          </div>
        </li>;
      })}</ul>
    </Section>}
  </>;
}
