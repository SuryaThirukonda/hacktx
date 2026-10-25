import { ArrowLeft, Box, Camera, Droplets, Flame, Pause, Play, RotateCcw } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api, wsUrl } from "../lib/api";
import {
  type CamMode,
  type Decision3D,
  decisionColor,
  type Frame3D,
  type FrameHazard3D,
  SceneController,
  TRAIL,
  trailKind,
  type World3D,
} from "../lib/sim3d";
import { ReconnectingSocket } from "../lib/ws";

const pretty = (s: string | null | undefined) => (s ? s.replaceAll("_", " ") : "–");

function engineLabel(engine: string, model: string): string {
  if (engine === "openai") return `OpenAI ${model} · live`;
  if (engine === "mock") return "MOCK stand-in (no key)";
  if (engine === "typesafe") return `TypeSafe ${model}`;
  if (engine === "strands") return "Strands (local)";
  return engine;
}

function DecisionCard({ d }: { d: Decision3D }) {
  const col = decisionColor(d);
  const fallback = d.engine === "rules";
  return (
    <div className="rounded-lg border border-white/10 bg-black/45 p-2.5 backdrop-blur-sm" style={{ borderLeft: `3px solid ${col}` }}>
      <div className="flex items-baseline gap-2 text-[11px] text-[#c3c2b7]">
        <span className="tnum">t {(d.t_ms / 1000).toFixed(1)} s</span>
        <span>lost {d.lost_s?.toFixed(1) ?? "–"} s</span>
        <span className="ml-auto tnum">{d.latency_ms != null ? `${Math.round(d.latency_ms)} ms` : ""}</span>
      </div>
      <div className="mt-0.5 text-[14px] font-bold text-[#f0efec]">{pretty(d.selected)}</div>
      <div className="text-[11px] text-[#c3c2b7]">
        {fallback ? "rules fallback" : engineLabel(d.engine, d.model)}
        {d.confidence != null ? ` · confidence ${d.confidence.toFixed(2)}` : ""}
      </div>
      <div className="mt-1.5 space-y-1">
        {d.probabilities.map(([opt, p]) => (
          <div key={opt} className="flex items-center gap-2 text-[10.5px] text-[#c3c2b7]">
            <span className="w-[112px] truncate">{pretty(opt)}</span>
            <span className="relative h-[7px] flex-1 rounded-sm bg-white/10">
              <span className="absolute inset-y-0 left-0 rounded-sm" style={{ width: `${Math.max(2, p * 100)}%`, background: opt === d.selected ? col : "#5b5a55" }} />
            </span>
            <span className="tnum w-8 text-right">{p.toFixed(2)}</span>
          </div>
        ))}
      </div>
      <div className="mt-1.5 text-[10.5px]" style={{ color: d.acted && !fallback ? "#c3c2b7" : "#e0a83a" }}>
        {fallback
          ? `Engine unavailable: ${(d.reason.split(";")[0] || "error").slice(0, 90)}`
          : d.acted
            ? `Flown for 3 s · rules would have picked ${pretty(d.baseline)}`
            : `Below the confidence gate · rules flew ${pretty(d.baseline)}`}
      </div>
      <div className="mt-0.5 text-[10px] text-[#898781]">
        cover at last-seen: {d.cover == null ? "–" : d.cover ? "yes" : "no"} · {d.predicted ?? ""}
      </div>
    </div>
  );
}

