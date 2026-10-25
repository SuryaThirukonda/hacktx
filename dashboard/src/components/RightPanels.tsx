import { Activity, Gauge, OctagonX, Pause, Play, RotateCcw, Ruler, ShieldCheck, SlidersHorizontal, Zap } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { clockTime, DASH, headingName, meters, mps, ms, num, secs } from "../lib/format";
import { STATUS_COLOR } from "../lib/severity";
import { EMPTY, useStore, type RightTab } from "../lib/store";
import type { ScenarioInfo } from "../types/contracts";
import { Badge, Btn, Dot, Empty, KV, Toggle } from "./ui";

async function call(p: Promise<unknown>, ok?: string) {
  const toast = useStore.getState().showToast;
  try {
    await p;
    if (ok) toast(ok);
  } catch (e) {
    toast((e as Error).message, "error");
  }
}

function Section({ title, icon, children, right }: { title: string; icon?: React.ReactNode; children: React.ReactNode; right?: React.ReactNode }) {
  return (
    <div className="border-b border-line px-3 py-2.5">
      <div className="mb-1.5 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-ink-3">
        {icon}
        {title}
        <span className="ml-auto normal-case tracking-normal">{right}</span>
      </div>
      {children}
    </div>
  );
}

function CmdBar({ label, v, max }: { label: string; v: number; max: number }) {
  const w = Math.min(1, Math.abs(v) / max) * 50;
  return (
    <div className="flex items-center gap-2 text-[11px]">
      <span className="w-14 text-ink-3">{label}</span>
      <div className="relative h-2 flex-1 rounded-full bg-surface-3">
        <div className="absolute top-0 h-2 rounded-full bg-[var(--s3)]" style={v >= 0 ? { left: "50%", width: `${w}%` } : { right: "50%", width: `${w}%` }} />
        <div className="absolute left-1/2 top-[-2px] h-3 w-px bg-[var(--ink-3)]" />
      </div>
      <span className="tnum w-14 text-right text-ink-1">{v.toFixed(1)}</span>
    </div>
  );
}

