import { Bell, Check, Eye, Lightbulb, MapPin as MapPinIcon, Megaphone, X } from "lucide-react";
import { useMemo, useState } from "react";
import { actions, asOf, useIncident } from "../../lib/incident";
import { useStore } from "../../lib/store";
import type { ObservationView } from "../../types/incident";
import { FusedMap, type MapPath, type MapPin } from "../FusedMap";
import { PursuitCard } from "../PursuitCard";
import { VoicePanel } from "../VoicePanel";
import { Badge, Btn } from "../ui";
import { ago, briefingFor, evidenceUrl, locationText, ProvBadge, ReviewChip, Section, sideOf } from "./common";
import { EvidenceModal } from "./EvidenceModal";
import { KeyboardCamera } from "./KeyboardCamera";
import { PersonMark } from "../PersonMark";
import { TargetSARPanel, useTargetSAR, targetPaths, targetPins } from "../TargetSARPanel";
import { BridgeSetup } from "./BridgeSetup";

const SEV: Record<string, string> = { info: "var(--ink-2)", attention: "var(--warning)", warning: "var(--serious)", critical: "var(--critical)" };

export function RightRail() {
  const incident = useIncident((s) => s.incident);
  const all = useIncident((s) => s.observations);
  const events = useIncident((s) => s.events);
  const cursor = useIncident((s) => s.cursorSeq);
  const alerts = useIncident((s) => s.alerts);
  const selected = useIncident((s) => s.selectedObs);
  const placing = useIncident((s) => s.placing);
  const gcpMode = useIncident((s) => s.gcpMode);
  const showToast = useStore((s) => s.showToast);
  const [evidence, setEvidence] = useState<string | null>(null);
  const [markerLabel, setMarkerLabel] = useState("Manual marker");
  const [markerCat, setMarkerCat] = useState("other");
  const obs = useMemo(() => asOf(all, events, cursor), [all, events, cursor]);
  const live = useStore((s) => s.state?.mode === "LIVE");
  const mission = incident?.mission_mode ?? "search";
  const follow = useIncident((s) => s.follow);
  const targetSAR = useTargetSAR(incident?.id);
  const paths = [...followPaths(follow), ...targetPaths(targetSAR)];

  const run = async (f: () => Promise<unknown>, ok?: string) => {
    try {
      await f();
      if (ok) showToast(ok);
    } catch (e) {
      showToast(String((e as Error).message), "error");
    }
  };


  const pins: MapPin[] = obs
    .filter((v) => v.observation.world_position)
    .map((v) => ({
      id: v.observation.id!,
      x: v.observation.world_position!.x,
      y: v.observation.world_position!.y,
      status: v.observation.provenance === "manual" ? "manual" : v.review_status ?? "pending",
      label: v.observation.label,
      uncertainty: v.observation.horizontal_uncertainty_m,
      selected: v.observation.id === selected,
    }));

  pins.push(...targetPins(targetSAR));

  const groupCount: Record<string, number> = {};
  for (const v of obs) {
    const g = (v.observation.attributes as Record<string, unknown> | undefined)?.group;
    if (typeof g === "string") groupCount[g] = (groupCount[g] ?? 0) + 1;
  }
  const queue = obs
    .filter((v) => v.observation.provenance !== "manual" && !(v.observation.attributes as Record<string, unknown> | undefined)?.group)
    .sort((a, b) => (a.review_status === "pending" ? -1 : 0) - (b.review_status === "pending" ? -1 : 0) || (b.observation.observed_at ?? "").localeCompare(a.observation.observed_at ?? ""));
  const pendingN = queue.filter((v) => v.review_status === "pending").length;
  const sel = obs.find((v) => v.observation.id === selected) ?? null;
  const unacked = alerts.filter((a) => !a.acknowledged_at).length;

  return (
    <div className="flex min-h-0 flex-col gap-2">
      {live && <BridgeSetup />}
      {incident?.id && <TargetSARPanel iid={incident.id} data={targetSAR} />}
      <Section
        title={<span className="flex items-center gap-1.5"><MapPinIcon size={12} /> Incident map</span>}
        className="h-[300px] shrink-0 xl:h-[38%]"
        right={
          <>
            {cursor !== null && <Badge color="var(--s1)">replay view</Badge>}
            <Btn tone={placing ? "primary" : "default"} disabled={!incident} onClick={() => useIncident.getState().setPlacing(!placing)} title="Click the map to place a MANUAL marker">
              {placing ? "Click map…" : "+ Marker"}
            </Btn>
          </>
        }
      >
        <div className="relative h-full min-h-[200px]">
          <FusedMap
            pins={pins}
            paths={paths}
            placing={placing || gcpMode === "map"}
            onPinClick={(id) => useIncident.getState().setSelectedObs(id)}
            onPlace={(x, y) => {
              const g = useIncident.getState();
              if (g.gcpMode === "map" && g.gcpPending) {
                useIncident.setState({ gcpPairs: [...g.gcpPairs, { u: g.gcpPending[0], v: g.gcpPending[1], x, y }], gcpPending: null, gcpMode: null });
                showToast(`Ground point ${g.gcpPairs.length + 1} paired at ${x.toFixed(1)}, ${y.toFixed(1)} m`);
                return;
              }
              useIncident.getState().setPlacing(false);
              void run(() => actions.addManualMarker(x, y, markerLabel, markerCat), `MANUAL marker placed at ${x.toFixed(1)}, ${y.toFixed(1)} m`);
            }}
          />
          {placing && (
            <div className="absolute bottom-2 left-2 z-10 flex gap-1 rounded-md bg-[var(--overlay)] p-1 backdrop-blur">
              <select aria-label="Marker kind" value={markerCat} onChange={(e) => setMarkerCat(e.target.value)} className="h-7 rounded border border-line bg-surface-2 px-1 text-[12px]">
                <option value="other">marker</option>
                <option value="person">person</option>
                <option value="hazard">hazard</option>
                <option value="supply">supply</option>
                <option value="opening">entrance</option>
              </select>
              <input aria-label="Marker label" value={markerLabel} onChange={(e) => setMarkerLabel(e.target.value)} className="h-7 w-36 rounded border border-line bg-surface-2 px-1.5 text-[12px]" />
            </div>
          )}
        </div>
      </Section>

      <Section title={`Review queue · ${pendingN} pending`} className="min-h-[180px] flex-1">
        {queue.length === 0 ? (
          <p className="p-3 text-[12px] text-ink-3">No candidates yet. Select a box in the drone view and flag it, or wait for Search to flag one.</p>
        ) : (
          <ul className="divide-y divide-[var(--border)]">
            {queue.map((v) => (
              <ObsRow key={v.observation.id} v={v} resightings={groupCount[v.observation.id!] ?? 0} selected={v.observation.id === selected} onEvidence={setEvidence} run={run} />
            ))}
          </ul>
        )}
      </Section>

      {mission === "follow" && <FollowPanel run={run} />}

      <PursuitCard className="shrink-0" />

      <VoicePanel className="shrink-0" />

      <KeyboardCamera />

      <BridgeSetup />

      {incident?.id && <PersonMark incidentId={incident.id} />}

      <RecommendationsPanel run={run} />

      {mission === "structure" && <StructurePanel run={run} />}

      <Section title={<span className="flex items-center gap-1.5"><Megaphone size={12} /> Brief responder</span>} className="shrink-0">
        {sel ? (
          <div className="flex flex-col gap-1.5 p-2.5">
            <div className="text-[11px] text-ink-3">Message (built from computed fields only)</div>
            <div className="rounded-md bg-surface-2 p-2 text-[12.5px] leading-snug text-ink-1">{briefingFor(sel).message}</div>
            <div className="flex items-center gap-1.5">
              <ProvBadge p={sel.observation.provenance} />
              <ReviewChip status={sel.observation.provenance === "manual" ? "manual" : sel.review_status ?? "pending"} />
              <Btn tone="primary" className="ml-auto" disabled={!incident} onClick={() => run(() => actions.brief(briefingFor(sel).message, [sel.observation.id!], briefingFor(sel).severity), "Briefing sent to the responder")}>
                <Megaphone size={13} /> Send
              </Btn>
            </div>
          </div>
        ) : (
          <p className="p-2.5 text-[12px] text-ink-3">Select an observation (queue or map) to brief the responder.</p>
        )}
      </Section>

      <Section title={<span className="flex items-center gap-1.5"><Bell size={12} /> Alerts · {unacked} unacknowledged</span>} className="h-[170px] shrink-0">
        {alerts.length === 0 ? (
          <p className="p-3 text-[12px] text-ink-3">No alerts yet.</p>
        ) : (
          <ul className="divide-y divide-[var(--border)]">
            {[...alerts].reverse().slice(0, 40).map((a) => (
              <li key={a.id} className="flex items-start gap-2 px-2.5 py-1.5 text-[12px]">
                <span className="mt-0.5"><Badge color={SEV[a.severity] ?? "var(--ink-2)"}>{a.severity}</Badge></span>
                <div className="min-w-0 flex-1">
                  <div className="text-ink-1">{a.message}</div>
                  <div className="text-[10.5px] text-ink-3">
                    {ago(a.created_at)} · {a.acknowledged_at ? `acknowledged by ${a.acknowledged_by ?? "?"} ${ago(a.acknowledged_at)}` : a.status}
                  </div>
                </div>
                {!a.acknowledged_at && (
                  <Btn tone="ghost" title="Acknowledge" onClick={() => run(() => actions.ack(a.id!))}>
                    <Check size={13} /> Ack
                  </Btn>
                )}
              </li>
            ))}
          </ul>
        )}
      </Section>
      {evidence && <EvidenceModal obsId={evidence} onClose={() => setEvidence(null)} />}
    </div>
  );
}

