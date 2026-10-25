import { Crosshair, Lock, Search, Unlock, UserCheck } from "lucide-react";
import { useMemo, useState } from "react";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "../lib/api";
import { DASH, headingName, meters, mph, mps, num, secs, shortId } from "../lib/format";
import { EMPTY, useStore, type KinSample } from "../lib/store";
import type { LockState, RelativeKinematics, Track } from "../types/contracts";
import { Badge, Btn, Empty, KV, Panel } from "./ui";

const LOCK_COLOR: Record<string, string> = {
  LOCKED: "var(--good)",
  COAST: "var(--warning)",
  REACQUIRE: "var(--serious)",
  UNCERTAIN: "var(--serious)",
  HOLD: "var(--critical)",
  UNLOCKED: "var(--ink-3)",
};

async function call(p: Promise<unknown>, ok?: string) {
  const toast = useStore.getState().showToast;
  try {
    await p;
    if (ok) toast(ok);
  } catch (e) {
    toast((e as Error).message, "error");
  }
}

function ClockFace({ kin }: { kin: RelativeKinematics }) {
  const b = kin.bearing_rel_deg;
  const r = 30;
  return (
    <svg width="76" height="76" viewBox="-38 -38 76 76" role="img" aria-label={b !== null ? `subject at ${kin.clock} o'clock` : "bearing unknown"}>
      <circle r={r} fill="var(--surface-2)" stroke="var(--axis)" />
      {Array.from({ length: 12 }).map((_, i) => {
        const a = (i * 30 * Math.PI) / 180;
        return <line key={i} x1={Math.sin(a) * (r - 4)} y1={-Math.cos(a) * (r - 4)} x2={Math.sin(a) * r} y2={-Math.cos(a) * r} stroke="var(--ink-3)" strokeWidth={i % 3 === 0 ? 2 : 1} />;
      })}
      <text y={-r + 13} textAnchor="middle" fontSize="8" fill="var(--ink-3)">12</text>
      <polygon points="0,-7 5,5 0,2 -5,5" fill="var(--s1)" />
      {b !== null && (
        <g transform={`rotate(${b})`}>
          <line y1={-6} y2={-r + 3} stroke="var(--s2)" strokeWidth={3} strokeLinecap="round" />
          <circle cy={-r + 3} r={4} fill="var(--s2)" />
        </g>
      )}
    </svg>
  );
}

function KinChart({ data, field, label, unit, color }: { data: KinSample[]; field: keyof KinSample; label: string; unit: string; color: string }) {
  const t0 = data.length ? data[data.length - 1].t : 0;
  const rows = data.filter((d) => t0 - d.t < 30000).map((d) => ({ t: (d.t - t0) / 1000, v: d[field] as number | null }));
  const any = rows.some((r) => r.v !== null);
  return (
    <div className="min-w-0 flex-1">
      <div className="truncate text-[11px] text-ink-3">{label} · last 30 s</div>
      <div className="h-[86px]">
        {any ? (
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={rows} margin={{ top: 6, right: 6, bottom: 0, left: -22 }}>
              <CartesianGrid stroke="var(--grid)" vertical={false} />
              <XAxis dataKey="t" type="number" domain={[-30, 0]} tick={{ fontSize: 10, fill: "var(--ink-3)" }} tickFormatter={(v) => `${v}s`} stroke="var(--axis)" />
              <YAxis tick={{ fontSize: 10, fill: "var(--ink-3)" }} stroke="var(--axis)" width={44} />
              <Tooltip
                contentStyle={{ background: "var(--surface)", border: "1px solid var(--border-strong)", fontSize: 11, color: "var(--ink-1)" }}
                formatter={(v: number) => [`${v?.toFixed(2)} ${unit}`, label]}
                labelFormatter={(v: number) => `${v.toFixed(1)} s`}
              />
              <Line type="monotone" dataKey="v" stroke={color} strokeWidth={2} dot={false} isAnimationActive={false} connectNulls={false} />
            </LineChart>
          </ResponsiveContainer>
        ) : (
          <div className="hatch flex h-full items-center justify-center rounded text-[11px] text-ink-3">no valid measurement</div>
        )}
      </div>
    </div>
  );
}

