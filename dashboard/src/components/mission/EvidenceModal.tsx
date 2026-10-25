import { X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { api } from "../../lib/api";
import { useIncident } from "../../lib/incident";
import { Btn } from "../ui";
import { clockTime, evidenceUrl, locationText, ProvBadge, ReviewChip, sideOf } from "./common";

/** Everything known about one observation: the frame it came from, provenance, location quality, review history. */
export function EvidenceModal({ obsId, onClose }: { obsId: string; onClose: () => void }) {
  const v = useIncident((s) => s.observations.find((o) => o.observation.id === obsId));
  const events = useIncident((s) => s.events);
  const history = useMemo(
    () =>
      events.filter((e) => (e.kind === "review" && (e.payload as Record<string, unknown>).observation_id === obsId) ||
        (e.kind === "alert" && ((e.payload as Record<string, unknown>).source_observation_ids as string[] | undefined)?.includes(obsId))),
    [events, obsId],
  );
  const [loc, setLoc] = useState<{ verified: boolean; wgs84?: { lat: number; lon: number } | null; wgs84_reason?: string; origin?: string } | null>(null);
  useEffect(() => {
    api.get<typeof loc>(`/api/observations/${obsId}/location`).then(setLoc).catch(() => setLoc(null));
  }, [obsId]);
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);
  if (!v) return null;
  const o = v.observation;
  const img = evidenceUrl(o.evidence_frame_reference);
  const bb = o.bounding_box;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" role="dialog" aria-modal="true" aria-label="Observation evidence" onClick={onClose}>
      <div className="max-h-full w-full max-w-3xl overflow-y-auto rounded-xl border border-line-strong bg-surface p-4" onClick={(e) => e.stopPropagation()}>
        <div className="mb-2 flex items-center gap-2">
          <h3 className="text-[15px] font-bold text-ink-1">{o.label}</h3>
          <ProvBadge p={o.provenance} />
          <ReviewChip status={o.provenance === "manual" ? "manual" : v.review_status ?? "pending"} />
          <Btn tone="ghost" className="ml-auto" onClick={onClose} title="Close">
            <X size={14} />
          </Btn>
        </div>
        {img ? (
          <img src={img} alt="evidence frame with the detection box drawn" className="w-full rounded-md" />
        ) : (
          <div className="rounded-md bg-surface-2 p-6 text-center text-[12px] text-ink-3">No evidence image (manual or API observation).</div>
        )}
        <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-[12px]">
          <dt className="text-ink-3">Observed</dt>
          <dd className="text-ink-1">{clockTime(o.observed_at)} (received {clockTime(o.received_at)})</dd>
          <dt className="text-ink-3">Source</dt>
          <dd className="text-ink-1">{o.sensor_source_id} · {o.detector ?? "no detector"} {o.confidence != null ? `· confidence ${o.confidence.toFixed(2)}` : "· no calibrated confidence"}</dd>
          <dt className="text-ink-3">Location</dt>
          <dd className="text-ink-1">{locationText(v)}{o.world_position ? ` · (${o.world_position.x.toFixed(1)}, ${o.world_position.y.toFixed(1)}) m in ${o.coordinate_frame}` : ""}</dd>
          {loc?.verified && (
            <>
              <dt className="text-ink-3">WGS84</dt>
              <dd className="text-ink-1">{loc.wgs84 ? `${loc.wgs84.lat.toFixed(6)}, ${loc.wgs84.lon.toFixed(6)} (origin: ${loc.origin || "set"})` : loc.wgs84_reason}</dd>
            </>
          )}
          {bb && (
            <>
              <dt className="text-ink-3">Image box</dt>
              <dd className="font-mono text-[11px] text-ink-2">{bb.frame} [{bb.x0.toFixed(0)}, {bb.y0.toFixed(0)}, {bb.x1.toFixed(0)}, {bb.y1.toFixed(0)}] px</dd>
            </>
          )}
          {sideOf(v) && (
            <>
              <dt className="text-ink-3">Side</dt>
              <dd className="text-ink-1">{sideOf(v)}</dd>
            </>
          )}
          <dt className="text-ink-3">Track</dt>
          <dd className="font-mono text-[11px] text-ink-2">{o.track_id ?? "–"}</dd>
          <dt className="text-ink-3">Evidence</dt>
          <dd className="font-mono text-[11px] text-ink-2">{o.evidence_frame_reference ?? "–"}</dd>
        </dl>
        <h4 className="mt-3 text-[11px] font-bold uppercase tracking-wider text-ink-2">History</h4>
        <ul className="mt-1 text-[12px]">
          <li className="text-ink-2">{clockTime(o.received_at)} · observation recorded</li>
          {history.map((e) => {
            const p = e.payload as Record<string, unknown>;
            return (
              <li key={e.seq} className="text-ink-2">
                {clockTime(e.at)} · {e.kind === "review" ? `${String(p.status)} by ${String(p.reviewer)}` : `alert: ${String(p.message)}`}
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}