function ObsRow({ v, selected, onEvidence, run, resightings }: { v: ObservationView; selected: boolean; resightings: number; onEvidence: (id: string) => void; run: (f: () => Promise<unknown>, ok?: string) => void }) {
  const o = v.observation;
  const img = evidenceUrl(o.evidence_frame_reference);
  return (
    <li
      className={`flex cursor-pointer gap-2 px-2.5 py-2 ${selected ? "bg-surface-3" : "hover:bg-surface-2"}`}
      onClick={() => useIncident.getState().setSelectedObs(o.id!)}
    >
      {img ? (
        <img src={img} alt="evidence frame" className="h-12 w-16 shrink-0 rounded object-cover" loading="lazy" />
      ) : (
        <div className="flex h-12 w-16 shrink-0 items-center justify-center rounded bg-surface-2 text-[10px] text-ink-3">no image</div>
      )}
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-1">
          <span className="text-[12.5px] font-semibold text-ink-1">{o.label}</span>
          {sideOf(v) && <Badge color="var(--s1)">Side {sideOf(v)}</Badge>}
          {resightings > 0 && <Badge color="var(--ink-2)" title="later sightings within the position uncertainty">+{resightings} re-sighting{resightings > 1 ? "s" : ""}</Badge>}
        </div>
        <div className="mt-0.5 flex flex-wrap items-center gap-1">
          <ProvBadge p={o.provenance} />
          <ReviewChip status={v.review_status ?? "pending"} />
          {v.stale && <Badge color="var(--ink-3)">stale</Badge>}
        </div>
        <div className="mt-0.5 text-[10.5px] text-ink-3">{locationText(v)} · {ago(o.observed_at)}</div>
      </div>
      <div className="flex shrink-0 flex-col gap-1" onClick={(e) => e.stopPropagation()}>
        {v.review_status !== "confirmed" && (
          <Btn tone="primary" title="Confirm" onClick={() => run(() => actions.review(o.id!, "confirmed"))}>
            <Check size={13} />
          </Btn>
        )}
        {v.review_status !== "rejected" && (
          <Btn tone="default" title="Reject" onClick={() => run(() => actions.review(o.id!, "rejected"))}>
            <X size={13} />
          </Btn>
        )}
        <Btn tone="ghost" title="Inspect evidence" onClick={() => onEvidence(o.id!)}>
          <Eye size={13} />
        </Btn>
      </div>
    </li>
  );
}