function HazardPanel({ hz }: { hz: FrameHazard3D }) {
  if (hz.kind === "flood") {
    return (
      <div className="rounded-xl border border-[#56b4e9]/30 bg-black/55 p-3 text-[11px] backdrop-blur-sm">
        <div className="flex items-center gap-1.5 text-[12.5px] font-bold text-[#8fd4ff]">
          <Droplets size={14} className="text-[#56b4e9]" />
          Flood screening model (3D)
        </div>
        <div className="mt-0.5 text-[10.5px] text-[#a9a79f]">{hz.label}</div>
        <div className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1">
          <span className="text-[#898781]">water level</span>
          <span className="tnum font-semibold text-[#f0efec]">{hz.level_m.toFixed(2)} m</span>
          <span className="text-[#898781]">max depth</span>
          <span className="tnum font-semibold text-[#f0efec]">{hz.max_depth_m.toFixed(2)} m</span>
          <span className="text-[#898781]">flooded area</span>
          <span className="tnum text-[#f0efec]">{hz.flooded_area_m2.toFixed(0)} m²</span>
          <span className="text-[#898781]">deep (&gt;{hz.road_closed_depth_m} m)</span>
          <span className="tnum text-[#ff9f1c]">{hz.deep_area_m2.toFixed(0)} m² · {hz.closed_roads.length} road{hz.closed_roads.length === 1 ? "" : "s"} closed</span>
        </div>
        {hz.crew_route ? (
          <div className="mt-2 rounded-md bg-[#22c55e]/15 px-2 py-1.5 text-[11px] text-[#8fe0c0]">
            <div className="font-semibold">D9 crew route → {hz.crew_route.goal}</div>
            <div className="tnum text-[10.5px] text-[#c3c2b7]">
              {hz.crew_route.length_m.toFixed(0)} m · max water {hz.crew_route.max_water_depth_m.toFixed(2)} m (≤{hz.road_closed_depth_m} m limit)
            </div>
          </div>
        ) : (
          <div className="mt-2 rounded-md bg-[#d03b3b]/20 px-2 py-1 text-[11px] text-[#ffb08a]">
            No dry crew route available below {hz.road_closed_depth_m} m
          </div>
        )}
      </div>
    );
  }

  const unclearCount = hz.openings.filter((o) => o.condition === "unclear").length;
  const keepOutOpenings = hz.openings.filter((o) => o.in_keep_out).length;
  return (
    <div className="rounded-xl border border-[#ff922b]/30 bg-black/55 p-3 text-[11px] backdrop-blur-sm">
      <div className="flex items-center gap-1.5 text-[12.5px] font-bold text-[#ffb08a]">
        <Flame size={14} className="text-[#ff922b]" />
        Fire &amp; smoke screening model (3D)
      </div>
      <div className="mt-0.5 text-[10.5px] text-[#a9a79f]">{hz.label}</div>
      <div className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1">
        <span className="text-[#898781]">burning area</span>
        <span className="tnum font-semibold text-[#ff922b]">{hz.burning_area_m2.toFixed(0)} m²</span>
        <span className="text-[#898781]">2-min keep-out</span>
        <span className="tnum font-semibold text-[#ff6b6b]">{hz.keep_out_area_m2.toFixed(0)} m²</span>
        <span className="text-[#898781]">wind</span>
        <span className="tnum text-[#f0efec]">{hz.wind_speed_mps.toFixed(1)} m/s → {hz.wind_to_deg.toFixed(0)}°</span>
        <span className="text-[#898781]">openings (D11)</span>
        <span className="tnum text-[#f2cf7c]">{unclearCount}/{hz.openings.length} unclear (smoke) · {keepOutOpenings} in keep-out</span>
      </div>
      {hz.crew_route ? (
        <div className="mt-2 rounded-md bg-[#22c55e]/15 px-2 py-1.5 text-[11px] text-[#8fe0c0]">
          <div className="font-semibold">Crew route → {hz.crew_route.target_id}</div>
          <div className="tnum text-[10.5px] text-[#c3c2b7]">
            {hz.crew_route.length_m.toFixed(1)} m · {hz.crew_route.keep_out_cells} keep-out cells · never declared safe
          </div>
        </div>
      ) : (
        <div className="mt-2 rounded-md bg-[#fab219]/15 px-2 py-1 text-[11px] text-[#f2cf7c]">
          All doors inside keep-out or under smoke: no safe entry route proposed
        </div>
      )}
    </div>
  );
}

