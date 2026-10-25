import { Crosshair, Layers as LayersIcon, Maximize2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { cssVar, withAlpha } from "../lib/colors";
import { meters, mps, shortId } from "../lib/format";
import { useStore, type Layers } from "../lib/store";
import type { WorldState, XY } from "../types/contracts";
import { hypColor } from "./PursuitCard";
import { Btn, Toggle } from "./ui";

interface WorldMeta {
  available: boolean;
  name?: string;
  bounds?: [number, number, number, number];
  label?: string;
  ortho?: string;
  hazards?: boolean;
  hazard_label?: string | null;
  launchpad?: [number, number] | null;
}

interface HazardArea {
  kind: "water" | "fire" | "keep_out" | "smoke";
  polygon: [number, number][];
}

const HAZARD_STYLE: Record<HazardArea["kind"], { fill: string; stroke: string; dash?: number[] }> = {
  water: { fill: "rgba(70, 120, 190, 0.32)", stroke: "rgba(110, 160, 230, 0.8)" },
  fire: { fill: "rgba(200, 70, 40, 0.38)", stroke: "rgba(240, 110, 60, 0.9)" },
  keep_out: { fill: "rgba(0, 0, 0, 0)", stroke: "rgba(240, 80, 60, 0.95)", dash: [6, 4] },
  smoke: { fill: "rgba(170, 170, 170, 0.22)", stroke: "rgba(200, 200, 200, 0.5)", dash: [2, 3] },
};

interface View {
  cx: number;
  cy: number;
  zoom: number; // px per meter
}

const LAYER_LABELS: Record<keyof Layers, string> = {
  ortho: "Orthophoto (sim)",
  grid: "Grid",
  tracks: "Tracks",
  trails: "Trails",
  uncertainty: "Uncertainty",
  footprint: "Camera footprint",
  lidar: "LiDAR obstacles",
  occupancy: "Occupancy grid",
  zones: "Zones + geofence",
  predicted: "Prediction (assumption)",
};

/** An incident observation drawn on the map. Pending vs confirmed is shown by shape as well as colour. */
export interface MapPin {
  id: string;
  x: number;
  y: number;
  status: "pending" | "confirmed" | "rejected" | "manual";
  label: string;
  uncertainty?: number | null;
  selected?: boolean;
}

/** A polyline in local ground metres; dashed for unverified segments (for example a Follow trail). */
export interface MapPath {
  id: string;
  points: [number, number][];
  dashed?: boolean;
  color?: string;
}

export interface FusedMapProps {
  pins?: MapPin[];
  paths?: MapPath[];
  placing?: boolean;
  onPlace?: (x: number, y: number) => void;
  onPinClick?: (id: string) => void;
}

export function FusedMap({ pins = [], paths = [], placing = false, onPlace, onPinClick }: FusedMapProps = {}) {
  const pinsRef = useRef<MapPin[]>(pins);
  pinsRef.current = pins;
  const pathsRef = useRef<MapPath[]>(paths);
  pathsRef.current = paths;
  const placeRef = useRef({ placing, onPlace, onPinClick });
  placeRef.current = { placing, onPlace, onPinClick };
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const view = useRef<View>({ cx: 10, cy: 6, zoom: 12 });
  const fitted = useRef(false);
  const meta = useRef<WorldMeta | null>(null);
  const ortho = useRef<HTMLImageElement | null>(null);
  const occ = useRef<{ updates: number; canvas: HTMLCanvasElement; origin: XY; cell: number; nx: number; ny: number } | null>(null);
  const drag = useRef<{ x: number; y: number; cx: number; cy: number; moved: boolean } | null>(null);
  const [hover, setHover] = useState<{ x: number; y: number; text: string } | null>(null);
  const [showLayers, setShowLayers] = useState(false);
  const [follow, setFollow] = useState<"none" | "subject" | "responder">("none");
  const followRef = useRef(follow);
  followRef.current = follow;
  const layers = useStore((s) => s.layers);
  const setLayer = useStore((s) => s.setLayer);
  const mapMode = useStore((s) => s.state?.map_mode);

  const hazards = useRef<{ areas: HazardArea[]; label?: string } | null>(null);
  useEffect(() => {
    // the world changes with the scenario: reload its bounds and orthophoto when its name changes
    let stop = false;
    const load = () =>
      api.get<WorldMeta>("/api/world/meta").then((m) => {
        if (stop) return;
        const changed = m.name !== meta.current?.name;
        meta.current = m;
        if (m.available && changed) {
          const img = new Image();
          img.src = m.ortho ?? "/api/world/ortho.jpg";
          img.onload = () => (ortho.current = img);
          fitRef.current?.();
        }
        if (m.hazards) {
          api.get<{ areas: HazardArea[]; label?: string }>("/api/world/hazards").then((h) => (hazards.current = h)).catch(() => undefined);
        } else hazards.current = null;
      }).catch(() => (meta.current = { available: false }));
    void load();
    const id = window.setInterval(load, 2000);
    return () => {
      stop = true;
      window.clearInterval(id);
    };
  }, []);
  const fitRef = useRef<(() => void) | null>(null);

  const fit = () => {
    const c = canvasRef.current;
    if (!c) return;
    const W = c.clientWidth;
    const H = c.clientHeight;
    let b = meta.current?.bounds;
    const st = useStore.getState().state;
    if (!b && st?.geofence?.length) {
      const xs = st.geofence.map((p) => p[0]);
      const ys = st.geofence.map((p) => p[1]);
      b = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
    }
    if (!b) b = [-25, 35, -15, 25];
    const [x0, x1, y0, y1] = b;
    // the training field: fit the active area rather than the whole texture; other worlds: fit the world
    const field = !meta.current?.name || meta.current.name === "training-field";
    const fx0 = field ? Math.max(x0, -12) : x0, fx1 = field ? Math.min(x1, 36) : x1;
    const fy0 = field ? Math.max(y0, -13) : y0, fy1 = field ? Math.min(y1, 27) : y1;
    view.current = { cx: (fx0 + fx1) / 2, cy: (fy0 + fy1) / 2, zoom: Math.min(W / (fx1 - fx0), H / (fy1 - fy0)) * 0.98 };
  };
  fitRef.current = fit;

  // render loop
  useEffect(() => {
    let raf = 0;
    const loop = () => {
      draw();
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const wrap = wrapRef.current;
    const c = canvasRef.current;
    if (!wrap || !c) return;
    const ro = new ResizeObserver(() => {
      const dpr = window.devicePixelRatio || 1;
      const r = wrap.getBoundingClientRect();
      c.width = Math.max(1, Math.round(r.width * dpr));
      c.height = Math.max(1, Math.round(r.height * dpr));
      c.style.width = `${r.width}px`;
      c.style.height = `${r.height}px`;
      if (!fitted.current && r.width > 50) {
        fit();
        fitted.current = true;
      }
    });
    ro.observe(wrap);
    return () => ro.disconnect();
  }, []);

  const toScreen = (x: number, y: number, W: number, H: number): XY => {
    const v = view.current;
    return [W / 2 + (x - v.cx) * v.zoom, H / 2 - (y - v.cy) * v.zoom];
  };
  const toWorld = (sx: number, sy: number, W: number, H: number): XY => {
    const v = view.current;
    return [v.cx + (sx - W / 2) / v.zoom, v.cy - (sy - H / 2) / v.zoom];
  };

  function updateOccupancy() {
    const ex = useStore.getState().extras?.occupancy;
    if (!ex || (occ.current && occ.current.updates === ex.updates)) return;
    const img = new Image();
    img.onload = () => {
      const cv = document.createElement("canvas");
      cv.width = img.width;
      cv.height = img.height;
      const cx = cv.getContext("2d")!;
      cx.drawImage(img, 0, 0);
      const d = cx.getImageData(0, 0, cv.width, cv.height);
      const occCol = cssVar("--s5");
      const r = parseInt(occCol.slice(1, 3), 16), g = parseInt(occCol.slice(3, 5), 16), b = parseInt(occCol.slice(5, 7), 16);
      for (let i = 0; i < d.data.length; i += 4) {
        const v = d.data[i];
        if (v === 0) {
          d.data[i + 3] = 0; // unknown: transparent (never painted as free)
        } else if (v < 128) {
          d.data[i] = 120; d.data[i + 1] = 200; d.data[i + 2] = 160; d.data[i + 3] = 38; // observed free
        } else {
          d.data[i] = r; d.data[i + 1] = g; d.data[i + 2] = b; d.data[i + 3] = Math.min(230, (v - 128) * 1.9);
        }
      }
      cx.putImageData(d, 0, 0);
      occ.current = { updates: ex.updates, canvas: cv, origin: ex.origin_xy, cell: ex.cell_m, nx: ex.nx, ny: ex.ny };
    };
    img.src = `data:image/png;base64,${ex.png_b64}`;
  }

  function draw() {
    const c = canvasRef.current;
    if (!c) return;
    const ctx = c.getContext("2d");
    if (!ctx) return;
    const dpr = window.devicePixelRatio || 1;
    const W = c.width / dpr;
    const H = c.height / dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const st = useStore.getState().state;
    const L = useStore.getState().layers;
    const now = performance.now();
    ctx.fillStyle = cssVar("--surface-2");
    ctx.fillRect(0, 0, W, H);
    if (!st) return;
    // follow mode
    const f = followRef.current;
    if (f === "subject") {
      const p = st.locks.subject?.predicted_xy_m;
      if (p) { view.current.cx += (p[0] - view.current.cx) * 0.08; view.current.cy += (p[1] - view.current.cy) * 0.08; }
    } else if (f === "responder" && st.responder.position_xy_m) {
      const p = st.responder.position_xy_m;
      view.current.cx += (p[0] - view.current.cx) * 0.08;
      view.current.cy += (p[1] - view.current.cy) * 0.08;
    }
    const S = (x: number, y: number) => toScreen(x, y, W, H);
    const z = view.current.zoom;
    // 1. orthophoto (synthetic, simulator only)
    if (L.ortho && ortho.current && meta.current?.bounds) {
      const [x0, x1, y0, y1] = meta.current.bounds;
      const [sx, sy] = S(x0, y1);
      ctx.globalAlpha = 0.78;
      ctx.drawImage(ortho.current, sx, sy, (x1 - x0) * z, (y1 - y0) * z);
      ctx.globalAlpha = 1;
    }
    // 1b. hazard areas (simulator screening models: water, fire, keep-out, smoke)
    for (const a of hazards.current?.areas ?? []) {
      if (a.polygon.length < 3) continue;
      const st2 = HAZARD_STYLE[a.kind];
      ctx.beginPath();
      a.polygon.forEach(([x, y], i) => { const [sx, sy] = S(x, y); if (i) ctx.lineTo(sx, sy); else ctx.moveTo(sx, sy); });
      ctx.closePath();
      ctx.fillStyle = st2.fill;
      ctx.fill();
      ctx.strokeStyle = st2.stroke;
      ctx.lineWidth = 1.5;
      if (st2.dash) ctx.setLineDash(st2.dash);
      ctx.stroke();
      ctx.setLineDash([]);
    }
    // 2. occupancy
    if (L.occupancy) {
      updateOccupancy();
      const o = occ.current;
      if (o && st.registration.valid) {
        const [sx, sy] = S(o.origin[0], o.origin[1] + o.ny * o.cell);
        ctx.imageSmoothingEnabled = false;
        ctx.drawImage(o.canvas, sx, sy, o.nx * o.cell * z, o.ny * o.cell * z);
        ctx.imageSmoothingEnabled = true;
      }
    }
    // 3. grid
    if (L.grid) {
      const [wx0, wy1] = toWorld(0, 0, W, H);
      const [wx1, wy0] = toWorld(W, H, W, H);
      const step = z > 18 ? 1 : z > 6 ? 5 : 10;
      ctx.lineWidth = 1;
      for (let x = Math.floor(wx0 / step) * step; x <= wx1; x += step) {
        const [sx] = S(x, 0);
        ctx.strokeStyle = x % (step * 2) === 0 ? withAlpha(cssVar("--ink-3"), 0.32) : withAlpha(cssVar("--ink-3"), 0.16);
        ctx.beginPath(); ctx.moveTo(sx, 0); ctx.lineTo(sx, H); ctx.stroke();
      }
      for (let y = Math.floor(wy0 / step) * step; y <= wy1; y += step) {
        const [, sy] = S(0, y);
        ctx.strokeStyle = y % (step * 2) === 0 ? withAlpha(cssVar("--ink-3"), 0.32) : withAlpha(cssVar("--ink-3"), 0.16);
        ctx.beginPath(); ctx.moveTo(0, sy); ctx.lineTo(W, sy); ctx.stroke();
      }
      // origin axes
      const [ox, oy] = S(0, 0);
      ctx.strokeStyle = withAlpha(cssVar("--ink-2"), 0.5);
      ctx.beginPath(); ctx.moveTo(ox, 0); ctx.lineTo(ox, H); ctx.moveTo(0, oy); ctx.lineTo(W, oy); ctx.stroke();
    }
    // 3b. the launchpad (where a dispatched drone takes off from and reports relative to) and the search legs
    const pad = meta.current?.launchpad;
    if (pad) {
      const [px, py] = S(pad[0], pad[1]);
      ctx.beginPath();
      ctx.arc(px, py, 7, 0, Math.PI * 2);
      ctx.fillStyle = "rgba(210,212,214,0.9)";
      ctx.fill();
      ctx.strokeStyle = "#2b2d31";
      ctx.lineWidth = 1.5;
      ctx.stroke();
      txt(ctx, "H", px - 3.5, py + 3.5, "#2b2d31", 10, true);
      txt(ctx, "LAUNCHPAD", px + 10, py + 4, "rgba(220,222,224,0.9)", 10, true);
    }
    // 4. geofence + zones
    if (L.zones) {
      if (st.geofence.length >= 3) {
        ctx.beginPath();
        st.geofence.forEach(([x, y], i) => { const [sx, sy] = S(x, y); if (i) ctx.lineTo(sx, sy); else ctx.moveTo(sx, sy); });
        ctx.closePath();
        ctx.setLineDash([10, 6]);
        ctx.strokeStyle = cssVar("--warning");
        ctx.lineWidth = 2;
        ctx.stroke();
        ctx.setLineDash([]);
        const top = st.geofence.reduce((a, p) => (p[1] > a[1] || (p[1] === a[1] && p[0] < a[0]) ? p : a), st.geofence[0]);
        const [gx, gy] = S(top[0], top[1]);
        txt(ctx, "GEOFENCE", Math.max(6, gx + 6), Math.max(70, gy + 14), cssVar("--warning"), 10, true);
      }
      for (const zn of st.zones) {
        ctx.beginPath();
        zn.polygon_xy_m.forEach(([x, y], i) => { const [sx, sy] = S(x, y); if (i) ctx.lineTo(sx, sy); else ctx.moveTo(sx, sy); });
        ctx.closePath();
        ctx.fillStyle = "rgba(255,255,255,0.05)";
        ctx.fill();
        ctx.strokeStyle = "rgba(255,255,255,0.7)";
        ctx.lineWidth = 1.5;
        ctx.setLineDash([5, 4]);
        ctx.stroke();
        ctx.setLineDash([]);
        const [lx, ly] = S(zn.polygon_xy_m[3]?.[0] ?? zn.polygon_xy_m[0][0], zn.polygon_xy_m[3]?.[1] ?? zn.polygon_xy_m[0][1]);
        txt(ctx, zn.name, lx + 4, ly + 13, "rgba(255,255,255,0.85)");
      }
    }
    // 5. calibration markers (surveyed positions)
    for (const m of st.calibration.markers) {
      if (!m.world_xy) continue;
      const [sx, sy] = S(m.world_xy[0], m.world_xy[1]);
      const col = m.holdout ? cssVar("--s7") : cssVar("--s3");
      ctx.strokeStyle = col;
      ctx.lineWidth = 2;
      ctx.strokeRect(sx - 5, sy - 5, 10, 10);
      txt(ctx, `M${m.id}${m.holdout ? "h" : ""}`, sx + 7, sy + 4, col, 10);
    }
    // 6. drone footprint + drone
    const d = st.drone;
    if (d && L.footprint && d.footprint_xy_m && d.footprint_xy_m.length > 2) {
      ctx.beginPath();
      d.footprint_xy_m.forEach(([x, y], i) => { const [sx, sy] = S(x, y); if (i) ctx.lineTo(sx, sy); else ctx.moveTo(sx, sy); });
      ctx.closePath();
      ctx.fillStyle = withAlpha(cssVar("--s3"), 0.08);
      ctx.fill();
      ctx.strokeStyle = withAlpha(cssVar("--s3"), 0.75);
      ctx.lineWidth = 1.5;
      ctx.stroke();
    }
    if (d && d.position_xy_m) {
      const [sx, sy] = S(d.position_xy_m[0], d.position_xy_m[1]);
      const h = ((d.yaw_deg ?? 0) * Math.PI) / 180;
      ctx.save();
      ctx.translate(sx, sy);
      ctx.rotate(h);
      ctx.fillStyle = cssVar("--s3");
      ctx.strokeStyle = cssVar("--surface");
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(0, -13); ctx.lineTo(9, 9); ctx.lineTo(0, 4); ctx.lineTo(-9, 9); ctx.closePath();
      ctx.stroke(); ctx.fill();
      ctx.restore();
      txt(ctx, `DRONE ${d.alt_m !== null ? d.alt_m.toFixed(0) + " m" : ""}${d.sim ? " (sim)" : ""}`, sx + 12, sy - 8, cssVar("--s3"), 11, true);
    }
    // 7. phone frustum
    if (st.phone.frustum_xy_m && st.registration.valid) {
      ctx.beginPath();
      st.phone.frustum_xy_m.forEach(([x, y], i) => { const [sx, sy] = S(x, y); if (i) ctx.lineTo(sx, sy); else ctx.moveTo(sx, sy); });
      ctx.closePath();
      ctx.fillStyle = withAlpha(cssVar("--s1"), 0.12);
      ctx.fill();
      ctx.strokeStyle = withAlpha(cssVar("--s1"), 0.5);
      ctx.lineWidth = 1;
      ctx.stroke();
    }
    // 8. LiDAR obstacles (registered only)
    if (L.lidar && st.registration.valid) {
      const col = cssVar("--s5");
      for (const [ox, oy, ageMs] of st.obstacles) {
        const age = ageMs / 8000;
        const [sx, sy] = S(ox, oy);
        ctx.fillStyle = withAlpha(col, Math.max(0.15, 0.9 - age));
        ctx.fillRect(sx - 2, sy - 2, 4, 4);
      }
    }
    // 9. tracks
    const selected = st.selected_track_id;
    if (L.tracks) {
      for (const t of st.tracks) {
        if (!t.ground_xy_m) continue;
        const col = t.role === "subject" ? cssVar("--s2") : t.role === "responder" ? cssVar("--s1") : cssVar("--ink-2");
        if (L.trails && t.trail.length > 1) {
          ctx.beginPath();
          t.trail.forEach(([x, y], i) => { const [sx, sy] = S(x, y); if (i) ctx.lineTo(sx, sy); else ctx.moveTo(sx, sy); });
          ctx.strokeStyle = withAlpha(col.startsWith("#") ? col : "#c3c2b7", t.role ? 0.75 : 0.35);
          ctx.lineWidth = t.role ? 2 : 1.25;
          if (t.visibility !== "visible") ctx.setLineDash([4, 4]);
          ctx.stroke();
          ctx.setLineDash([]);
        }
        const [sx, sy] = S(t.ground_xy_m[0], t.ground_xy_m[1]);
        if (L.uncertainty && t.ground_error_m) {
          ctx.beginPath();
          ctx.arc(sx, sy, Math.max(3, t.ground_error_m * z), 0, Math.PI * 2);
          ctx.fillStyle = withAlpha(col.startsWith("#") ? col : "#c3c2b7", 0.12);
          ctx.fill();
        }
        if (t.velocity_xy_mps && t.speed_mps && t.speed_mps > 0.3) {
          const [ex, ey] = S(t.ground_xy_m[0] + t.velocity_xy_mps[0], t.ground_xy_m[1] + t.velocity_xy_mps[1]);
          ctx.beginPath(); ctx.moveTo(sx, sy); ctx.lineTo(ex, ey);
          ctx.strokeStyle = col; ctx.lineWidth = 2; ctx.stroke();
        }
        ctx.beginPath();
        ctx.arc(sx, sy, t.role ? 7 : 5, 0, Math.PI * 2);
        ctx.fillStyle = col;
        ctx.fill();
        ctx.lineWidth = 2;
        ctx.strokeStyle = t.track_id === selected ? "#ffffff" : cssVar("--surface");
        ctx.stroke();
        const lbl = `${t.role ? t.role.toUpperCase() + " " : ""}${shortId(t.track_id)}${t.speed_mps !== null && t.speed_mps > 0.3 ? " " + t.speed_mps.toFixed(1) + " m/s" : ""}`;
        const dupResponder = t.role === "responder" && st.responder.position_source === "drone_lock";
        if (!dupResponder && (t.role || t.track_id === selected || z > 14)) txt(ctx, lbl, sx + 9, sy - 8, t.role ? col : cssVar("--ink-1"), 11, true);
      }
    }
    // 10. subject lock: prediction + search radius + last seen
    const sl = st.locks.subject;
    if (sl && sl.state !== "UNLOCKED") {
      const col = cssVar("--s2");
      if (L.predicted && st.kinematics.valid && st.kinematics.predicted_path_xy_m.length) {
        const ss = st.tracks.find((t) => t.role === "subject")?.ground_xy_m;
        ctx.beginPath();
        if (ss) { const [a, b] = S(ss[0], ss[1]); ctx.moveTo(a, b); }
        st.kinematics.predicted_path_xy_m.forEach(([x, y], i) => { const [sx, sy] = S(x, y); if (i || ss) ctx.lineTo(sx, sy); else ctx.moveTo(sx, sy); });
        ctx.setLineDash([3, 4]);
        ctx.strokeStyle = withAlpha(col, 0.9);
        ctx.lineWidth = 2;
        ctx.stroke();
        ctx.setLineDash([]);
      }
      if (sl.state !== "LOCKED" && sl.predicted_xy_m && sl.search_radius_m) {
        const [sx, sy] = S(sl.predicted_xy_m[0], sl.predicted_xy_m[1]);
        ctx.beginPath();
        ctx.arc(sx, sy, sl.search_radius_m * z, 0, Math.PI * 2);
        ctx.setLineDash([6, 6]);
        ctx.strokeStyle = withAlpha(col, 0.8);
        ctx.lineWidth = 1.5;
        ctx.stroke();
        ctx.setLineDash([]);
        txt(ctx, `SEARCH r=${sl.search_radius_m.toFixed(0)} m (${sl.state})`, sx + 6, sy - sl.search_radius_m * z - 4, col, 11, true);
      }
      if (sl.state !== "LOCKED" && sl.last_seen_xy_m) {
        const [sx, sy] = S(sl.last_seen_xy_m[0], sl.last_seen_xy_m[1]);
        ctx.strokeStyle = col;
        ctx.lineWidth = 2.5;
        ctx.beginPath(); ctx.moveTo(sx - 6, sy - 6); ctx.lineTo(sx + 6, sy + 6); ctx.moveTo(sx + 6, sy - 6); ctx.lineTo(sx - 6, sy + 6); ctx.stroke();
        txt(ctx, "last seen", sx + 8, sy + 14, col, 10);
      }
    }
    // 11. responder
    const r = st.responder;
    if (r.position_xy_m) {
      const col = cssVar("--s1");
      const [sx, sy] = S(r.position_xy_m[0], r.position_xy_m[1]);
      if (L.uncertainty && r.position_error_m) {
        ctx.beginPath();
        ctx.arc(sx, sy, Math.max(4, r.position_error_m * z), 0, Math.PI * 2);
        ctx.fillStyle = withAlpha(col, 0.12);
        ctx.fill();
        ctx.strokeStyle = withAlpha(col, 0.4);
        ctx.lineWidth = 1;
        ctx.stroke();
      }
      if (r.heading_deg !== null) {
        const h = ((r.heading_deg - 90) * Math.PI) / 180;
        ctx.beginPath();
        ctx.moveTo(sx, sy);
        ctx.arc(sx, sy, 34, h - 0.5, h + 0.5);
        ctx.closePath();
        ctx.fillStyle = withAlpha(col, 0.28);
        ctx.fill();
      }
      ctx.beginPath();
      ctx.arc(sx, sy, 8, 0, Math.PI * 2);
      ctx.fillStyle = col;
      ctx.fill();
      ctx.lineWidth = 2.5;
      ctx.strokeStyle = "#ffffff";
      ctx.stroke();
      txt(ctx, `RESPONDER${r.track_id ? " " + shortId(r.track_id) : ""} · ${r.position_source.replace("_", " ")}`, sx + 11, sy + 16, col, 11, true);
      // 12. range line
      const k = st.kinematics;
      const subjXY = st.tracks.find((t) => t.role === "subject")?.ground_xy_m;
      if (k.valid && subjXY) {
        const [ax, ay] = S(subjXY[0], subjXY[1]);
        ctx.beginPath(); ctx.moveTo(sx, sy); ctx.lineTo(ax, ay);
        ctx.strokeStyle = withAlpha(cssVar("--ink-1"), 0.6);
        ctx.setLineDash([2, 3]);
        ctx.lineWidth = 1.5;
        ctx.stroke();
        ctx.setLineDash([]);
        const mx = (sx + ax) / 2, my = (sy + ay) / 2;
        txt(ctx, `${k.range_m?.toFixed(1)} m · ${k.clock ?? "?"} o'clock${k.closing_mps !== null && k.closing_mps > 0.5 ? ` · closing ${k.closing_mps.toFixed(1)} m/s` : ""}`, mx + 6, my - 6, cssVar("--ink-1"), 11, true);
      }
    }
    // 13. critical alert pulse at the subject
    if (st.alerts_active.some((a) => a.severity === "critical" && !a.acked)) {
      const sxy = st.tracks.find((t) => t.role === "subject")?.ground_xy_m;
      if (sxy) {
        const [sx, sy] = S(sxy[0], sxy[1]);
        const ph = (now % 1000) / 1000;
        ctx.beginPath();
        ctx.arc(sx, sy, 10 + ph * 34, 0, Math.PI * 2);
        ctx.strokeStyle = withAlpha(cssVar("--critical"), 1 - ph);
        ctx.lineWidth = 3;
        ctx.stroke();
      }
    }
    // 12. pursuit belief while the subject is lost: particles per identity hypothesis, the candidate viewpoints,
    // and the camera footprint of the chosen one
    const pu = st.pursuit;
    if (pu?.active) {
      for (const [x, y, k] of pu.particles) {
        const [sx, sy] = S(x, y);
        ctx.fillStyle = withAlpha(hypColor(k), 0.55);
        ctx.fillRect(sx - 1.5, sy - 1.5, 3, 3);
      }
      const pick = pu.route?.candidate_id;
      for (const c of pu.candidates.slice(0, 5)) {
        const [sx, sy] = S(c.target_xy[0], c.target_xy[1]);
        const chosen = c.id === pick;
        if (chosen && c.footprint && c.footprint.length > 2) {
          ctx.beginPath();
          c.footprint.forEach(([x, y], i) => { const [fx, fy] = S(x, y); if (i) ctx.lineTo(fx, fy); else ctx.moveTo(fx, fy); });
          ctx.closePath();
          ctx.fillStyle = "rgba(90, 210, 230, 0.10)";
          ctx.fill();
          ctx.strokeStyle = "rgba(90, 210, 230, 0.55)";
          ctx.lineWidth = 1;
          ctx.stroke();
        }
        ctx.beginPath();
        ctx.arc(sx, sy, chosen ? 6 : 4, 0, Math.PI * 2);
        ctx.strokeStyle = c.unsafe ? withAlpha(cssVar("--critical"), 0.8) : chosen ? "rgba(90, 210, 230, 1)" : withAlpha(cssVar("--ink-2"), 0.8);
        ctx.lineWidth = chosen ? 2 : 1.2;
        ctx.stroke();
        txt(ctx, `${c.id} ${Math.round(100 * c.coverage)}%`, sx + 7, sy - 5, chosen ? "#9fe7f2" : cssVar("--ink-2"), 10, true);
      }
    }
    // 12c. the live crew route through flood water or fire (a proposal for a person; nothing moves anyone)
    const cr = st.crew_routing;
    const crw = cr?.route as { waypoints?: [number, number][]; id?: string; label?: string; length_m?: number; max_water_depth_m?: number; target?: string } | null | undefined;
    if (crw?.waypoints && crw.waypoints.length > 1) {
      ctx.beginPath();
      crw.waypoints.forEach(([x, y], i) => { const [sx, sy] = S(x, y); if (i) ctx.lineTo(sx, sy); else ctx.moveTo(sx, sy); });
      ctx.strokeStyle = "rgba(61, 220, 132, 0.95)";
      ctx.lineWidth = 3;
      ctx.stroke();
      const [lx, ly] = S(crw.waypoints[crw.waypoints.length - 1][0], crw.waypoints[crw.waypoints.length - 1][1]);
      txt(ctx, `crew ${crw.label ?? crw.id ?? ""} ${crw.length_m?.toFixed(0) ?? ""} m${cr?.kind === "flood" && crw.max_water_depth_m != null ? ` · ≤${crw.max_water_depth_m.toFixed(2)} m water` : ""}`, lx + 6, ly - 6, "#8ff0b8", 10, true);
    } else if (cr && !crw && cr.why) {
      txt(ctx, `crew: ${cr.why}`, 10, H - 14, "#ff9c7a", 11, true);
    }
    // 12a. pursuit route being flown (simulator) or advised: dashed cyan to each waypoint
    const route = st.autonomy?.route;
    if (route && route.length && st.drone?.position_xy_m) {
      ctx.beginPath();
      const [dx0, dy0] = S(st.drone.position_xy_m[0], st.drone.position_xy_m[1]);
      ctx.moveTo(dx0, dy0);
      for (const [x, y] of route) { const [sx, sy] = S(x, y); ctx.lineTo(sx, sy); }
      ctx.setLineDash([6, 4]);
      ctx.strokeStyle = "rgba(90, 210, 230, 0.9)";
      ctx.lineWidth = 2;
      ctx.stroke();
      ctx.setLineDash([]);
      const [ex, ey] = S(route[route.length - 1][0], route[route.length - 1][1]);
      ctx.beginPath();
      ctx.arc(ex, ey, 5, 0, Math.PI * 2);
      ctx.strokeStyle = "rgba(90, 210, 230, 0.9)";
      ctx.stroke();
    }
    // 12b. object memory: hidden people as fading circles (their possible area now), labelled with state and age
    if (L.tracks) {
      for (const m of st.memory ?? []) {
        if (m.state === "VISIBLE") continue;
        const [sx, sy] = S(m.xy[0], m.xy[1]);
        const a = Math.max(0.2, m.confidence);
        ctx.beginPath();
        ctx.arc(sx, sy, Math.max(4, m.radius_m * z), 0, Math.PI * 2);
        ctx.setLineDash([4, 4]);
        ctx.strokeStyle = `rgba(240, 200, 90, ${a.toFixed(2)})`;
        ctx.lineWidth = 1.5;
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillStyle = `rgba(240, 200, 90, ${a.toFixed(2)})`;
        ctx.fillRect(sx - 2.5, sy - 2.5, 5, 5);
        ctx.font = "600 10px system-ui, sans-serif";
        ctx.fillText(`${m.object_id} ${m.state.toLowerCase().replace("_", " ")} ${m.since_seen_s.toFixed(0)} s`, sx + 6, sy - 6);
      }
    }
    // 13a. incident paths (Follow trail: solid = confirmed target, dashed = unverified binding)
    for (const pa of pathsRef.current) {
      if (pa.points.length < 2) continue;
      ctx.beginPath();
      pa.points.forEach(([x, y], i) => { const [sx, sy] = toScreen(x, y, W, H); if (i) ctx.lineTo(sx, sy); else ctx.moveTo(sx, sy); });
      ctx.strokeStyle = pa.color ?? cssVar("--s2");
      ctx.lineWidth = 2.5;
      if (pa.dashed) ctx.setLineDash([5, 4]);
      ctx.stroke();
      ctx.setLineDash([]);
    }
    // 13b. incident observations: confirmed = filled, pending = hollow dashed, manual = diamond (MANUAL)
    for (const pn of pinsRef.current) {
      const [px, py] = toScreen(pn.x, pn.y, W, H);
      const col = pn.status === "confirmed" ? cssVar("--good") : pn.status === "manual" ? cssVar("--s4")
        : pn.status === "rejected" ? cssVar("--ink-3") : cssVar("--warning");
      if (pn.uncertainty && pn.uncertainty > 0) {
        ctx.beginPath();
        ctx.arc(px, py, Math.max(6, pn.uncertainty * z), 0, Math.PI * 2);
        ctx.fillStyle = withAlpha(col, 0.12);
        ctx.fill();
      }
      ctx.save();
      ctx.lineWidth = pn.selected ? 3 : 2;
      ctx.strokeStyle = col;
      ctx.fillStyle = col;
      if (pn.status === "manual") {
        ctx.beginPath(); ctx.moveTo(px, py - 8); ctx.lineTo(px + 8, py); ctx.lineTo(px, py + 8); ctx.lineTo(px - 8, py); ctx.closePath();
        ctx.fill();
      } else {
        ctx.beginPath();
        ctx.arc(px, py, 7, 0, Math.PI * 2);
        if (pn.status === "confirmed") ctx.fill();
        else { ctx.setLineDash([3, 2]); ctx.stroke(); }
      }
      ctx.restore();
      if (pn.selected) { ctx.beginPath(); ctx.arc(px, py, 12, 0, Math.PI * 2); ctx.strokeStyle = cssVar("--ink-1"); ctx.lineWidth = 1.5; ctx.stroke(); }
      txt(ctx, pn.label, px + 11, py + 4, cssVar("--ink-1"), 11, true);
    }

    // 14. chrome: scale bar, north arrow, provenance + mode watermark
    const meter = z;
    let barM = 10;
    if (meter * barM > W * 0.3) barM = 5;
    if (meter * barM > W * 0.3) barM = 2;
    ctx.fillStyle = cssVar("--ink-1");
    ctx.fillRect(14, H - 18, barM * meter, 3);
    txt(ctx, `${barM} m`, 14, H - 24, cssVar("--ink-1"), 11, true);
    ctx.save();
    ctx.translate(W - 22, 66);
    ctx.fillStyle = cssVar("--ink-1");
    ctx.beginPath(); ctx.moveTo(0, -12); ctx.lineTo(6, 6); ctx.lineTo(0, 2); ctx.lineTo(-6, 6); ctx.closePath(); ctx.fill();
    ctx.restore();
    txt(ctx, "N", W - 26, 48, cssVar("--ink-1"), 11, true);
    if (st.map_mode === "UNCALIBRATED" || st.map_mode === "PHONE_ONLY") {
      const phoneRegistered = st.map_mode === "PHONE_ONLY" && st.registration.valid;
      ctx.save();
      ctx.translate(W / 2, H / 2);
      ctx.rotate(-0.35);
      ctx.font = "800 30px system-ui, sans-serif";
      ctx.textAlign = "center";
      ctx.fillStyle = withAlpha(cssVar("--critical"), 0.35);
      ctx.fillText(phoneRegistered ? "DRONE FEED NOT CURRENT" : "NOT REGISTERED", 0, 0);
      ctx.font = "600 13px system-ui, sans-serif";
      ctx.fillText(phoneRegistered ? "aerial tracks are stale: only the registered phone layer is current"
        : "no validated ground transform: positions are not metric", 0, 24);
      ctx.restore();
    }
  }

  return (
    <div ref={wrapRef} className="absolute inset-0 overflow-hidden">
      <canvas
        ref={canvasRef}
        className={`block ${placing ? "cursor-crosshair" : "cursor-grab active:cursor-grabbing"}`}
        onWheel={(e) => {
          const c = canvasRef.current!;
          const rect = c.getBoundingClientRect();
          const W = rect.width, H = rect.height;
          const before = toWorld(e.clientX - rect.left, e.clientY - rect.top, W, H);
          view.current.zoom = Math.min(120, Math.max(2, view.current.zoom * (e.deltaY < 0 ? 1.15 : 1 / 1.15)));
          const after = toWorld(e.clientX - rect.left, e.clientY - rect.top, W, H);
          view.current.cx += before[0] - after[0];
          view.current.cy += before[1] - after[1];
        }}
        onMouseDown={(e) => (drag.current = { x: e.clientX, y: e.clientY, cx: view.current.cx, cy: view.current.cy, moved: false })}
        onMouseUp={(e) => {
          const dd = drag.current;
          drag.current = null;
          if (dd && !dd.moved) {
            const c = canvasRef.current!;
            const rect = c.getBoundingClientRect();
            const sx = e.clientX - rect.left, sy = e.clientY - rect.top;
            const pr = placeRef.current;
            if (pr.placing && pr.onPlace) {
              const [wx, wy] = toWorld(sx, sy, rect.width, rect.height);
              pr.onPlace(wx, wy);
              return;
            }
            if (pr.onPinClick) {
              const hitPin = pinsRef.current.find((pn) => {
                const [px, py] = toScreen(pn.x, pn.y, rect.width, rect.height);
                return Math.hypot(px - sx, py - sy) < 10;
              });
              if (hitPin) {
                pr.onPinClick(hitPin.id);
                return;
              }
            }
            const st = useStore.getState().state;
            const hit = st ? pick(st, e.clientX - rect.left, e.clientY - rect.top, rect.width, rect.height) : null;
            api.post(`/api/tracks/${encodeURIComponent(hit ?? "none")}/select`).catch(() => undefined);
          }
        }}
        onMouseMove={(e) => {
          const c = canvasRef.current!;
          const rect = c.getBoundingClientRect();
          const dd = drag.current;
          if (dd) {
            const dx = e.clientX - dd.x, dy = e.clientY - dd.y;
            if (Math.abs(dx) + Math.abs(dy) > 3) dd.moved = true;
            view.current.cx = dd.cx - dx / view.current.zoom;
            view.current.cy = dd.cy + dy / view.current.zoom;
            setFollow("none");
            return;
          }
          const st = useStore.getState().state;
          if (!st) return;
          const id = pick(st, e.clientX - rect.left, e.clientY - rect.top, rect.width, rect.height);
          const t = st.tracks.find((q) => q.track_id === id);
          const [wx, wy] = toWorld(e.clientX - rect.left, e.clientY - rect.top, rect.width, rect.height);
          setHover({
            x: e.clientX - rect.left,
            y: e.clientY - rect.top,
            text: t
              ? `${t.role ? t.role + " " : ""}${shortId(t.track_id)} · ${t.class_name} ${t.confidence.toFixed(2)} · ±${meters(t.ground_error_m)} · ${mps(t.speed_mps)} · ${t.visibility}`
              : `x ${wx.toFixed(1)} m, y ${wy.toFixed(1)} m`,
          });
        }}
        onMouseLeave={() => {
          drag.current = null;
          setHover(null);
        }}
      />
      {hover && (
        <div className="pointer-events-none absolute z-10 rounded border border-line-strong bg-[var(--overlay)] px-2 py-1 text-[11px] text-ink-1 backdrop-blur" style={{ left: hover.x + 12, top: hover.y + 12 }}>
          {hover.text}
        </div>
      )}
      <div className="absolute left-2 top-2 flex flex-col gap-1">
        <span className="rounded bg-[var(--overlay)] px-2 py-0.5 text-[11px] font-semibold text-ink-1 backdrop-blur">
          {mapMode === "REGISTERED_METRIC" ? "REGISTERED · METRIC" : mapMode === "DRONE_LOCAL" ? "DRONE-LOCAL METRIC · phone not registered" : mapMode === "PHONE_ONLY" ? "PHONE ONLY · drone not current" : "UNCALIBRATED"}
        </span>
        {meta.current?.available && <span className="rounded bg-[var(--overlay)] px-2 py-0.5 text-[10px] text-ink-3 backdrop-blur">background: synthetic orthophoto</span>}
        {hazards.current?.label && <span className="rounded bg-[var(--overlay)] px-2 py-0.5 text-[10px] text-[var(--warning)] backdrop-blur">{hazards.current.label}</span>}
      </div>
      <div className="absolute right-2 top-2 flex gap-1">
        <Btn tone="default" title="Follow subject" active={follow === "subject"} onClick={() => setFollow(follow === "subject" ? "none" : "subject")}>
          <Crosshair size={13} /> Subject
        </Btn>
        <Btn tone="default" title="Follow responder" active={follow === "responder"} onClick={() => setFollow(follow === "responder" ? "none" : "responder")}>
          <Crosshair size={13} /> Responder
        </Btn>
        <Btn tone="default" title="Fit" onClick={() => { setFollow("none"); fit(); }}>
          <Maximize2 size={13} />
        </Btn>
        <Btn tone="default" title="Layers" active={showLayers} onClick={() => setShowLayers(!showLayers)}>
          <LayersIcon size={13} />
        </Btn>
      </div>
      {showLayers && (
        <div className="absolute right-2 top-11 z-20 flex w-52 flex-col gap-1.5 rounded-md border border-line-strong bg-surface p-2.5 shadow-xl">
          {(Object.keys(LAYER_LABELS) as (keyof Layers)[]).map((k) => (
            <Toggle key={k} checked={layers[k]} onChange={(v) => setLayer(k, v)} label={LAYER_LABELS[k]} />
          ))}
        </div>
      )}
      <Legend />
    </div>
  );

  function pick(st: WorldState, sx: number, sy: number, W: number, H: number): string | null {
    let best: string | null = null;
    let bd = 14;
    for (const t of st.tracks) {
      if (!t.ground_xy_m) continue;
      const [x, y] = toScreen(t.ground_xy_m[0], t.ground_xy_m[1], W, H);
      const d = Math.hypot(x - sx, y - sy);
      if (d < bd) {
        bd = d;
        best = t.track_id;
      }
    }
    return best;
  }
}

function txt(ctx: CanvasRenderingContext2D, s: string, x: number, y: number, color: string, size = 11, halo = false) {
  ctx.font = `600 ${size}px system-ui, sans-serif`;
  if (halo) {
    ctx.lineWidth = 3;
    ctx.strokeStyle = "rgba(13,13,13,0.75)";
    ctx.strokeText(s, x, y);
  }
  ctx.fillStyle = color;
  ctx.fillText(s, x, y);
}

function Legend() {
  const items: [string, string][] = [
    ["var(--s1)", "Responder"],
    ["var(--s2)", "Subject"],
    ["var(--ink-2)", "Other track"],
    ["var(--s3)", "Drone / footprint"],
    ["var(--s5)", "LiDAR (registered)"],
    ["var(--s7)", "Holdout marker"],
  ];
  return (
    <div className="pointer-events-none absolute bottom-2 right-2 grid grid-cols-2 gap-x-3 gap-y-0.5 rounded bg-[var(--overlay)] px-2 py-1 text-[10px] text-ink-2 backdrop-blur">
      {items.map(([c, l]) => (
        <span key={l} className="inline-flex items-center gap-1">
          <span className="inline-block h-2 w-2 rounded-full" style={{ background: c }} />
          {l}
        </span>
      ))}
    </div>
  );
}