function StructurePanel({ run }: { run: (f: () => Promise<unknown>, ok?: string) => void }) {
  const incident = useIncident((s) => s.incident);
  const view = useIncident((s) => s.structure);
  const [side, setSide] = useState("A");
  const [kind, setKind] = useState("blocked entrance");
  const b = view?.buildings[0] ?? null;
  return (
    <Section title="Structure · sides A to D" className="shrink-0">
      <p className="px-2.5 pt-2 text-[10.5px] leading-snug text-ink-3">{view?.note ?? "Side A is the address or front side; B, C, D continue clockwise."}</p>
      {b ? (
        <div className="flex items-center gap-1.5 px-2.5 pt-1.5 text-[12px]">
          <span className="font-semibold text-ink-1">{b.name}</span>
          <span className="text-[10.5px] text-ink-3">({b.source})</span>
          <label className="ml-auto flex items-center gap-1 text-ink-2">
            Side A faces
            <select aria-label="Side A compass face" value={b.side_a ?? ""} disabled={!incident}
              onChange={(e) => run(() => actions.setSideA(b.building_id, e.target.value), `Side A set to the ${e.target.value} face`)}
              className="h-7 rounded-md border border-line bg-surface-2 px-1 text-[12px]">
              <option value="" disabled>set…</option>
              <option value="N">north</option>
              <option value="E">east</option>
              <option value="S">south</option>
              <option value="W">west</option>
            </select>
          </label>
        </div>
      ) : (
        <p className="px-2.5 pt-1.5 text-[11px] text-ink-3">No building known for this incident (map data). Manual tags still work.</p>
      )}
      <div className="grid grid-cols-2 gap-1.5 p-2">
        {(["A", "B", "C", "D"] as const).map((sd) => {
          const items = b?.sides[sd] ?? [];
          return (
            <div key={sd} className="rounded-md border border-line p-1.5">
              <div className="text-[11px] font-bold text-ink-2">Side {sd}</div>
              {items.length === 0 ? (
                <div className="text-[10.5px] text-ink-3">nothing tagged</div>
              ) : (
                items.map((x) => (
                  <button key={x.observation_id} type="button" onClick={() => useIncident.getState().setSelectedObs(x.observation_id)}
                    className="block w-full truncate text-left text-[11px] hover:underline"
                    style={{ color: x.blocked ? "var(--serious)" : x.status === "confirmed" ? "var(--good)" : "var(--ink-1)" }} title={x.text}>
                    {x.text}
                  </button>
                ))
              )}
            </div>
          );
        })}
      </div>
      {b && b.sides["?"]?.length > 0 && (
        <p className="px-2.5 pb-1 text-[10.5px] text-ink-3">{b.sides["?"].length} opening(s) waiting for side A to be set.</p>
      )}
      <div className="flex gap-1 border-t border-line p-2">
        <select aria-label="Tag kind" value={kind} onChange={(e) => setKind(e.target.value)} className="h-7 min-w-0 flex-1 rounded-md border border-line bg-surface-2 px-1 text-[12px]">
          <option>blocked entrance</option>
          <option>door</option>
          <option>window</option>
          <option>garage door</option>
          <option>stairs</option>
          <option>hazard</option>
        </select>
        <select aria-label="Side" value={side} onChange={(e) => setSide(e.target.value)} className="h-7 w-14 rounded-md border border-line bg-surface-2 px-1 text-[12px]">
          {["A", "B", "C", "D"].map((sd) => (
            <option key={sd}>{sd}</option>
          ))}
        </select>
        <Btn disabled={!incident} onClick={() => run(() => actions.tagStructure(side, kind), `${kind} tagged on side ${side} (MANUAL)`)}>Tag</Btn>
      </div>
    </Section>
  );
}

