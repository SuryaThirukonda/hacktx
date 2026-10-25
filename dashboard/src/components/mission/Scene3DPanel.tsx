import { Droplets, Flame } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { wsUrl } from "../../lib/api";
import { type CamMode, type Frame3D, type FrameHazard3D, SceneController, type World3D } from "../../lib/sim3d";
import { useStore } from "../../lib/store";
import { ReconnectingSocket } from "../../lib/ws";

/** The simulator's ground truth in 3D (drone, camera footprint, people, buildings, water and fire hazards), labelled SIMULATED. */
export function Scene3DPanel() {
  const host = useRef<HTMLDivElement>(null);
  const ctl = useRef<SceneController | null>(null);
  const [mode, setMode] = useState<CamMode>("chase");
  const [unavailable, setUnavailable] = useState(false);
  const [status, setStatus] = useState("connecting");
  const story = useStore((s) => s.storyFocus);
  const [followStory, setFollowStory] = useState(true);

  // the demo page's story: highlight the beat's entity and, if wanted, take its camera preset
  useEffect(() => {
    ctl.current?.setFocus(story?.entity ?? null);
    if (followStory && story?.camera) {
      setMode(story.camera);
      ctl.current?.setMode(story.camera);
    }
  }, [story?.beat, story?.entity, story?.camera, followStory]);
  const [hazard, setHazard] = useState<FrameHazard3D | null>(null);
  const lastHud = useRef(0);

  useEffect(() => {
    if (!host.current) return;
    const c = new SceneController(host.current, 0);
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
          if (!w.hazards) setHazard(null);
          setUnavailable(false);
        } else if (m.type === "frame") {
          const f = raw as Frame3D;
          c.update(f);
          const now = performance.now();
          if (now - lastHud.current > 250) {
            lastHud.current = now;
            setHazard(f.hazards ?? null);
          }
        } else if (m.type === "unavailable") setUnavailable(true);
      },
    });
    return () => {
      s.close();
      c.dispose();
    };
  }, []);

  const pick = (m: CamMode) => {
    setMode(m);
    ctl.current?.setMode(m);
  };

  return (
    <div className="absolute inset-0 overflow-hidden bg-[#1c2530]">
      <div ref={host} className="absolute inset-0" />
      <div className="absolute left-2 top-2 z-10 flex flex-wrap items-center gap-1 rounded-md bg-black/55 p-1 backdrop-blur-sm">
        <span className="rounded bg-[#9085e9]/25 px-1.5 py-0.5 text-[10px] font-bold tracking-wide text-[#c9c2ff]">SIMULATED · ground truth</span>
        {(["chase", "overview", "top"] as CamMode[]).map((m) => (
          <button
            key={m}
            type="button"
            onClick={() => pick(m)}
            className={`rounded px-1.5 py-0.5 text-[11px] font-semibold ${mode === m ? "bg-white/20 text-white" : "text-[#c3c2b7] hover:bg-white/10"}`}
          >
            {m}
          </button>
        ))}
        {story && (
          <button
            type="button"
            onClick={() => setFollowStory(!followStory)}
            title="Let the scenario's story move the camera"
            className={`rounded px-1.5 py-0.5 text-[11px] font-semibold ${followStory ? "bg-white/20 text-white" : "text-[#c3c2b7] hover:bg-white/10"}`}
          >
            story camera
          </button>
        )}
        <span className="px-1 text-[10px] text-[#898781]">{status}</span>
      </div>
      {hazard && (
        <div className="pointer-events-none absolute bottom-2 left-2 z-10 flex flex-wrap items-center gap-2 rounded-md border border-white/10 bg-black/60 px-2.5 py-1.5 text-[11px] text-[#f0efec] backdrop-blur-sm">
          {hazard.kind === "flood" ? (
            <>
              <Droplets size={13} className="text-[#56b4e9]" />
              <span className="font-semibold text-[#8fd4ff]">Flood 3D</span>
              <span className="tnum">level {hazard.level_m.toFixed(2)} m · max depth {hazard.max_depth_m.toFixed(2)} m</span>
              <span className="tnum text-[#ff9f1c]">{hazard.closed_roads.length} road{hazard.closed_roads.length === 1 ? "" : "s"} &gt;0.3 m</span>
              {hazard.crew_route && (
                <span className="tnum text-[#6ee7b7]">route {hazard.crew_route.length_m.toFixed(0)} m (≤{hazard.crew_route.max_water_depth_m.toFixed(2)} m)</span>
              )}
            </>
          ) : (
            <>
              <Flame size={13} className="text-[#ff922b]" />
              <span className="font-semibold text-[#ffb08a]">Fire &amp; smoke 3D</span>
              <span className="tnum">burning {hazard.burning_area_m2.toFixed(0)} m² · keep-out {hazard.keep_out_area_m2.toFixed(0)} m²</span>
              <span className="tnum text-[#f2cf7c]">
                {hazard.openings.filter((o) => o.condition === "unclear").length}/{hazard.openings.length} openings under smoke
              </span>
              {hazard.crew_route && (
                <span className="tnum text-[#6ee7b7]">route → {hazard.crew_route.target_id} ({hazard.crew_route.length_m.toFixed(1)} m)</span>
              )}
            </>
          )}
          <span className="text-[10px] text-[#a9a79f]">· {hazard.label}</span>
        </div>
      )}
      {unavailable && (
        <div className="absolute inset-0 z-10 flex items-center justify-center p-6 text-center text-[13px] text-[#e0a83a]">
          The 3D world shows the simulator; the backend is not in SIMULATED mode.
        </div>
      )}
    </div>
  );
}
