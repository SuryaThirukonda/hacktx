import { useEffect, useState } from "react";
import { api, wsUrl } from "./api";
import { ReconnectingSocket } from "./ws";
import type { Frame3D, World3D } from "./sim3d";
import type { SpokenCallout } from "./useCalloutVoice";
import type { XY } from "../types/contracts"; export interface SurveyGrid { nx: number; ny: number; cell: number; x0: number; y0: number; data: string; seen_m2?: number;
}
export interface CityState { running?: boolean; run_no: number; t: number; phase: string; phase_label?: string; seed?: number; error?: string; drone?: { xy: XY; footprint: XY[] | null; trail: XY[] }; target?: { xy: XY; fresh: boolean } | null; police?: { xy: XY; heading: number; state: string; ping?: { xy: XY; age_s: number } | null; }; callouts?: SpokenCallout[]; events?: { t: number; kind: string; text: string }[]; survey?: SurveyGrid; belief?: [number, number, number][]; truth?: { suspect: XY }; reports?: { xy: XY; t: number }[]; summary?: Record<string, number | string | null>; video?: { fps: number; dropped: number };
}
export type MapFrame = Frame3D & { survey?: SurveyGrid; beacon?: { xy: XY; status?: string } | null };
export function useSimulationFeed(city: boolean, active: boolean) { const [world, setWorld] = useState<World3D | null>(null); const [frame, setFrame] = useState<MapFrame | null>(null); const [state, setState] = useState<CityState | null>(null); const [error, setError] = useState(""); useEffect(() => { setWorld(null); setFrame(null); setState(null); setError(""); if (!active) return; let last = 0; const socket = new ReconnectingSocket( wsUrl(city ? "/ws/test12/sim3d" : "/ws/sim3d"), { onMessage: (raw) => { const m = raw as { type: string }; if (m.type === "world") setWorld(raw as World3D); else if (m.type === "frame" && performance.now() - last > 240) { last = performance.now(); setFrame(raw as MapFrame); } }, }); if (!city) return () => socket.close(); let done = false, timer: number | undefined; const poll = async () => { try { const s = await api.get<CityState>("/api/test12/state"); if (!done) { setState(s); setError(""); } } catch (e) { if (!done) setError(String(e)); } if (!done) timer = window.setTimeout(poll, 250); }; void poll(); return () => { done = true; clearTimeout(timer); socket.close(); }; }, [city, active]); return { world, frame, cityState: state, error };
}