const ACTION_LABEL: Record<string, string> = {
  review_candidate: "Review", inspect_area: "Inspect area", mark_last_seen: "Last seen", request_operator_confirmation: "Confirm",
  notify_responder: "Notify responder", show_structure_side: "Structure side", hold_for_more_information: "Hold",
  return_to_operator_review: "Operator review",
};

/** Software recommendations (rules or a validated model proposal). Messages are templates over computed fields;
 *  anything with an effect waits for a person's approval. */
function RecommendationsPanel({ run }: { run: (f: () => Promise<unknown>, ok?: string) => void }) {
  const acts = useIncident((s) => s.missionActions);
  const mode = useIncident((s) => s.decisionMode);
  const open = useMemo(
    () => acts.filter((a) => a.execution_status === "not_started" && a.approval_status !== "rejected").slice().reverse(),
    [acts],
  );
  const waiting = open.filter((a) => a.approval_status === "pending").length;
  return (
    <Section
      title={<span className="flex min-w-0 items-center gap-1.5 whitespace-nowrap"><Lightbulb size={12} /> Recommendations · {waiting} to approve</span>}
      right={mode ? <span className="max-w-[32%] truncate text-[10.5px] text-ink-3" title={`Decision engine: ${mode}`}>{mode}</span> : null}
      className="max-h-[220px] shrink-0"
    >
      {open.length === 0 ? (
        <p className="p-2.5 text-[12px] text-ink-3">No open recommendations.</p>
      ) : (
        <ul className="divide-y divide-[var(--border)] overflow-y-auto">
          {open.map((a) => {
            const p = (a.parameters ?? {}) as { message?: string; engine?: string | null };
            return (
              <li key={a.id} className="flex flex-col gap-1 px-2.5 py-1.5">
                <div className="flex items-center gap-1.5">
                  <Badge color="var(--s1)">{ACTION_LABEL[a.action_type] ?? a.action_type}</Badge>
                  <Badge color="var(--ink-3)" title={a.reason}>{a.proposed_by === "model" ? `model · ${p.engine ?? "?"}` : a.proposed_by}</Badge>
                  {a.approval_status === "pending" && (
                    <span className="ml-auto flex gap-1">
                      <Btn tone="primary" title="Approve" onClick={() => run(() => actions.decideAction(a.id!, true), "Approved")}>
                        <Check size={13} />
                      </Btn>
                      <Btn title="Reject" onClick={() => run(() => actions.decideAction(a.id!, false), "Rejected")}>
                        <X size={13} />
                      </Btn>
                    </span>
                  )}
                </div>
                <div className="text-[12px] leading-snug text-ink-1">{p.message}</div>
                {(a.safety_constraints?.length ?? 0) > 0 && (
                  <div className="text-[10.5px] text-ink-3">{a.safety_constraints!.join(" · ")}</div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </Section>
  );
}

/** Contiguous trail segments with a verified position; unverified bindings are dashed. */
function followPaths(f: { trail: { xy: [number, number] | null; identity: string; track_id: string }[] } | null): MapPath[] {
  if (!f) return [];
  const out: MapPath[] = [];
  let cur: MapPath | null = null;
  for (const p of f.trail) {
    const dashed = p.identity !== "confirmed";
    if (!p.xy) {
      cur = null;
      continue;
    }
    if (!cur || cur.dashed !== dashed) {
      cur = { id: `follow-${out.length}`, points: cur ? [cur.points[cur.points.length - 1]] : [], dashed, color: dashed ? "#e0a83a" : undefined };
      out.push(cur);
    }
    cur.points.push(p.xy);
  }
  return out;
}

const FOLLOW_TONE: Record<string, string> = { tracking: "var(--good)", lost: "var(--warning)", unverified: "var(--serious)", none: "var(--ink-3)", released: "var(--ink-3)" };

/** Follow: the operator designates; the system tracks, marks loss, and never re-designates on its own. */
function FollowPanel({ run }: { run: (f: () => Promise<unknown>, ok?: string) => void }) {
  const f = useIncident((s) => s.follow);
  const prop = useStore((s) => s.extras?.describe_proposal ?? null);
  const lookFor = useStore((s) => s.extras?.look_for ?? null);
  const auto = useStore((s) => s.state?.autonomy);
  const sim = useStore((s) => s.state?.mode === "SIMULATED");
  const bridge = useStore((s) => s.extras?.bridge_control);
  const cameraOnly = auto?.motion_profile === "camera_only";
  const controlReady = sim || Boolean(bridge?.capable && !bridge.inhibited && (auto?.target_is_sim || auto?.real_flight_allowed));
  const [who, setWho] = useState("");
  const [look, setLook] = useState("");
  if (!f) return null;
  const m = f.motion;
  const field = "h-7 min-w-0 flex-1 rounded border border-line bg-surface-2 px-1.5 text-[12px]";
  return (
    <Section title="Follow · designated target" className="shrink-0">
      <div className="flex flex-col gap-1.5 p-2.5 text-[12px]">
        <div className="flex gap-1">
          <input aria-label="Who to follow" placeholder="Who to follow: red shirt, jeans" value={who} onChange={(e) => setWho(e.target.value)} className={field} />
          <Btn disabled={!who.trim()} onClick={() => run(() => actions.followDescribe(who), "Matching the description…")}>Find</Btn>
        </div>
        {prop?.track_id && (
          <div className="flex items-center gap-1.5 rounded-md bg-surface-2 px-2 py-1 text-[11.5px]">
            <span className="min-w-0 flex-1 truncate">
              “{prop.description}” → <span className="font-mono">{prop.track_id}</span> · {prop.engine}
              {prop.confidence != null ? ` ${prop.confidence.toFixed(2)}` : ""}
            </span>
            <Btn tone="primary" title="Designate this person" onClick={() => run(() => actions.followDescribeConfirm(), "Target designated")}>
              <Check size={13} />
            </Btn>
          </div>
        )}
        <div className="flex gap-1">
          <input aria-label="Look for" placeholder="Look for: yellow jacket" value={look} onChange={(e) => setLook(e.target.value)} className={field} />
          <Btn disabled={!look.trim()} onClick={() => run(() => actions.lookFor(look), "Look-for set")}>Set</Btn>
          {lookFor && <Btn tone="ghost" onClick={() => run(() => actions.lookFor(""), "Look-for cleared")}>Clear</Btn>}
        </div>
        {lookFor && <div className="text-[11px] text-ink-3">Looking for “{lookFor.text}” (weighs pursuit routes towards matching sightings)</div>}
        <div className="flex items-center gap-1.5">
          <Badge color={FOLLOW_TONE[f.status]} solid>{f.status.toUpperCase()}</Badge>
          {f.track_id && <span className="font-mono text-[11px] text-ink-2">{f.track_id}</span>}
          {f.designated_by && <span className="ml-auto text-[11px] text-ink-3">designated by {f.designated_by}</span>}
        </div>
        <div className="leading-snug text-ink-1">{f.text}</div>
        {f.status === "unverified" && (
          <div className="flex gap-1">
            <Btn tone="primary" onClick={() => run(() => actions.followConfirm(), "Binding confirmed")}>
              <Check size={13} /> It is the target
            </Btn>
            <Btn onClick={() => run(() => actions.followReject(), "Re-binding rejected: searching again")}>
              <X size={13} /> Not them
            </Btn>
            <span className="self-center text-[11px] text-ink-3">or select the right box and designate it</span>
          </div>
        )}
        <div className="tnum rounded-md bg-surface-2 px-2 py-1 text-[11.5px] text-ink-2" title={m.source}>
          {m.valid
            ? `${m.distance_m} m travelled${m.speed_mps != null ? ` · ${m.speed_mps} m/s${m.heading_deg != null ? ` heading ${m.heading_deg}°` : ""} over ${m.window_s} s` : ""} · ±${m.position_uncertainty_m} m${m.note ? ` (${m.note})` : ""}`
            : `No motion statistics: ${m.reason}`}
        </div>
        {f.status === "lost" && (
          <div className="text-[11px] text-[var(--warning)]">Designation retained. Camera follow stops rotating while the person is out of view; a different track needs confirmation.</div>
        )}
        {auto && (
          <div className="flex flex-col gap-1 rounded-md border border-line px-2 py-1.5">
            <div className="flex items-center gap-1.5">
              <span className="text-[11px] font-semibold text-ink-2">{cameraOnly ? "Camera follow · yaw + gimbal" : "Aircraft follow"}</span>
              <Badge color={sim ? "var(--s7)" : "var(--warning)"} solid>{sim ? "SIMULATOR" : controlReady ? "CONTROL READY" : "CONTROL DISABLED"}</Badge>
              <span className="ml-auto font-mono text-[11px] text-ink-2">{auto.mode} · {auto.fsm}</span>
            </div>
            <div className="flex gap-1">
              <Btn tone={auto.mode === "FOLLOW" ? "primary" : "default"} disabled={!controlReady || f.status !== "tracking"} onClick={() => run(() => actions.autonomy("FOLLOW"), cameraOnly ? "Camera follow requested (no translation)" : "Aircraft follow requested")}>{cameraOnly ? "Start camera follow" : "Follow"}</Btn>
              <Btn tone={auto.mode === "HOLD" ? "primary" : "default"} disabled={!controlReady} onClick={() => run(() => actions.autonomy("HOLD"), "Holding")}>Hold</Btn>
              <Btn tone={auto.mode === "MANUAL" ? "primary" : "default"} onClick={() => run(() => actions.autonomy("MANUAL"), "Manual")}>Manual</Btn>
            </div>
            {auto.route_label && (
              <div className="text-[11px] leading-snug text-ink-2">Route ({auto.primitive_source}): {auto.route_label}</div>
            )}
            {auto.fsm === "VERIFY" && (
              <div className="text-[11px] leading-snug text-[var(--warning)]">Holding: the lock re-bound without confirmation. Confirm it is the target, or designate the right person.</div>
            )}
            {!sim && <div className="text-[11px] leading-snug text-[var(--warning)]">
              {bridge?.legacy || !bridge?.capable ? "Video-only relay: update the background relay in the original DJI SDK sample for controller-priority website control."
                : bridge.inhibited ? "Pilot in control. Re-arm on the website with the controller neutral."
                : !auto.real_flight_allowed && !auto.target_is_sim ? "Backend real-flight gate is off. Use the DJI simulator for the first control test."
                : "Ready to request control. The backend will check telemetry and aircraft readiness."}
            </div>}
            {cameraOnly && <div className="text-[10.5px] text-ink-3">Forward/backward, left/right and vertical commands are all zero. Pilot input cancels camera follow.</div>}
            {auto.note && <div className="text-[10.5px] text-ink-3">{auto.note}</div>}
          </div>
        )}
        {f.status !== "none" && f.status !== "released" && (
          <Btn tone="ghost" onClick={() => run(() => actions.followRelease(), "Follow released")}>Release target</Btn>
        )}
      </div>
    </Section>
  );
}