export function AutonomyPanel() {
  const a = useStore((s) => s.state?.autonomy);
  const d = useStore((s) => s.state?.drone);
  const mode = useStore((s) => s.state?.mode);
  const audit = useStore((s) => s.extras?.autonomy_audit ?? EMPTY);
  const baudit = useStore((s) => s.extras?.bridge_audit ?? EMPTY);
  if (!a) return <Empty>Waiting…</Empty>;
  const setMode = (m: string) => call(api.post("/api/autonomy/mode", { mode: m }), `Autonomy → ${m}`);
  const c = a.last_command;
  const sf = a.safety;
  return (
    <>
      <Section title="Autonomy" icon={<Gauge size={12} />} right={
        <Badge color={a.link === "sim" ? "var(--s7)" : a.link === "real" ? "var(--critical)" : "var(--ink-3)"}>
          {a.link === "sim" ? "SIM TARGET" : a.link === "real" ? "REAL AIRCRAFT" : "NO AIRCRAFT LINK"}
        </Badge>}>
        <div className="grid grid-cols-4 gap-1">
          {(["MANUAL", "FOLLOW", "HOLD", "RTL"] as const).map((m) => (
            <Btn key={m} active={a.mode === m} onClick={() => setMode(m)} disabled={a.mode === "ESTOP" && m !== "MANUAL"}>
              {m}
            </Btn>
          ))}
        </div>
        <button
          onClick={() => call(api.post("/api/autonomy/estop"), "E-STOP sent: hover + virtual stick disabled")}
          className="mt-2 flex h-11 w-full items-center justify-center gap-2 rounded-md bg-critical text-[15px] font-black tracking-widest text-white hover:brightness-110"
        >
          <OctagonX size={18} /> E-STOP
        </button>
        {a.mode === "ESTOP" && <div className="mt-1 text-[11px] text-critical">E-STOP latched. Switch to MANUAL to clear.</div>}
        {mode === "SIMULATED" && (
          <Btn className="mt-1.5 w-full" tone="ghost" onClick={() => call(api.post("/api/simulation/control", { action: "pilot_override" }), "Pilot stick input simulated")}>
            Simulate pilot stick override
          </Btn>
        )}
        <div className="mt-2 grid grid-cols-2 gap-x-3">
          <KV k="mode" v={a.mode} />
          <KV k="state" v={a.fsm} />
          <KV k="primitive" v={a.primitive ? a.primitive.replaceAll("_", " ") : DASH} />
          <KV k="chosen by" v={a.primitive_source} />
          <KV k="commands" v={a.commands_enabled ? "sent" : "advisory only"} />
          <KV k="gimbal cmd" v={num(a.gimbal_cmd_deg, 0, "°")} />
        </div>
        {a.note && <div className="mt-1 rounded bg-surface-2 p-1.5 text-[11px] text-warning">{a.note}</div>}
        {c && (
          <div className="mt-2 flex flex-col gap-1">
            <CmdBar label="forward" v={c.forward_mps} max={6} />
            <CmdBar label="right" v={c.right_mps} max={6} />
            <CmdBar label="up" v={c.up_mps} max={1.5} />
            <CmdBar label="yaw °/s" v={c.yaw_rate_dps} max={45} />
          </div>
        )}
      </Section>
      <Section title="Safety supervisor" icon={<ShieldCheck size={12} />}>
        <div className="grid grid-cols-2 gap-x-3">
          <KV k="geofence" v={<span style={{ color: sf.geofence_ok ? "var(--good-ink)" : "var(--critical)" }}>{sf.geofence_ok ? "inside" : "AT FENCE"} {sf.geofence_margin_m !== null ? `(${sf.geofence_margin_m.toFixed(1)} m)` : ""}</span>} />
          <KV k="altitude" v={sf.altitude_ok ? "ok" : "clamped"} />
          <KV k="battery" v={sf.battery_state} />
          <KV k="link" v={sf.link_ok ? "ok" : "LOST"} />
          <KV k="people-over" v={sf.people_over ? "AVOIDING" : "clear"} />
        </div>
        {sf.clamps.length > 0 && <div className="mt-1 text-[11px] text-warning">clamped: {sf.clamps.join("; ")}</div>}
      </Section>
      <Section title="Drone telemetry" icon={<Activity size={12} />} right={d?.sim ? <Badge color="var(--s7)">DJI simulator</Badge> : undefined}>
        {d ? (
          <div className="grid grid-cols-2 gap-x-3">
            <KV k="altitude" v={meters(d.alt_m)} />
            <KV k="battery" v={num(d.battery_pct, 0, "%")} />
            <KV k="heading" v={headingName(d.yaw_deg)} />
            <KV k="gimbal" v={num(d.gimbal_pitch_deg, 0, "°")} />
            <KV k="speed" v={d.velocity_xy_mps ? mps(Math.hypot(d.velocity_xy_mps[0], d.velocity_xy_mps[1])) : DASH} />
            <KV k="v-stick" v={d.vstick_enabled ? "enabled" : "off"} />
            <KV k="position" v={d.position_xy_m ? `${d.position_xy_m[0].toFixed(1)}, ${d.position_xy_m[1].toFixed(1)}` : DASH} />
            <KV k="tel. age" v={ms(d.telemetry_age_ms)} />
          </div>
        ) : (
          <div className="text-[12px] text-ink-3">No telemetry.</div>
        )}
      </Section>
      <Section title="Audit log">
        <ul className="max-h-40 overflow-y-auto scroll-thin text-[11px]">
          {[...audit.map((x) => ({ ...x, src: "mac" })), ...baudit.map((x) => ({ ...x, src: "bridge" }))]
            .sort((x, y) => y.ts_ms - x.ts_ms)
            .slice(0, 30)
            .map((x, i) => (
              <li key={i} className="flex gap-2 py-0.5">
                <span className="tnum w-11 shrink-0 text-ink-3">{clockTime(x.ts_ms)}</span>
                <span className="w-11 shrink-0 text-ink-3">{x.src}</span>
                <span className="text-ink-2">{x.text}</span>
              </li>
            ))}
          {audit.length + baudit.length === 0 && <li className="text-ink-3">empty</li>}
        </ul>
      </Section>
    </>
  );
}

interface RecordingInfo {
  name: string;
  path: string;
  mode: string;
  scenario: string | null;
  duration_s?: number;
  counts?: Record<string, number>;
}

