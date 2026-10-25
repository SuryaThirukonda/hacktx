import { useEffect, useRef, useState } from "react";
import { SceneController, type CamMode, type World3D } from "../lib/sim3d";
import type { MapFrame } from "../lib/useSimulationFeed";

export function SimulationScene({ world, frame }: { world: World3D | null; frame: MapFrame | null }) {
  const host = useRef<HTMLDivElement>(null);
  const controller = useRef<SceneController | null>(null);
  const [mode, setMode] = useState<CamMode>("chase");
  useEffect(() => {
    if (!host.current) return;
    const c = new SceneController(host.current, 0);
    controller.current = c;
    c.setMode("chase");
    return () => { c.dispose(); controller.current = null; };
  }, []);
  useEffect(() => {
    if (world && controller.current) {
      controller.current.setWorld(world);
      controller.current.loadDroneMesh(world.drone_mesh);
    }
  }, [world]);
  useEffect(() => { if (frame) controller.current?.update(frame); }, [frame]);
  return <><div ref={host} className="absolute inset-0" />
    <div className="absolute top-3 left-3 flex gap-1 rounded bg-black/60 p-1 text-xs">
      {(["chase", "overview", "top"] as CamMode[]).map((m) => <button key={m} aria-pressed={mode === m}
        onClick={() => { setMode(m); controller.current?.setMode(m); }} className="rounded px-3 py-1">{m}</button>)}
    </div></>;
}