function SubjectCard({ lock, kin }: { lock: LockState; kin: RelativeKinematics }) {
  const motion = useStore((s) => s.extras?.motion_label);
  const kinHistory = useStore((s) => s.kinHistory);
  const tracks = useStore((s) => s.state?.tracks ?? EMPTY);
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-[13px] font-semibold" style={{ color: "var(--s2)" }}>SUBJECT</span>
        <Badge color={LOCK_COLOR[lock.state]}>{lock.state}</Badge>
        <span className="tnum text-[12px] text-ink-2">{shortId(lock.track_id)}</span>
        {!lock.verified && <Badge color="var(--warning)" title="Re-acquired by appearance+geometry; not confirmed by operator or D2">UNVERIFIED</Badge>}
        {lock.rebinds > 0 && <span className="text-[11px] text-ink-3">{lock.rebinds} rebind{lock.rebinds > 1 ? "s" : ""}</span>}
        <div className="ml-auto flex gap-1">
          <Btn tone="ghost" title="Release subject lock" onClick={() => call(api.del("/api/locks/subject"), "Subject lock released")}>
            <Unlock size={13} /> Release
          </Btn>
        </div>
      </div>
      {lock.description && <div className="text-[11px] text-ink-3">described as “{lock.description}”</div>}
      {kin.valid ? (
        <div className="flex items-center gap-3">
          <ClockFace kin={kin} />
          <div className="grid flex-1 grid-cols-3 gap-x-3 gap-y-1">
            <div>
              <div className="text-[10px] uppercase text-ink-3">range</div>
              <div className="tnum text-[20px] font-bold leading-6">{num(kin.range_m, 1)}<span className="text-[12px] font-medium text-ink-3"> m</span></div>
              <div className="tnum text-[10px] text-ink-3">±{num(kin.range_error_m, 1)} m</div>
            </div>
            <div>
              <div className="text-[10px] uppercase text-ink-3">bearing</div>
              <div className="tnum text-[20px] font-bold leading-6">{kin.clock ?? DASH}<span className="text-[12px] font-medium text-ink-3"> o'clock</span></div>
              <div className="tnum text-[10px] text-ink-3">{kin.bearing_rel_deg !== null ? `${kin.bearing_rel_deg.toFixed(0)}° rel` : "heading unknown"}</div>
            </div>
            <div>
              <div className="text-[10px] uppercase text-ink-3">closing</div>
              <div className="tnum text-[20px] font-bold leading-6" style={{ color: kin.closing_mps !== null && kin.closing_mps > 2.5 ? "var(--critical)" : undefined }}>
                {num(kin.closing_mps, 1)}<span className="text-[12px] font-medium text-ink-3"> m/s</span>
              </div>
              <div className="tnum text-[10px] text-ink-3">{mph(kin.closing_mps)} · TTC {kin.ttc_s !== null ? `${kin.ttc_s.toFixed(1)} s` : DASH}</div>
            </div>
            <div className="col-span-3 tnum text-[11px] text-ink-2">
              speed {mps(kin.subject_speed_mps)} ({mph(kin.subject_speed_mps)}) · heading {headingName(kin.subject_heading_deg)} · accel {num(kin.subject_accel_mps2, 1, " m/s²")}
              {motion?.label && (
                <span className="ml-1" title={`advisory label from ${motion.engine}; never gates alerts`}>
                  · <span style={{ color: "var(--s7)" }}>{motion.label.replaceAll("_", " ")}</span>
                  {motion.confidence !== null && <span className="text-ink-3"> ({motion.confidence.toFixed(2)}, {motion.engine})</span>}
                </span>
              )}
            </div>
          </div>
        </div>
      ) : (
        <div className="hatch rounded-md p-2 text-[12px] text-ink-2">
          No responder-relative measurement: <span className="text-ink-1">{kin.reason}</span>
        </div>
      )}
      {lock.needs_operator_confirm && lock.candidates.length > 0 && (
        <div className="rounded-md border border-[var(--serious)] p-2">
          <div className="mb-1 text-[12px] font-semibold" style={{ color: "var(--serious)" }}>Identity uncertain: confirm the subject (never auto-switched)</div>
          {lock.candidates.map((c) => {
            const t = tracks.find((q) => q.track_id === c.track_id);
            return (
              <div key={c.track_id} className="flex items-center gap-2 py-0.5 text-[12px]">
                <span className="tnum w-12">{shortId(c.track_id)}</span>
                <span className="tnum text-ink-2">appearance {c.similarity.toFixed(2)}</span>
                <span className="tnum text-ink-3">{c.distance_m !== null ? `${c.distance_m.toFixed(1)} m from prediction` : ""}</span>
                <span className="text-ink-3">{t?.visibility}</span>
                <Btn className="ml-auto" onClick={() => call(api.post("/api/locks/subject/confirm", { track_id: c.track_id }), "Subject confirmed")}>
                  <UserCheck size={13} /> Confirm
                </Btn>
              </div>
            );
          })}
        </div>
      )}
      <div className="flex gap-2">
        <KinChart data={kinHistory} field="range" label="Range (m)" unit="m" color="var(--s2)" />
        <KinChart data={kinHistory} field="closing" label="Closing (m/s)" unit="m/s" color="var(--s2)" />
      </div>
    </div>
  );
}

