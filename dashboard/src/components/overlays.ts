import { cssVar, roleColor, withAlpha } from "../lib/colors";
import type { DepthSector, FrameOverlay, LockState } from "../types/contracts";
import type { ImageGeom } from "./VideoPane";

function label(ctx: CanvasRenderingContext2D, text: string, x: number, y: number, bg: string, fg = "#fff") {
  ctx.font = "600 11px system-ui, sans-serif";
  x = Math.max(2, Math.min(x, ctx.canvas.width / (window.devicePixelRatio || 1) - ctx.measureText(text).width - 10));
  const w = ctx.measureText(text).width + 8;
  ctx.fillStyle = bg;
  ctx.beginPath();
  ctx.roundRect(x, y - 15, w, 15, 3);
  ctx.fill();
  ctx.fillStyle = fg;
  ctx.fillText(text, x + 4, y - 4);
}

function dashedRect(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number) {
  ctx.setLineDash([5, 4]);
  ctx.strokeRect(x, y, w, h);
  ctx.setLineDash([]);
}

export function hitTestTracks(ov: FrameOverlay, u: number, v: number): string | null {
  let best: string | null = null;
  let bd = 1e9;
  for (const t of ov.tracks) {
    const [x0, y0, x1, y1] = t.box;
    const inside = u >= x0 - 6 && u <= x1 + 6 && v >= y0 - 6 && v <= y1 + 6;
    const d = inside ? 0 : Math.hypot(u - (x0 + x1) / 2, v - (y0 + y1) / 2);
    if (d < bd) {
      bd = d;
      best = t.track_id;
    }
  }
  return bd <= 18 ? best : null;
}

