import { Box, Boxes, Flag, Flame, Pause, Play, RotateCcw, UserCheck, Video } from "lucide-react";
import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { api } from "../../lib/api";
import { actions, useIncident } from "../../lib/incident";
import { useStore } from "../../lib/store";
import type { FrameOverlay } from "../../types/contracts";
import { drawDroneOverlay, hitTestTracks } from "../overlays";
import { Badge, Btn } from "../ui";
import { VideoPane } from "../VideoPane";

const Scene3DPanel = lazy(() => import("./Scene3DPanel").then((m) => ({ default: m.Scene3DPanel })));
const SceneViewer = lazy(() => import("./SceneViewer").then((m) => ({ default: m.SceneViewer })));

const MODE_BADGE: Record<string, [string, string]> = {
  SYNTHETIC: ["SIMULATED", "var(--s7)"],
  RECORDED: ["REPLAY", "var(--warning)"],
  LIVE: ["LIVE", "var(--good)"],
  UNKNOWN: ["NO FRAMES", "var(--ink-3)"],
};

export function CenterView() {
  const [tab, setTab] = useState<"video" | "3d" | "scene">("video");
  const [ov, setOv] = useState<FrameOverlay | null>(null);
  const [thermal, setThermal] = useState(false);
  const [thermalTick, setThermalTick] = useState(0);
  useEffect(() => {
    if (!thermal) return;
    const id = window.setInterval(() => setThermalTick((t) => t + 1), 500);
    return () => window.clearInterval(id);
  }, [thermal]);
  const lastSet = useRef(0);
  const src = useStore((s) => s.state?.sources.find((q) => q.source_id === "drone"));
  const selected = useStore((s) => s.state?.selected_track_id ?? null);
  const designated = useStore((s) => s.state?.locks.subject?.track_id ?? null);
  const live = useStore((s) => s.state?.mode === "LIVE");
  const autonomy = useStore((s) => s.state?.autonomy);
  const follow = useIncident((s) => s.follow);
  const selTrack = useStore((s) => s.state?.tracks.find((t) => t.track_id === s.state?.selected_track_id));
  const paused = useStore((s) => s.state?.sim?.paused ?? false);
  const tel = useStore((s) => s.state?.drone);
  const showToast = useStore((s) => s.showToast);
  const incident = useIncident((s) => s.incident);
  const activeScenario = useIncident((s) => s.activeScenario);
  const mission = incident?.mission_mode ?? "search";
  useEffect(() => {
    // Indoor works on a prior scan; the other missions start on the drone view
    setTab((t) => (mission === "indoor" ? "scene" : t === "scene" ? "video" : t));
  }, [mission]);
  const offline = !src || src.status === "OFFLINE" || src.status === "NOT_CONFIGURED";
  const [label, color] = MODE_BADGE[ov?.provenance ?? src?.provenance ?? "UNKNOWN"] ?? MODE_BADGE.UNKNOWN;

  const run = async (f: () => Promise<unknown>, ok?: string) => {
    try {
      await f();
      if (ok) showToast(ok);
    } catch (e) {
      showToast(String((e as Error).message), "error");
    }
  };

  return (
    <section className="flex h-full min-h-0 flex-col rounded-lg border border-line bg-surface">
      <header className="flex h-9 shrink-0 items-center gap-1 border-b border-line px-2">
        <div role="tablist" aria-label="Center view" className="flex gap-1">
          <TabBtn active={tab === "video"} onClick={() => setTab("video")} icon={<Video size={13} />} label="Drone view" />
          <TabBtn active={tab === "3d"} onClick={() => setTab("3d")} icon={<Box size={13} />} label="3D world" />
          <TabBtn active={tab === "scene"} onClick={() => setTab("scene")} icon={<Boxes size={13} />} label="Scene" />
        </div>
        <Badge color={color} solid className="ml-2">{label}</Badge>
        {ov && <span className="tnum hidden text-[11px] text-ink-3 md:inline">frame #{ov.frame_id} · {ov.detections} detections · {ov.model_id}</span>}
        {!live && <div className="ml-auto flex items-center gap-1">
          {tab === "video" && src?.provenance === "SYNTHETIC" && (
            <Btn active={thermal} title="SIMULATED THERMAL: a false-colour view made from simulator ground truth (no temperatures)" onClick={() => setThermal((v) => !v)}>
              <Flame size={13} /> <span className="hidden sm:inline">Sim thermal</span>
            </Btn>
          )}
          <Btn title={paused ? "Resume" : "Pause"} disabled={!incident} onClick={() => run(() => actions.replay(paused ? "start" : "pause"))}>
            {paused ? <Play size={13} /> : <Pause size={13} />}
            <span className="hidden sm:inline">{paused ? "Play" : "Pause"}</span>
          </Btn>
          <Btn title="Restart the scenario" disabled={!incident} onClick={() => run(() => actions.replay("restart", activeScenario), "Scenario restarted")}>
            <RotateCcw size={13} />
          </Btn>
        </div>}
      </header>

      {!live && designated && autonomy && <div className="flex flex-wrap items-center gap-2 border-b border-line px-2 py-1 text-[12px]">
        <span>{autonomy.mode === "FOLLOW" ? "Follow requested" : "Target marked · movement not started"} · {autonomy.mode}</span>
        <Btn disabled={!autonomy.commands_enabled || follow?.status !== "tracking" || autonomy.mode === "ESTOP"}
          onClick={() => run(() => api.post("/api/autonomy/mode", {mode:"FOLLOW"}), "Follow requested; check aircraft virtual-stick confirmation")}>
          <Play size={12} /> Start {autonomy.motion_profile === "camera_only" ? "camera" : "aircraft"} follow
        </Btn>
        <Btn onClick={() => run(() => api.post("/api/autonomy/mode", {mode:"MANUAL"}), "Movement stop requested")}>Stop · pilot control</Btn>
        {!autonomy.commands_enabled && <span className="text-ink-3">Control unavailable: check Aircraft control connection.</span>}
        {follow?.status === "unverified" && <span className="text-ink-3">Confirm the re-bound target before following.</span>}
      </div>}

      <div className="relative min-h-0 flex-1">
        {tab === "video" ? (
          <VideoPane
            source="drone"
            offline={{ offline, label: src?.status === "STALE" ? "DRONE VIDEO STALE" : "NO DRONE VIDEO", quiet: src?.provenance === "SYNTHETIC" }}
            hitTest={hitTestTracks}
            onFrame={(o) => {
              const now = performance.now();
              if (now - lastSet.current > 400) {
                lastSet.current = now;
                setOv(o);
              }
            }}
            draw={(ctx, o, g, hover) => {
              const st = useStore.getState().state;
              drawDroneOverlay(ctx, o, g, hover, { selected: st?.selected_track_id ?? null, locks: st?.locks, showZones: false, t: performance.now() });
            }}
            onImageClick={(u, v) => {
              const g = useIncident.getState();
              if (g.gcpMode === "image") {
                useIncident.setState({ gcpPending: [u, v], gcpMode: "map" });
                showToast(`Image point ${u.toFixed(0)}, ${v.toFixed(0)} px: now click the same spot on the map`);
                return;
              }
              void run(async () => {
                const r = await api.post<{ ok: boolean; reason?: string }>("/api/click", { u, v, action: "select" });
                if (!r.ok) throw new Error(r.reason ?? "no track there");
              });
            }}
          >
            {thermal && (
              <img
                alt="Simulated thermal view (from simulator ground truth, no temperatures)"
                src={`/api/world/thermal.jpg?t=${thermalTick}`}
                className="pointer-events-none absolute bottom-2 right-2 z-10 w-[38%] rounded border border-[var(--warning)] shadow-lg"
              />
            )}
            <div className="pointer-events-none absolute left-2 top-2 flex flex-col gap-1">
              <Badge color="var(--ink-2)" className="bg-[var(--overlay)]">Click selects a box · Designate target starts a subject lock</Badge>
              {follow?.status === "lost" && (
                <Badge color="var(--warning)" className="bg-[var(--overlay)]">Target out of view · designation retained</Badge>
              )}
              {ov && (
                <Badge color={ov.calibration_valid ? "var(--good)" : "var(--warning)"} className="bg-[var(--overlay)]">
                  {ov.calibration_valid ? `ground transform: ${String(ov.extra?.calibration_source ?? "valid")}` : "no ground transform: positions not verified"}
                </Badge>
              )}
            </div>
          </VideoPane>
        ) : tab === "3d" ? (
          <Suspense fallback={<div className="p-4 text-[12px] text-ink-3">Loading 3D view…</div>}>
            <Scene3DPanel />
          </Suspense>
        ) : (
          <Suspense fallback={<div className="p-4 text-[12px] text-ink-3">Loading scene viewer…</div>}>
            <SceneViewer />
          </Suspense>
        )}
      </div>

      <footer className="flex min-h-10 shrink-0 flex-wrap items-center gap-1.5 border-t border-line px-2 py-1.5 text-[12px]">
        {selTrack ? (
          <>
            <span className="text-ink-3">Selected</span>
            <span className="font-mono text-[11px] text-ink-1">{selTrack.track_id}</span>
            {designated !== selTrack.track_id && <Badge color="var(--warning)">Selection only</Badge>}
            <span className="text-ink-2">
              {selTrack.class_name} · {selTrack.state.toLowerCase()} · {selTrack.ground_xy_m ? `±${selTrack.ground_error_m?.toFixed(1)} m` : "position not verified"}
            </span>
            <div className="ml-auto flex gap-1">
              {!live && mission !== "follow" && (
                <Btn tone="primary" disabled={!incident} onClick={() => run(() => actions.flagTrack(selTrack.track_id), "Flagged for review")}>
                  <Flag size={13} /> Flag as possible person
                </Btn>
              )}
              {selTrack.class_name === "person" && (
                <Btn tone="primary" disabled={!incident} onClick={() => run(async () => {
                  if (mission !== "follow") await actions.setMission("follow");
                  await actions.followDesignate(selTrack.track_id);
                }, "Target designated. Use Start camera follow to request aircraft control.")}>
                  <UserCheck size={13} /> Designate target
                </Btn>
              )}
              <Btn tone="ghost" onClick={() => run(() => api.post(`/api/tracks/none/select`))}>Clear</Btn>
            </div>
          </>
        ) : (
          <span className="text-ink-3">
            {follow?.status === "lost" ? "Designated target is out of view. The designation is retained; confirm any new binding." : selected ? "Selected track expired. Selection alone does not designate a Follow target." : "No selection. Select a person, then choose Designate target to follow them."}
          </span>
        )}
        {tel && (
          <span className="tnum ml-auto hidden text-[11px] text-ink-3 lg:inline">
            alt {tel.alt_m?.toFixed(1) ?? "–"} m · battery {tel.battery_pct?.toFixed(0) ?? "–"}% · gimbal {tel.gimbal_pitch_deg?.toFixed(0) ?? "–"}°
          </span>
        )}
      </footer>
    </section>
  );
}

function TabBtn({ active, onClick, icon, label }: { active: boolean; onClick: () => void; icon: React.ReactNode; label: string }) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      onClick={onClick}
      className={`inline-flex h-7 items-center gap-1.5 rounded-md px-2 text-[12px] font-semibold ${active ? "bg-surface-3 text-ink-1" : "text-ink-2 hover:bg-surface-2"}`}
    >
      {icon}
      {label}
    </button>
  );
}