function RecordingControls() {
  const rec = useStore((s) => s.state?.incident.recording ?? false);
  const mode = useStore((s) => s.state?.mode);
  const incident = useStore((s) => s.state?.incident);
  const allowed = mode === "SIMULATED" || (incident?.active && incident.consent_confirmed);
  return (
    <div className="mt-2 flex items-center gap-2">
      {rec ? (
        <Btn tone="danger" onClick={() => call(api.post("/api/recordings", { action: "stop" }), "Recording stopped and saved")}>
          <span className="h-2 w-2 rounded-full bg-white" /> Stop recording
        </Btn>
      ) : (
        <Btn disabled={!allowed} onClick={() => call(api.post("/api/recordings", { action: "start" }), "Recording inputs + events")}
          title={allowed ? "Record frames, observations, telemetry and events for replay" : "Requires an active incident with consent"}>
          <span className="h-2 w-2 rounded-full bg-critical" /> Record session
        </Btn>
      )}
      <span className="text-[11px] text-ink-3">{rec ? "recording (consented)" : "retention off"}</span>
    </div>
  );
}

export function RecordingsList() {
  const [list, setList] = useState<RecordingInfo[]>([]);
  const rec = useStore((s) => s.state?.incident.recording ?? false);
  useEffect(() => {
    api.get<{ sessions: RecordingInfo[] }>("/api/recordings").then((r) => setList(r.sessions)).catch(() => undefined);
  }, [rec]);
  return (
    <Section title="Recorded sessions">
      {list.length === 0 ? (
        <div className="text-[12px] text-ink-3">None yet. Use “Record session” (Ops tab).</div>
      ) : (
        <ul className="flex flex-col gap-1.5 text-[11px]">
          {list.slice(0, 8).map((r) => (
            <li key={r.name} className="rounded bg-surface-2 p-1.5">
              <div className="flex items-center gap-2">
                <span className="truncate font-mono text-ink-1">{r.name}</span>
                <span className="ml-auto text-ink-3">{r.duration_s ? `${r.duration_s}s` : "open"}</span>
              </div>
              <div className="text-ink-3">{r.mode}{r.scenario ? ` · ${r.scenario}` : ""}{r.counts ? ` · ${r.counts.drone_frames} frames` : ""}</div>
              <div className="mt-0.5 select-all font-mono text-[10px] text-ink-2">make replay SESSION=sessions/{r.name}</div>
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}

export function GuardrailsPanel() {
  const g = useStore((s) => s.state?.guardrails ?? EMPTY);
  const inc = useStore((s) => s.state?.incident);
  const notices = useStore((s) => s.state?.notices ?? EMPTY);
  const [consent, setConsent] = useState(false);
  const color = { enforced: "var(--good)", active: "var(--s1)", warning: "var(--warning)", disabled: "var(--ink-3)" } as const;
  return (
    <>
      <Section title="Incident & privacy" icon={<ShieldCheck size={12} />}>
        {inc?.active ? (
          <div className="flex items-center gap-2">
            <Badge color="var(--good)">{inc.incident_id}</Badge>
            <span className="text-[11px] text-ink-3">consent {inc.consent_confirmed ? "confirmed" : "missing"}</span>
            <Btn className="ml-auto" tone="ghost" onClick={() => call(api.del("/api/incident"), "Incident closed: locks and galleries cleared")}>
              Close
            </Btn>
          </div>
        ) : (
          <div className="flex flex-col gap-1.5">
            <label className="flex items-start gap-2 text-[12px] text-ink-2">
              <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} className="mt-0.5" />
              All tracked participants are consenting team members / exercise participants.
            </label>
            <Btn tone="primary" disabled={!consent} onClick={() => call(api.post("/api/incident", { consent }), "Incident started")}>
              Start incident (enables locks)
            </Btn>
          </div>
        )}
        <RecordingControls />
        {notices.map((n) => (
          <div key={n} className="mt-1.5 text-[11px] text-warning">{n}</div>
        ))}
      </Section>
      <Section title="Guardrails">
        <ul className="flex flex-col gap-1.5">
          {g.map((x) => (
            <li key={x.id} className="text-[12px]">
              <div className="flex items-center gap-1.5">
                <Dot color={color[x.status]} />
                <span className="font-medium text-ink-1">{x.label}</span>
                <span className="ml-auto text-[10px] uppercase" style={{ color: color[x.status] }}>{x.status}</span>
              </div>
              <div className="pl-3.5 text-[11px] text-ink-3">{x.detail}</div>
            </li>
          ))}
        </ul>
      </Section>
    </>
  );
}

export function SimPanel() {
  const sim = useStore((s) => s.state?.sim);
  const specs = useStore((s) => s.faultSpecs);
  const mode = useStore((s) => s.state?.mode);
  const [scenarios, setScenarios] = useState<ScenarioInfo[]>([]);
  const [pick, setPick] = useState<string>("");
  useEffect(() => {
    api.get<ScenarioInfo[]>("/api/scenarios").then(setScenarios).catch(() => undefined);
  }, []);
  useEffect(() => {
    if (sim && !pick) setPick(sim.scenario);
  }, [sim, pick]);
  const faults = sim?.faults ?? {};
  const groups = Object.entries(specs).reduce<Record<string, [string, (typeof specs)[string]][]>>((acc, [k, v]) => {
    (acc[v.group] ??= []).push([k, v]);
    return acc;
  }, {});
  const control = (action: string, value?: unknown) => call(api.post("/api/simulation/control", { action, value }));
  const setFault = (key: string, value: unknown) => call(api.post("/api/simulation/config", { key, value }));
  const current = scenarios.find((s) => s.id === pick);
  return (
    <>
      {mode === "REPLAY" && sim ? (
        <Section title="Replay (RECORDED inputs)" icon={<Play size={12} />} right={<span className="tnum text-ink-2">{sim.t_s.toFixed(1)} / {sim.duration_s?.toFixed(0)} s</span>}>
          <div className="text-[11px] text-ink-3">{sim.scenario}</div>
          <div className="mt-2 flex items-center gap-1">
            <Btn onClick={() => call(api.post("/api/replay/control", { action: sim.paused ? "resume" : "pause" }))}>{sim.paused ? <Play size={13} /> : <Pause size={13} />}{sim.paused ? "Play" : "Pause"}</Btn>
            {[0.25, 0.5, 1, 2].map((v) => (
              <Btn key={v} active={sim.speed === v} onClick={() => call(api.post("/api/replay/control", { action: "speed", value: v }))}>{v}×</Btn>
            ))}
          </div>
          <input type="range" min={0} max={sim.duration_s ?? 1} step={0.5} value={sim.t_s} className="mt-2 w-full"
            onChange={(e) => call(api.post("/api/replay/control", { action: "seek", value: Number(e.target.value) }))} />
          <div className="text-[10px] text-ink-3">Seeking restarts the pipeline session and fast-forwards; analysis resumes at the target time.</div>
        </Section>
      ) : mode === "SIMULATED" && sim ? (
        <Section title="Scenario" icon={<Play size={12} />} right={<span className="tnum text-ink-2">{sim.t_s.toFixed(1)} / {sim.duration_s?.toFixed(0)} s</span>}>
          <div className="flex gap-1.5">
            <select value={pick} onChange={(e) => setPick(e.target.value)} className="h-7 min-w-0 flex-1 rounded-md border border-line-strong bg-surface-2 px-1.5 text-[12px] text-ink-1">
              {scenarios.map((s) => (
                <option key={s.id} value={s.id}>{s.id} · {s.name}</option>
              ))}
            </select>
            <Btn tone="primary" onClick={() => control("restart", pick)} title="Load scenario (new session, new tracker epoch)">
              <RotateCcw size={13} /> Load
            </Btn>
          </div>
          {current && <div className="mt-1 text-[11px] text-ink-3">{current.description}</div>}
          <div className="mt-2 flex items-center gap-1">
            <Btn onClick={() => control(sim.paused ? "resume" : "pause")}>{sim.paused ? <Play size={13} /> : <Pause size={13} />}{sim.paused ? "Resume" : "Pause"}</Btn>
            {[0.5, 1, 2].map((v) => (
              <Btn key={v} active={sim.speed === v} onClick={() => control("speed", v)}>{v}×</Btn>
            ))}
          </div>
          <div className="mt-2 h-1.5 w-full rounded-full bg-surface-3">
            <div className="h-1.5 rounded-full bg-[var(--s7)]" style={{ width: `${Math.min(100, (sim.t_s / (sim.duration_s || 1)) * 100)}%` }} />
          </div>
        </Section>
      ) : (
        <Section title="Scenario">
          <div className="text-[12px] text-ink-3">Simulator not running ({mode}). Fault injection still applies to cloud decisions and ingest.</div>
        </Section>
      )}
      {Object.entries(groups).map(([group, items]) => (
        <Section key={group} title={`Faults · ${group}`} icon={<Zap size={12} />}>
          <div className="flex flex-col gap-1.5">
            {items.map(([key, spec]) =>
              spec.type === "bool" ? (
                <Toggle key={key} checked={Boolean(faults[key])} onChange={(v) => setFault(key, v)} label={spec.label} />
              ) : (
                <label key={key} className="flex items-center gap-2 text-[12px] text-ink-2">
                  <span className="w-28 shrink-0 truncate">{spec.label}</span>
                  <input
                    type="range"
                    min={spec.min ?? 0}
                    max={spec.max ?? 1}
                    step={(spec.max ?? 1) / 20}
                    value={Number(faults[key] ?? 0)}
                    onChange={(e) => setFault(key, Number(e.target.value))}
                    className="min-w-0 flex-1"
                  />
                  <span className="tnum w-12 text-right">{Number(faults[key] ?? 0).toFixed(key.endsWith("_ms") ? 0 : 2)}</span>
                </label>
              ),
            )}
          </div>
        </Section>
      ))}
      <div className="px-3 py-2 text-[11px] text-ink-3">Injected faults are labelled in state and events. Scripted scenario faults appear here as active toggles.</div>
    </>
  );
}

export function CalibrationPanel() {
  const c = useStore((s) => s.state?.calibration);
  const r = useStore((s) => s.state?.registration);
  const mapMode = useStore((s) => s.state?.map_mode);
  if (!c || !r) return <Empty>Waiting…</Empty>;
  return (
    <>
      <Section title="Drone ground calibration" icon={<Ruler size={12} />} right={<Badge color={c.valid ? "var(--good)" : "var(--critical)"}>{c.valid ? c.source.toUpperCase() : "INVALID"}</Badge>}>
        <div className="text-[11px] text-ink-2">{c.reason}</div>
        <div className="mt-1 grid grid-cols-2 gap-x-3">
          <KV k="error bound" v={meters(c.error_bound_m, 2)} />
          <KV k="holdout RMSE" v={c.holdout_rmse_m !== null ? `${(c.holdout_rmse_m * 100).toFixed(1)} cm` : DASH} />
          <KV k="fit RMSE" v={c.fit_rmse_m !== null ? `${(c.fit_rmse_m * 100).toFixed(1)} cm` : DASH} />
          <KV k="markers seen" v={c.markers_detected} />
          <KV k="used / holdout" v={`${c.markers_used} / ${c.holdout_count}`} />
          <KV k="age" v={c.age_s !== null ? `${c.age_s.toFixed(1)} s` : DASH} />
        </div>
        {c.markers.length > 0 && (
          <table className="mt-1.5 w-full text-[11px]">
            <thead className="text-ink-3">
              <tr><th className="text-left font-normal">marker</th><th className="text-left font-normal">role</th><th className="text-right font-normal">world (m)</th><th className="text-right font-normal">error</th></tr>
            </thead>
            <tbody className="tnum">
              {c.markers.map((m) => (
                <tr key={m.id}>
                  <td>M{m.id}</td>
                  <td style={{ color: m.holdout ? "var(--s7)" : "var(--s3)" }}>{m.holdout ? "holdout" : m.used_for_fit ? "fit" : "seen"}</td>
                  <td className="text-right">{m.world_xy ? `${m.world_xy[0]}, ${m.world_xy[1]}` : DASH}</td>
                  <td className="text-right">{m.error_m !== null ? `${(m.error_m * 100).toFixed(1)} cm` : DASH}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <div className="mt-1.5 text-[10px] text-ink-3">Holdout markers are never used for fitting: their error is the honest accuracy estimate. Metric speeds and ranges are hidden when no source is valid.</div>
      </Section>
      <Section title="Phone ↔ map registration" right={<Badge color={r.valid ? "var(--good)" : "var(--warning)"}>{r.valid ? r.confidence.toUpperCase() : "NONE"}</Badge>}>
        <div className="text-[11px] text-ink-2">{r.reason}</div>
        <div className="mt-1 grid grid-cols-2 gap-x-3">
          <KV k="method" v={r.method.replaceAll("_", " ")} />
          <KV k="residual" v={meters(r.residual_m, 2)} />
          <KV k="pairs" v={r.pairs} />
          <KV k="yaw" v={num(r.yaw_deg, 1, "°")} />
        </div>
        <div className="mt-1 text-[11px] text-ink-3">Map mode: <span className="text-ink-1">{mapMode}</span>. LiDAR obstacles are drawn on the shared map only when registered.</div>
      </Section>
    </>
  );
}

export function SystemPanel() {
  const st = useStore((s) => s.state);
  const lat = useStore((s) => s.extras?.alert_latency_ms);
  if (!st) return <Empty>Waiting…</Empty>;
  const L = st.metrics.latencies;
  const rows: [string, string][] = [
    ["detect_ms", "Detection"],
    ["cmc_ms", "Camera-motion comp."],
    ["calibration_ms", "Calibration"],
    ["track_ms", "Tracking"],
    ["frame_total_ms", "Frame total"],
    ["tick_ms", "Fusion + rules tick"],
    ["sim_step_ms", "Sim step (render)"],
  ];
  const decRows = Object.keys(L).filter((k) => k.startsWith("decision_ms_"));
  const alertRows = Object.keys(L).filter((k) => k.startsWith("alert_latency_ms_"));
  return (
    <>
      <Section title="Sources" icon={<Activity size={12} />}>
        <table className="w-full text-[11px]">
          <thead className="text-ink-3">
            <tr><th className="text-left font-normal">source</th><th className="text-left font-normal">status</th><th className="text-right font-normal">rate</th><th className="text-right font-normal">age</th><th className="text-right font-normal">drop</th></tr>
          </thead>
          <tbody className="tnum">
            {st.sources.map((s) => (
              <tr key={s.source_id} title={s.detail ?? ""}>
                <td className="py-0.5">{s.source_id}</td>
                <td><span className="inline-flex items-center gap-1"><Dot color={STATUS_COLOR[s.status]} />{s.status}</span></td>
                <td className="text-right">{s.fps !== null ? `${s.fps.toFixed(0)} Hz` : DASH}</td>
                <td className="text-right">{s.last_age_ms !== null ? ms(s.last_age_ms) : DASH}</td>
                <td className="text-right">{s.dropped}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Section>
      <Section title="Latency (p50 / p95, measured)" icon={<SlidersHorizontal size={12} />}>
        {[...rows.filter(([k]) => L[k]), ...decRows.map((k) => [k, `Decision ${k.replace("decision_ms_", "")}`] as [string, string]), ...alertRows.map((k) => [k, `Capture→alert ${k.replace("alert_latency_ms_", "").toLowerCase()}`] as [string, string])].map(([k, label]) => (
          <div key={k} className="flex items-baseline justify-between py-[2px] text-[12px]">
            <span className="text-ink-3">{label}</span>
            <span className="tnum text-ink-1">{ms(L[k]?.p50)} / {ms(L[k]?.p95)} <span className="text-[10px] text-ink-3">n={L[k]?.n}</span></span>
          </div>
        ))}
        {lat !== null && lat !== undefined && <div className="mt-1 text-[11px] text-ink-3">last capture→alert {ms(lat)} (sim time)</div>}
      </Section>
      <Section title="Situation summary">
        <div className="text-[12px] text-ink-1">{st.narration ?? DASH}</div>
        <div className="mt-1 text-[10px] text-ink-3">Template narration from measured fields only.</div>
      </Section>
      <Section title="Counters">
        <div className="grid grid-cols-2 gap-x-3">
          {Object.entries(st.metrics.counters)
            .filter(([k]) => k.startsWith("alert_") || k.startsWith("event_"))
            .sort()
            .slice(0, 24)
            .map(([k, v]) => (
              <KV key={k} k={k.replace("event_", "ev ").replace("alert_", "al ").toLowerCase()} v={v} />
            ))}
        </div>
        <KV k="session" v={st.session_id} mono />
        <KV k="uptime (sim)" v={secs(st.server_ts_ms)} />
      </Section>
    </>
  );
}

export function RightColumn() {
  const tab = useStore((s) => s.rightTab);
  const setTab = useStore((s) => s.setRightTab);
  const tabs: [RightTab, string][] = [["ops", "Ops"], ["sim", "Sim & faults"], ["calib", "Calibration"], ["system", "System"]];
  return (
    <section className="flex h-full min-h-0 flex-col rounded-lg border border-line bg-surface">
      <nav className="flex h-9 shrink-0 items-center gap-0.5 border-b border-line px-1.5">
        {tabs.map(([k, l]) => (
          <button key={k} onClick={() => setTab(k)} className={`rounded px-2 py-1 text-[12px] font-semibold ${tab === k ? "bg-surface-3 text-ink-1" : "text-ink-3 hover:text-ink-2"}`}>
            {l}
          </button>
        ))}
      </nav>
      <div className="min-h-0 flex-1 overflow-y-auto scroll-thin">
        {tab === "ops" && (
          <>
            <AutonomyPanel />
            <GuardrailsPanel />
          </>
        )}
        {tab === "sim" && <SimPanel />}
        {tab === "calib" && <CalibrationPanel />}
        {tab === "system" && (
          <>
            <SystemPanel />
            <RecordingsList />
          </>
        )}
      </div>
    </section>
  );
}