export function drawDroneOverlay(
  ctx: CanvasRenderingContext2D,
  ov: FrameOverlay,
  g: ImageGeom,
  hover: string | null,
  opts: { selected: string | null; locks: Record<string, LockState> | undefined; showZones: boolean; t: number },
) {
  const S = (x: number) => g.ox + x * g.scale;
  const T = (y: number) => g.oy + y * g.scale;
  // zones projected through the current calibration
  if (opts.showZones) {
    for (const z of ov.zones_px) {
      ctx.beginPath();
      z.polygon.forEach(([x, y], i) => (i ? ctx.lineTo(S(x), T(y)) : ctx.moveTo(S(x), T(y))));
      ctx.closePath();
      ctx.strokeStyle = "rgba(255,255,255,0.55)";
      ctx.lineWidth = 1.5;
      ctx.setLineDash([6, 5]);
      ctx.stroke();
      ctx.setLineDash([]);
      const [lx, ly] = z.polygon[0];
      ctx.font = "600 10px system-ui, sans-serif";
      ctx.fillStyle = "rgba(255,255,255,0.75)";
      ctx.fillText(z.name.toUpperCase(), S(lx) + 4, T(ly) - 4);
    }
  }
  // object memory: where a hidden person would be in this frame (dashed, fading with confidence)
  const mem = (ov.extra?.memory ?? []) as { object_id: string; state: string; since_seen_s: number; confidence: number; ghost_box?: number[] }[];
  for (const m of mem) {
    if (!m.ghost_box) continue;
    const [x0, y0, x1, y1] = m.ghost_box;
    ctx.setLineDash([4, 4]);
    ctx.strokeStyle = `rgba(240, 200, 90, ${Math.max(0.25, m.confidence).toFixed(2)})`;
    ctx.lineWidth = 1.5;
    ctx.strokeRect(S(x0), T(y0), (x1 - x0) * g.scale, (y1 - y0) * g.scale);
    ctx.setLineDash([]);
    ctx.font = "600 10px system-ui, sans-serif";
    ctx.fillStyle = "rgba(240, 200, 90, 0.9)";
    ctx.fillText(`${m.object_id} ${m.state.toLowerCase().replace("_", " ")} ${m.since_seen_s.toFixed(0)} s`, S(x0), T(y0) - 3);
  }
  // calibration markers (fit = drone/aqua, holdout = violet)
  for (const m of ov.markers) {
    if (!m.corners_px.length) continue;
    const col = m.holdout ? cssVar("--s7") : cssVar("--s3");
    ctx.beginPath();
    m.corners_px.forEach(([x, y], i) => (i ? ctx.lineTo(S(x), T(y)) : ctx.moveTo(S(x), T(y))));
    ctx.closePath();
    ctx.strokeStyle = col;
    ctx.lineWidth = 2;
    ctx.stroke();
    const compact = g.scale < 0.55;
    const err = m.error_m !== null && !compact ? ` ${(m.error_m * 100).toFixed(0)}cm` : "";
    label(ctx, `M${m.id}${m.holdout ? (compact ? "h" : " holdout") : ""}${err}`, S(m.center_px[0]) + 8, T(m.center_px[1]) - 6, withAlpha(col, 0.9));
  }
  // tracks
  const subj = opts.locks?.subject;
  for (const t of ov.tracks) {
    const [x0, y0, x1, y1] = t.box;
    const col = roleColor(t.role);
    const sel = t.track_id === opts.selected;
    const hov = t.track_id === hover;
    const lostish = t.state === "LOST" || t.state === "OCCLUDED";
    ctx.strokeStyle = col;
    ctx.lineWidth = t.role || sel ? 2.5 : 1.5;
    if (sel || hov) {
      ctx.save();
      ctx.strokeStyle = "rgba(255,255,255,0.9)";
      ctx.lineWidth = 4.5;
      ctx.strokeRect(S(x0) - 2, T(y0) - 2, (x1 - x0) * g.scale + 4, (y1 - y0) * g.scale + 4);
      ctx.restore();
      ctx.strokeStyle = col;
      ctx.lineWidth = 2.5;
    }
    if (lostish) dashedRect(ctx, S(x0), T(y0), (x1 - x0) * g.scale, (y1 - y0) * g.scale);
    else ctx.strokeRect(S(x0), T(y0), (x1 - x0) * g.scale, (y1 - y0) * g.scale);
    if (t.velocity_px && !lostish) {
      const cx = S((x0 + x1) / 2);
      const cy = T((y0 + y1) / 2);
      const ex = cx + t.velocity_px[0] * g.scale * 2;
      const ey = cy + t.velocity_px[1] * g.scale * 2;
      if (Math.hypot(ex - cx, ey - cy) > 6) {
        ctx.beginPath();
        ctx.moveTo(cx, cy);
        ctx.lineTo(ex, ey);
        ctx.lineWidth = 2;
        ctx.stroke();
        const a = Math.atan2(ey - cy, ex - cx);
        ctx.beginPath();
        ctx.moveTo(ex, ey);
        ctx.lineTo(ex - 7 * Math.cos(a - 0.45), ey - 7 * Math.sin(a - 0.45));
        ctx.lineTo(ex - 7 * Math.cos(a + 0.45), ey - 7 * Math.sin(a + 0.45));
        ctx.closePath();
        ctx.fillStyle = col;
        ctx.fill();
      }
    }
    if (t.role === "subject") {
      const r = Math.max(x1 - x0, y1 - y0) * g.scale * 0.85 + 10 + 3 * Math.sin(opts.t / 180);
      ctx.beginPath();
      ctx.arc(S((x0 + x1) / 2), T((y0 + y1) / 2), r, 0, Math.PI * 2);
      ctx.strokeStyle = withAlpha(col, 0.85);
      ctx.lineWidth = 2;
      ctx.stroke();
    }
    const tag = t.role === "subject" ? `SUBJECT ${subj?.state ?? ""}${subj && !subj.verified ? " (UNVERIFIED)" : ""}` : t.role === "responder" ? "RESPONDER" : "";
    const compact = g.scale < 0.55 && !sel && !hov;
    const idOnly = t.label.split(" ")[0];
    const text = compact ? (tag ? `${tag.split(" ")[0]} ${idOnly}` : idOnly) : `${tag ? tag + " · " : ""}${t.label}`;
    if (t.role || sel || hov || !lostish) label(ctx, text, S(x0), T(y0) - 3, withAlpha(t.role ? col : "#1a1a19", t.role ? 0.95 : 0.75));
  }
  // centre reticle
  const cx = S(g.iw / 2);
  const cy = T(g.ih / 2);
  ctx.strokeStyle = "rgba(255,255,255,0.35)";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(cx - 10, cy);
  ctx.lineTo(cx - 4, cy);
  ctx.moveTo(cx + 4, cy);
  ctx.lineTo(cx + 10, cy);
  ctx.moveTo(cx, cy - 10);
  ctx.lineTo(cx, cy - 4);
  ctx.moveTo(cx, cy + 4);
  ctx.lineTo(cx, cy + 10);
  ctx.stroke();
  // gimbal pitch gauge: quarter arc from horizon (right) to nadir (down)
  const pitch = ov.extra?.gimbal_pitch_deg as number | null | undefined;
  if (pitch !== null && pitch !== undefined) {
    const R = 24;
    const bx = g.ox + 12;
    const by = g.oy + g.ih * g.scale - R - 14;
    ctx.beginPath();
    ctx.arc(bx, by, R, 0, Math.PI / 2);
    ctx.strokeStyle = "rgba(255,255,255,0.4)";
    ctx.lineWidth = 1;
    ctx.stroke();
    const a = (Math.min(90, Math.max(0, -pitch)) * Math.PI) / 180;
    ctx.beginPath();
    ctx.moveTo(bx, by);
    ctx.lineTo(bx + R * Math.cos(a), by + R * Math.sin(a));
    ctx.strokeStyle = cssVar("--s3");
    ctx.lineWidth = 2;
    ctx.stroke();
    ctx.font = "600 10px system-ui, sans-serif";
    ctx.fillStyle = "rgba(255,255,255,0.85)";
    ctx.fillText(`gimbal ${pitch.toFixed(0)}°`, bx + 2, by - 5);
  }
}

