import { useEffect, useRef, useState } from "react";
import { LocateFixed, Minus, Plus } from "lucide-react";
import type { MapPath, MapPin } from "./FusedMap";
import { withToken } from "../lib/api";
import type { World3D } from "../lib/sim3d";
import type { CityState, MapFrame } from "../lib/useSimulationFeed";
import type { WorldState, XY } from "../types/contracts"; /** The same survey/tracking vocabulary for every simulation; truth is a separate, opt-in layer. */
export function SurveyMap({ world, frame, city, state, spawn, onSpawn, paths = [], pins = [], }: {
    paths?: MapPath[]; pins?: MapPin[];
    world: World3D | null;
    frame: MapFrame | null;
    city: CityState | null;
    state: WorldState | null;
    spawn: XY | null;
    onSpawn?: (xy: XY | null) => void;
}) {
    const canvas = useRef<HTMLCanvasElement>(null), image = useRef<HTMLImageElement | null>(null);
    const transform = useRef({ scale: 1, ox: 0, oy: 0, h: 1, x0: 0, y0: 0 });
    const [size, setSize] = useState({ w: 600, h: 500 }), [revision, setRevision] = useState(0);
    const [surveyed, setSurveyed] = useState(true), [trails, setTrails] = useState(true), [belief, setBelief] = useState(true), [truth, setTruth] = useState(false), [zoom, setZoom] = useState(1);
    useEffect(() => { setZoom(1); image.current = null; if (!world)
        return; const img = new Image(); img.onload = () => { image.current = img; setRevision((v) => v + 1); }; img.src = withToken(world.ortho); return () => { img.onload = null; }; }, [world]);
    useEffect(() => { const c = canvas.current; if (!c)
        return; const observer = new ResizeObserver(([entry]) => setSize({ w: entry.contentRect.width, h: entry.contentRect.height })); observer.observe(c); return () => observer.disconnect(); }, []);
    useEffect(() => {
        const c = canvas.current;
        if (!c || !world)
            return;
        const g = c.getContext("2d");
        if (!g)
            return;
        const dpr = window.devicePixelRatio || 1, W = size.w, H = size.h;
        c.width = W * dpr;
        c.height = H * dpr;
        g.scale(dpr, dpr);
        const [x0, x1, y0, y1] = world.bounds, scale = Math.min((W - 40) / (x1 - x0), (H - 40) / (y1 - y0)) * zoom;
        const ox = (W - (x1 - x0) * scale) / 2, oy = (H - (y1 - y0) * scale) / 2;
        const X = (x: number) => ox + (x - x0) * scale, Y = (y: number) => H - oy - (y - y0) * scale;
        transform.current = { scale, ox, oy, h: H, x0, y0 };
        g.fillStyle = "#101b20";
        g.fillRect(0, 0, W, H);
        if (image.current) {
            g.globalAlpha = 0.55;
            g.drawImage(image.current, X(x0), Y(y1), (x1 - x0) * scale, (y1 - y0) * scale);
            g.globalAlpha = 1;
        }
        {
            for (const tree of world.trees) {
                g.fillStyle = "#284c3d";
                g.beginPath();
                g.arc(X(tree.xy[0]), Y(tree.xy[1]), tree.canopy_r * scale, 0, Math.PI * 2);
                g.fill();
            }
            for (const b of world.boxes) {
                g.save();
                g.translate(X(b.center[0]), Y(b.center[1]));
                g.rotate((b.heading * Math.PI) / 180);
                g.fillStyle = "#5d6670";
                g.fillRect((-b.size[1] * scale) / 2, (-b.size[0] * scale) / 2, b.size[1] * scale, b.size[0] * scale);
                g.restore();
            }
        } // Metric grid, rounded to a useful spacing for small training fields and kilometre-scale forests.
        const step = Math.pow(10, Math.floor(Math.log10((x1 - x0) / 5))) * ((x1 - x0) / 5 / Math.pow(10, Math.floor(Math.log10((x1 - x0) / 5))) > 5 ? 5 : 1);
        g.strokeStyle = "#b4d8de13";
        g.lineWidth = 1;
        g.font = "10px ui-monospace,monospace";
        for (let x = Math.ceil(x0 / step) * step; x <= x1; x += step) {
            g.beginPath();
            g.moveTo(X(x), Y(y0));
            g.lineTo(X(x), Y(y1));
            g.stroke();
            g.fillStyle = "#a5b8bd66";
            g.fillText(`${x} E`, X(x) + 3, Math.max(14, Y(y1) + 13));
        }
        for (let y = Math.ceil(y0 / step) * step; y <= y1; y += step) {
            g.beginPath();
            g.moveTo(X(x0), Y(y));
            g.lineTo(X(x1), Y(y));
            g.stroke();
        }
        const polygon = (points: XY[], fill: string, stroke?: string) => { if (!points.length)
            return; g.beginPath(); points.forEach(([x, y], i) => i ? g.lineTo(X(x), Y(y)) : g.moveTo(X(x), Y(y))); g.closePath(); g.fillStyle = fill; g.fill(); if (stroke) {
            g.strokeStyle = stroke;
            g.stroke();
        } };
        for (const a of frame?.hazards?.areas ?? [])
            polygon(a.polygon, a.kind === "water" ? "#2788c85c" : a.kind === "deep_water" ? "#2165d075" : a.kind === "fire" ? "#ef683b80" : a.kind === "keep_out" ? "#f5b05233" : "#d1c8c833");
        const survey = city?.survey ?? frame?.survey;
        if (surveyed && survey) {
            const age = atob(survey.data);
            for (let row = 0; row < survey.ny; row++)
                for (let col = 0; col < survey.nx; col++) {
                    const value = age.charCodeAt(row * survey.nx + col);
                    if (!value)
                        continue;
                    g.fillStyle = `rgba(79,155,237,${Math.max(0.12, 0.52 - value / 650)})`;
                    const x = survey.x0 + col * survey.cell, y = survey.y0 + (survey.ny - row) * survey.cell;
                    g.fillRect(X(x), Y(y), survey.cell * scale + 0.5, survey.cell * scale + 0.5);
                }
        }
        const line = (points: XY[], color: string, width = 1.5, dash: number[] = []) => { if (!points.length)
            return; g.strokeStyle = color; g.lineWidth = width; g.setLineDash(dash); g.beginPath(); points.forEach(([x, y], i) => i ? g.lineTo(X(x), Y(y)) : g.moveTo(X(x), Y(y))); g.stroke(); g.setLineDash([]); };
        if (belief) {
            g.fillStyle = "#ffb66188";
            for (const [x, y] of city?.belief ?? frame?.pursuit?.particles ?? [])
                g.fillRect(X(x), Y(y), 2.5, 2.5);
        }
        if (trails) {
            if (city?.drone)
                line(city.drone.trail, "#62d9cb88", 1);
            for (const tr of state?.tracks ?? [])
                line(tr.trail.map((p) => [p[0], p[1]]), tr.role === "subject" ? "#ed956388" : "#b2bdc255", 1);
        }
        for (const path of paths) line(path.points, path.color ?? "#79c6f2", 2, path.dashed ? [4, 4] : []);
        const reports = city?.reports ?? [];
        if (trails)
            for (const r of reports) {
                g.fillStyle = "#e779d5";
                g.beginPath();
                g.arc(X(r.xy[0]), Y(r.xy[1]), 2.5, 0, Math.PI * 2);
                g.fill();
            }
        const footprint = city?.drone?.footprint ?? frame?.footprint;
        if (footprint)
            polygon(footprint, "#64dfe516", "#64dfe5");
        if (frame?.crew_route?.waypoints.length)
            line(frame.crew_route.waypoints, "#9ef0bf", 2, [5, 4]);
        const dot = (xy: XY, color: string, text?: string, ring = false) => { g.beginPath(); g.arc(X(xy[0]), Y(xy[1]), ring ? 6 : 4.5, 0, Math.PI * 2); g.fillStyle = color; g.strokeStyle = color; g.lineWidth = 2; ring ? g.stroke() : g.fill(); if (text) {
            g.font = "500 11px system-ui";
            g.lineWidth = 3;
            g.strokeStyle = "#0b151a";
            g.strokeText(text, X(xy[0]) + 10, Y(xy[1]) - 9);
            g.fillStyle = color;
            g.fillText(text, X(xy[0]) + 10, Y(xy[1]) - 9);
        } };
        for (const pin of pins) dot([pin.x, pin.y], "#79c6f2", pin.label);
        for (const tr of state?.tracks ?? [])
            if (tr.ground_xy_m && tr.state !== "EXPIRED")
                dot(tr.ground_xy_m, tr.role === "subject" ? "#ffad70" : "#bdc8c7", tr.role ?? undefined, tr.visibility !== "visible");
        const crew = city?.police?.ping?.xy ?? state?.rescue?.ping?.xy ?? state?.responder?.position_xy_m;
        const target = city?.target?.xy ?? frame?.subject.est_xy;
        if (crew && target)
            line([crew, target], "#a9bdf482", 1, [3, 4]);
        if (target && city)
            dot(target, "#ffad70", city.target?.fresh ? "Tracked subject" : "Last estimate", !city.target?.fresh);
        if (crew)
            dot(crew, "#82a9ff", city ? "Police ping" : "Crew ping");
        if (frame?.beacon)
            dot(frame.beacon.xy, "#ffe196", "Rescue beacon", true);
        if (frame?.drone)
            dot([frame.drone.pos[0], frame.drone.pos[1]], "#6ce9d2", "Drone");
        if (truth) {
            if (city?.truth)
                dot(city.truth.suspect, "#ff6773", "Truth · SIM", true);
            else
                for (const a of frame?.actors ?? [])
                    dot(a.xy, "#ff6773", a.id, true);
        }
        if (spawn)
            dot(spawn, "#ffefa7", "Next start", true);
        g.fillStyle = "#aec2cc";
        g.font = "10px ui-monospace";
        g.fillText("N ↑", W - 35, 25);
        g.strokeStyle = "#aec2cc";
        g.lineWidth = 2;
        g.beginPath();
        g.moveTo(20, H - 25);
        g.lineTo(20 + step * scale, H - 25);
        g.stroke();
        g.fillText(`${step} m`, 20, H - 32);
    }, [world, frame, city, state, size, revision, surveyed, trails, belief, truth, zoom, spawn, paths, pins,]);
    return (<div className="survey-map"> <div className="map-options"> <label> <input type="checkbox" checked={surveyed} onChange={(e) => setSurveyed(e.target.checked)}/> Surveyed </label> <label> <input type="checkbox" checked={trails} onChange={(e) => setTrails(e.target.checked)}/> History </label> <label> <input type="checkbox" checked={belief} onChange={(e) => setBelief(e.target.checked)}/> Search belief </label> <label> <input type="checkbox" checked={truth} onChange={(e) => setTruth(e.target.checked)}/> Truth (sim) </label> </div> <div className="map-canvas-wrap"> <canvas ref={canvas} aria-label="Survey map with camera coverage, estimated tracks, report history and crew pings" onClick={onSpawn ? (e) => { const r = e.currentTarget.getBoundingClientRect(), t = transform.current; onSpawn([t.x0 + (e.clientX - r.left - t.ox) / t.scale, t.y0 + (t.h - t.oy - (e.clientY - r.top)) / t.scale,]); } : undefined}/> {!world && <p className="map-wait">Connecting to map…</p>} <div className="map-zoom"> <button aria-label="Zoom in" onClick={() => setZoom((v) => Math.min(4, v * 1.3))}> <Plus size={15}/> </button> <button aria-label="Zoom out" onClick={() => setZoom((v) => Math.max(1, v / 1.3))}> <Minus size={15}/> </button> <button aria-label="Fit map" onClick={() => setZoom(1)}> <LocateFixed size={15}/> </button> </div> </div> <div className="map-legend"> <span> <i style={{ background: "#6ce9d2" }}/> Drone / view </span> <span> <i style={{ background: "#4f9bed" }}/> Seen ground · fades with time </span> <span> <i style={{ background: "#ffad70" }}/> Subject estimate </span> <span> <i style={{ background: "#82a9ff" }}/> Crew ping </span> <span> <i style={{ background: "#ffe196" }}/> Beacon </span> {city && (<span> <i style={{ background: "#e779d5" }}/> Reports </span>)} </div> <p className="map-note"> {onSpawn ? (spawn ? (<> <button onClick={() => onSpawn(null)}>Clear chosen start</button>{" "} · Restart to apply </>) : ("Click the map to choose the subject’s next start.")) : ("Camera coverage from map visibility. Hidden ground stays unsurveyed.")} </p> </div>);
}