export function Sim3D() {
  const host = useRef<HTMLDivElement>(null);
  const ctl = useRef<SceneController | null>(null);
  const latest = useRef<Frame3D | null>(null);
  const [frame, setFrame] = useState<Frame3D | null>(null);
  const [mode, setMode] = useState<CamMode>("chase");
  const [status, setStatus] = useState("connecting");
  const [unavailable, setUnavailable] = useState(false);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", localStorage.getItem("overwatch.theme") === "light" ? "light" : "dark");
    document.title = "Overwatch · 3D decision view";
    if (!host.current) return;
    const c = new SceneController(host.current);
    ctl.current = c;
    c.setMode("chase");
    let meshLoaded = false;
    const s = new ReconnectingSocket(wsUrl("/ws/sim3d"), {
      onStatus: setStatus,
      onMessage: (raw) => {
        const m = raw as { type: string };
        if (m.type === "world") {
          const w = raw as World3D;
          c.setWorld(w);
          if (!meshLoaded) {
            c.loadDroneMesh(w.drone_mesh);
            meshLoaded = true;
          }
          setUnavailable(false);
        } else if (m.type === "frame") {
          const f = raw as Frame3D;
          latest.current = f;
          c.update(f);
        } else if (m.type === "unavailable") setUnavailable(true);
      },
    });
    const hud = setInterval(() => latest.current && setFrame(latest.current), 200);
    return () => {
      clearInterval(hud);
      s.close();
      c.dispose();
    };
  }, []);

  const pick = (m: CamMode) => {
    setMode(m);
    ctl.current?.setMode(m);
  };
  const control = (action: string, value?: unknown) => api.post("/api/simulation/control", { action, value }).catch(() => undefined);

  const f = frame;
  const a = f?.autonomy;
  const kind = f ? trailKind(f) : "tracking";
  const decisions = f ? [...f.decisions].reverse() : [];
  const counts = { flown: 0, gated: 0, fallback: 0 };
  for (const d of f?.decisions ?? []) counts[d.engine === "rules" ? "fallback" : d.acted ? "flown" : "gated"]++;

  return (
    <div className="fixed inset-0 overflow-hidden bg-[#1c2530] text-[#f0efec]">
      <div ref={host} className="absolute inset-0 z-0" />
      <style>{`
        .s3d-lbl { font: 600 11px Lato, system-ui, sans-serif; padding: 1px 6px; border-radius: 4px; white-space: nowrap;
                   background: rgba(13,13,13,0.66); color: #f0efec; transform: translateY(-50%); }
        .s3d-lbl-subject { color: #ffb08a; } .s3d-lbl-responder { color: #9cc3f5; } .s3d-lbl-bystander { color: #c3c2b7; font-weight: 400; }
        .s3d-lbl-drone { color: #8fe0c0; } .s3d-lbl-est { color: #ffb08a; font-weight: 400; background: rgba(13,13,13,0.45); }
        .s3d-lbl-static { color: #c3c2b7; font-weight: 400; background: rgba(13,13,13,0.4); }
        .s3d-pin { font-size: 10.5px; } .s3d-pin-ai { color: #c9c2ff; border: 1px solid #9085e9; }
        .s3d-pin-rules { color: #f2cf7c; border: 1px solid #c98500; }
      `}</style>

      {/* top-left: what is being shown */}
      <div className="pointer-events-auto absolute left-3 top-3 z-10 max-w-[460px] rounded-xl border border-white/10 bg-black/50 p-3 backdrop-blur-sm">
        <div className="flex items-center gap-2">
          <a href="/" className="rounded-md p-1 text-[#c3c2b7] hover:bg-white/10" title="Back to the console">
            <ArrowLeft size={15} />
          </a>
          <Box size={15} className="text-[#9085e9]" />
          <div className="text-[14px] font-bold">3D decision view</div>
          <span className="rounded-md bg-[#9085e9]/25 px-1.5 py-0.5 text-[10px] font-bold tracking-wide text-[#c9c2ff]">SIMULATED</span>
        </div>
        {unavailable ? (
          <div className="mt-2 text-[12px] text-[#e0a83a]">The 3D view shows the simulator; the backend is not in SIMULATED mode.</div>
        ) : (
          <>
            <div className="mt-1 text-[12px] text-[#c3c2b7]">
              {f ? `${f.scenario} · ${f.scenario_name}` : status === "open" ? "waiting for the simulator" : `link ${status}`}
            </div>
            {f && (
              <div className="mt-2 grid grid-cols-3 gap-x-3 gap-y-1 text-[11px]">
                <span className="text-[#898781]">time</span>
                <span className="text-[#898781]">drone</span>
                <span className="text-[#898781]">decisions</span>
                <span className="tnum">{f.t_s.toFixed(1)} / {f.duration_s.toFixed(0)} s{f.paused ? " · paused" : ""}</span>
                <span className="truncate" title={f.drone.model}>{f.drone.model.replace(" (MuJoCo Menagerie)", " · MuJoCo")}</span>
                <span className="truncate" title={engineLabel(f.engine, f.engine_model)}>{engineLabel(f.engine, f.engine_model)}</span>
              </div>
            )}
          </>
        )}
      </div>

      {/* autonomy + rotor state + active hazard summary */}
      {f && a && (
        <div className="pointer-events-none absolute left-3 top-[132px] z-10 flex w-[310px] flex-col gap-2">
          <div className="rounded-xl border border-white/10 bg-black/45 p-3 text-[11px] backdrop-blur-sm">
            <div className="flex flex-wrap items-center gap-1.5">
              <Chip label={a.mode} />
              <Chip label={a.fsm} tone={a.fsm === "REACQUIRE" ? "warn" : a.fsm === "HOLD" ? "crit" : "ok"} />
              <span className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5" style={{ background: `${TRAIL[kind]}33`, color: TRAIL[kind] }}>
                <i className="inline-block h-2 w-2 rounded-full" style={{ background: TRAIL[kind] }} />
                {a.primitive ? pretty(a.primitive) : "tracking"}
              </span>
            </div>
            <div className="mt-1 text-[#898781]">
              {a.primitive ? `chosen by ${a.primitive_source.replace("decision:", "decision model: ")}` : "following the subject (IBVS)"}
            </div>
            <div className="mt-2 grid grid-cols-4 gap-x-2 text-[#898781]">
              <span>alt</span><span>speed</span><span>pitch</span><span>roll</span>
              <span className="tnum text-[#f0efec]">{f.drone.pos[2].toFixed(1)} m</span>
              <span className="tnum text-[#f0efec]">{Math.hypot(f.drone.vel[0], f.drone.vel[1]).toFixed(1)} m/s</span>
              <span className="tnum text-[#f0efec]">{f.drone.pitch.toFixed(0)}°</span>
              <span className="tnum text-[#f0efec]">{f.drone.roll.toFixed(0)}°</span>
            </div>
            {f.drone.thrusts && (
              <div className="mt-2">
                <div className="text-[#898781]">rotor thrust (N, 0-13) · MuJoCo</div>
                <div className="mt-1 flex items-end gap-1.5">
                  {f.drone.thrusts.map((t, i) => (
                    <div key={i} className="flex flex-1 flex-col items-center gap-0.5">
                      <div className="relative h-8 w-full rounded-sm bg-white/10">
                        <div className="absolute inset-x-0 bottom-0 rounded-sm bg-[#199e70]" style={{ height: `${Math.min(100, (t / 13) * 100)}%` }} />
                      </div>
                      <span className="tnum text-[10px] text-[#c3c2b7]">{t.toFixed(1)}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
            {a.clamps.length > 0 && <div className="mt-2 text-[#e0a83a]">safety: {a.clamps.join(", ")}</div>}
            {a.geofence_margin_m != null && a.geofence_margin_m < 5 && (
              <div className="mt-1 text-[#e0a83a]">geofence margin {a.geofence_margin_m.toFixed(1)} m</div>
            )}
            <div className="mt-2 text-[#898781]">
              subject lock: <span className="text-[#f0efec]">{f.subject.state}</span>
              {f.subject.lost_ms != null && f.subject.state !== "LOCKED" ? ` · lost ${(f.subject.lost_ms / 1000).toFixed(1)} s` : ""}
            </div>
          </div>
          {f.hazards && <HazardPanel hz={f.hazards} />}
        </div>
      )}

      {/* right: the decisions that moved the drone */}
      {f && (
        <div className="pointer-events-auto absolute bottom-3 right-3 top-3 z-10 flex w-[330px] flex-col gap-2">
          <div className="rounded-xl border border-white/10 bg-black/50 p-3 backdrop-blur-sm">
            <div className="text-[13px] font-bold">D1 reacquire decisions</div>
            <div className="mt-0.5 text-[11px] text-[#c3c2b7]">
              When the subject is lost, the decision model picks one bounded search manoeuvre; the X2 flies it for 3 s.
              Below the confidence gate (0.5) the rules decide instead.
            </div>
            <div className="mt-1.5 flex flex-wrap gap-3 text-[11px]">
              <span style={{ color: TRAIL.ai }}>model flown {counts.flown}</span>
              <span style={{ color: TRAIL.rules }}>below gate {counts.gated}</span>
              <span style={{ color: TRAIL.rules }}>engine error → rules {counts.fallback}</span>
            </div>
            {f.ai_paused && (
              <div className="mt-1.5 rounded-md bg-[#fab219]/15 px-2 py-1 text-[11px] text-[#f2cf7c]">
                AI paused, rules deciding: {(f.pause_reason ?? "").slice(0, 110)}
              </div>
            )}
          </div>
          <div className="min-h-0 flex-1 space-y-2 overflow-y-auto pr-1">
            {decisions.length === 0 ? (
              <div className="rounded-lg bg-black/40 p-3 text-[12px] text-[#c3c2b7]">
                {a && (a.fsm === "REACQUIRE" || a.fsm === "COAST") ? "Subject lost: waiting for the first decision." : "No reacquire decisions yet: the subject is in view."}
              </div>
            ) : (
              decisions.map((d) => <DecisionCard key={d.id} d={d} />)
            )}
          </div>
        </div>
      )}

      {/* bottom-left: legend + controls */}
      <div className="pointer-events-auto absolute bottom-3 left-3 z-10 rounded-xl border border-white/10 bg-black/50 p-3 text-[11px] backdrop-blur-sm">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <span className="text-[#898781]">trail:</span>
          {(
            [
              ["tracking", "following"],
              ["ai", "decision model"],
              ["rules", "rules"],
              ["mock", "mock"],
              ["hold", "hold / manual"],
            ] as const
          ).map(([k, txt]) => (
            <span key={k} className="inline-flex items-center gap-1">
              <i className="inline-block h-[3px] w-4 rounded" style={{ background: TRAIL[k] }} />
              {txt}
            </span>
          ))}
        </div>
        <div className="mt-1 text-[#898781]">
          orange ring = where the lock believes the subject is · X = last seen · dashed = search radius · cyan = camera footprint
          {` · drone drawn ×${mode === "chase" ? 2 : 5}`}
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          <Camera size={13} className="text-[#898781]" />
          {(["chase", "overview", "top"] as CamMode[]).map((m) => (
            <button key={m} onClick={() => pick(m)} className={`rounded-md px-2 py-1 ${mode === m ? "bg-[#9085e9]/30 text-[#c9c2ff]" : "bg-white/5 text-[#c3c2b7] hover:bg-white/10"}`}>
              {m}
            </button>
          ))}
          <span className="mx-1 h-4 w-px bg-white/15" />
          {(
            [
              ["OW7", "Decision-driven search (MuJoCo X2)"],
              ["FR1", "Flood: stranded people in rising water"],
              ["FR2", "Flood: route crew around closed roads"],
              ["FF1", "Fire: route crew outside keep-out zone"],
              ["FF2", "Fire: smoke over building + occupant"],
            ] as const
          ).map(([sid, tip]) => (
            <button
              key={sid}
              onClick={() => control("restart", sid)}
              className={`inline-flex items-center gap-1 rounded-md px-2 py-1 ${f?.scenario === sid ? "bg-[#9085e9]/30 text-[#c9c2ff]" : "bg-white/5 text-[#c3c2b7] hover:bg-white/10"}`}
              title={tip}
            >
              <RotateCcw size={11} /> {sid}
            </button>
          ))}
          <span className="mx-1 h-4 w-px bg-white/15" />
          <button onClick={() => control(f?.paused ? "resume" : "pause")} className="inline-flex items-center gap-1 rounded-md bg-white/5 px-2 py-1 text-[#c3c2b7] hover:bg-white/10">
            {f?.paused ? <Play size={12} /> : <Pause size={12} />} {f?.paused ? "resume" : "pause"}
          </button>
          {[1, 2].map((sp) => (
            <button key={sp} onClick={() => control("speed", sp)} className={`rounded-md px-2 py-1 ${f?.speed === sp ? "bg-white/20 text-[#f0efec]" : "bg-white/5 text-[#c3c2b7] hover:bg-white/10"}`}>
              {sp}×
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

function Chip({ label, tone = "ok" }: { label: string; tone?: "ok" | "warn" | "crit" }) {
  const c = tone === "crit" ? "#d03b3b" : tone === "warn" ? "#fab219" : "#c3c2b7";
  return (
    <span className="rounded-md px-1.5 py-0.5 font-bold" style={{ background: `${c}26`, color: c }}>
      {label}
    </span>
  );
}