export function drawPhoneOverlay(
  ctx: CanvasRenderingContext2D,
  ov: FrameOverlay,
  g: ImageGeom,
  hover: string | null,
  opts: { t: number },
) {
  const S = (x: number) => g.ox + x * g.scale;
  const T = (y: number) => g.oy + y * g.scale;
  for (const t of ov.tracks) {
    const [x0, y0, x1, y1] = t.box;
    const matched = !t.track_id.startsWith("phone-");
    const col = t.role ? roleColor(t.role) : matched ? cssVar("--ink-2") : "rgba(255,255,255,0.6)";
    ctx.strokeStyle = col;
    ctx.lineWidth = t.track_id === hover ? 3 : 2;
    if (!matched) ctx.setLineDash([4, 4]);
    ctx.strokeRect(S(x0), T(y0), (x1 - x0) * g.scale, (y1 - y0) * g.scale);
    ctx.setLineDash([]);
    const tag = t.role === "subject" ? "SUBJECT " : t.role === "responder" ? "RESPONDER " : "";
    label(ctx, tag + t.label, S(x0), T(y0) - 3, withAlpha(t.role ? col : "#1a1a19", 0.9));
  }
  const sectors = (ov.extra?.sectors as DepthSector[] | undefined) ?? [];
  const localAlert = Boolean(ov.extra?.local_alert);
  // sector strip along the bottom of the image
  const order = ["left", "front_left", "front", "front_right", "right"];
  const W = g.iw * g.scale;
  const sw = W / order.length;
  const y = g.oy + g.ih * g.scale - 34;
  order.forEach((name, i) => {
    const s = sectors.find((q) => q.sector === name);
    const x = g.ox + i * sw;
    let fill = "rgba(26,26,25,0.55)";
    let text = "unknown";
    if (s && s.confidence !== "unknown") {
      if (s.nearest_m === null) {
        fill = withAlpha(cssVar("--good"), 0.35);
        text = "clear";
      } else {
        const d = s.nearest_m;
        fill = d < 1.2 ? withAlpha(cssVar("--critical"), 0.75) : d < 2.5 ? withAlpha(cssVar("--warning"), 0.6) : withAlpha(cssVar("--good"), 0.45);
        text = `${d.toFixed(1)} m`;
      }
    }
    ctx.fillStyle = fill;
    ctx.fillRect(x + 1, y, sw - 2, 30);
    if (!s || s.confidence === "unknown") {
      ctx.save();
      ctx.beginPath();
      ctx.rect(x + 1, y, sw - 2, 30);
      ctx.clip();
      ctx.strokeStyle = "rgba(255,255,255,0.18)";
      ctx.lineWidth = 1;
      for (let k = -22; k < sw; k += 7) {
        ctx.beginPath();
        ctx.moveTo(x + k, y + 22);
        ctx.lineTo(x + k + 22, y);
        ctx.stroke();
      }
      ctx.restore();
    }
    ctx.textAlign = "center";
    ctx.font = "500 9px system-ui, sans-serif";
    ctx.fillStyle = "rgba(255,255,255,0.8)";
    ctx.fillText(name.replace("_", " ").toUpperCase(), x + sw / 2, y + 11);
    ctx.font = "700 12px system-ui, sans-serif";
    ctx.fillStyle = "#fff";
    ctx.fillText(text, x + sw / 2, y + 25);
    ctx.textAlign = "left";
  });
  if (localAlert) {
    const pulse = 0.55 + 0.45 * Math.abs(Math.sin(opts.t / 160));
    ctx.strokeStyle = withAlpha(cssVar("--critical"), pulse);
    ctx.lineWidth = 6;
    ctx.strokeRect(g.ox + 3, g.oy + 3, g.iw * g.scale - 6, g.ih * g.scale - 6);
  }
}