function TrackDetails({ t }: { t: Track }) {
  const lockSubject = () => call(api.post("/api/locks/subject", { track_id: t.track_id }), `Subject locked on ${shortId(t.track_id)}`);
  const lockResponder = () => call(api.post("/api/locks/responder", { track_id: t.track_id }), `Responder set to ${shortId(t.track_id)}`);
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-[13px] font-semibold">Track {shortId(t.track_id)}</span>
        <Badge color={t.state === "CONFIRMED" ? "var(--good)" : "var(--warning)"}>{t.state}</Badge>
        <Badge color="var(--ink-2)">{t.visibility}</Badge>
        {t.role && <Badge color={t.role === "subject" ? "var(--s2)" : "var(--s1)"}>{t.role}</Badge>}
        <div className="ml-auto flex gap-1">
          {t.role !== "subject" && (
            <Btn onClick={lockSubject} title="Human-selected lock (requires an active incident)">
              <Lock size={13} /> Lock subject
            </Btn>
          )}
          {t.role !== "responder" && (
            <Btn onClick={lockResponder}>
              <Crosshair size={13} /> Is responder
            </Btn>
          )}
        </div>
      </div>
      <div className="grid grid-cols-2 gap-x-4">
        <KV k="class" v={`${t.class_name} ${t.confidence.toFixed(2)}`} />
        <KV k="model" v={t.model_id} mono />
        <KV k="position" v={t.ground_xy_m ? `${t.ground_xy_m[0].toFixed(1)}, ${t.ground_xy_m[1].toFixed(1)} m` : "unlocalized"} />
        <KV k="± error" v={meters(t.ground_error_m, 2)} />
        <KV k="speed" v={t.velocity_valid ? `${mps(t.speed_mps)} · ${mph(t.speed_mps)}` : "not valid"} />
        <KV k="heading" v={headingName(t.heading_deg)} />
        <KV k="hits" v={t.hits} />
        <KV k="last seen" v={secs(t.last_seen_ts_ms - t.first_seen_ts_ms)} />
        <KV k="calibration" v={t.calibration_id ?? "—"} mono />
        <KV k="lat/lon" v={t.lat !== null && t.lon !== null ? `${t.lat.toFixed(6)}, ${t.lon.toFixed(6)}` : "—"} mono />
      </div>
      <div className="text-[10px] text-ink-3">Track IDs are ephemeral association labels within this session, not identities.</div>
    </div>
  );
}

function Describe() {
  const [text, setText] = useState("");
  const proposal = useStore((s) => s.extras?.describe_proposal);
  return (
    <div className="flex flex-col gap-1.5">
      <form
        className="flex gap-1.5"
        onSubmit={(e) => {
          e.preventDefault();
          if (text.trim()) call(api.post("/api/locks/describe", { text }), "Asked decision engine (D3) for a match");
        }}
      >
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Describe subject (e.g. red shirt, jeans)"
          className="h-7 min-w-0 flex-1 rounded-md border border-line-strong bg-surface-2 px-2 text-[12px] text-ink-1 placeholder:text-ink-3"
        />
        <Btn tone="primary">
          <Search size={13} /> Find
        </Btn>
      </form>
      {proposal && (
        <div className="flex items-center gap-2 rounded-md bg-surface-2 p-1.5 text-[12px]">
          <span style={{ color: "var(--s7)" }}>D3 · {proposal.engine}</span>
          <span>
            “{proposal.description}” → {proposal.track_id ? shortId(proposal.track_id) : "no match"}
            {proposal.confidence !== null && ` (${proposal.confidence.toFixed(2)})`}
          </span>
          {proposal.track_id && (
            <Btn className="ml-auto" onClick={() => call(api.post("/api/locks/describe/confirm", { track_id: proposal.track_id }), "Subject locked (operator-confirmed match)")}>
              <UserCheck size={13} /> Confirm & lock
            </Btn>
          )}
        </div>
      )}
    </div>
  );
}

export function TrackInspector() {
  const st = useStore((s) => s.state);
  const selected = useMemo(() => st?.tracks.find((t) => t.track_id === st.selected_track_id) ?? null, [st]);
  const subj = st?.locks.subject;
  const resp = st?.locks.responder;
  return (
    <Panel
      title="Subject & track inspector"
      icon={<Crosshair size={14} />}
      right={
        resp && (
          <span className="text-[11px] text-ink-3">
            responder {resp.state === "UNLOCKED" ? "not marked" : shortId(resp.track_id)} · {st?.responder.position_source.replace("_", " ")}
          </span>
        )
      }
      bodyClass="overflow-y-auto scroll-thin p-3 flex flex-col gap-3"
    >
      {!st ? (
        <Empty>Waiting for state…</Empty>
      ) : (
        <>
          {subj && subj.state !== "UNLOCKED" ? (
            <SubjectCard lock={subj} kin={st.kinematics} />
          ) : (
            <div className="rounded-md bg-surface-2 p-2 text-[12px] text-ink-2">
              No subject selected. Click a person in the drone view (shift-click locks immediately) or describe them below. A human always makes the selection.
            </div>
          )}
          {selected && selected.role !== "subject" && <TrackDetails t={selected} />}
          {selected && selected.role === "subject" && <TrackDetails t={selected} />}
          <Describe />
        </>
      )}
    </Panel>
  );
}
