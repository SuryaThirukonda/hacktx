import { useEffect, useRef, useState, type ReactNode } from "react";
import { wsUrl } from "../lib/api";
import { ReconnectingSocket } from "../lib/ws";
import type { FrameOverlay } from "../types/contracts";

export interface ImageGeom {
  scale: number;
  ox: number;
  oy: number;
  iw: number;
  ih: number;
}

export interface VideoPaneProps {
  source: "drone" | "phone";
  draw: (ctx: CanvasRenderingContext2D, ov: FrameOverlay, g: ImageGeom, hover: string | null) => void;
  onImageClick?: (u: number, v: number, e: React.MouseEvent) => void;
  hitTest?: (ov: FrameOverlay, u: number, v: number) => string | null;
  offline: { offline: boolean; label: string; quiet?: boolean }; // quiet: a simulated feed between frames is not "no signal"
  children?: ReactNode;
  onFrame?: (ov: FrameOverlay) => void;
}

/** Binary frames: [uint32 BE header length][FrameOverlay JSON][JPEG]. Overlays always belong to the drawn frame. */
export function VideoPane({ source, draw, onImageClick, hitTest, offline, children, onFrame }: VideoPaneProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const latest = useRef<{ bmp: ImageBitmap; ov: FrameOverlay } | null>(null);
  const geom = useRef<ImageGeom>({ scale: 1, ox: 0, oy: 0, iw: 1, ih: 1 });
  const hover = useRef<string | null>(null);
  const lastFrameAt = useRef<number>(0);
  const [stale, setStale] = useState(true);
  const [hoverLabel, setHoverLabel] = useState<{ x: number; y: number; text: string } | null>(null);
  const drawRef = useRef(draw);
  drawRef.current = draw;
  const onFrameRef = useRef(onFrame);
  onFrameRef.current = onFrame;

  const render = () => {
    const c = canvasRef.current;
    const L = latest.current;
    if (!c) return;
    const ctx = c.getContext("2d");
    if (!ctx) return;
    const dpr = window.devicePixelRatio || 1;
    const W = c.width / dpr;
    const H = c.height / dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = "#000";
    ctx.fillRect(0, 0, W, H);
    if (!L) return;
    const iw = L.bmp.width;
    const ih = L.bmp.height;
    const scale = Math.min(W / iw, H / ih);
    const ox = (W - iw * scale) / 2;
    const oy = (H - ih * scale) / 2;
    geom.current = { scale, ox, oy, iw, ih };
    ctx.drawImage(L.bmp, ox, oy, iw * scale, ih * scale);
    ctx.save();
    drawRef.current(ctx, L.ov, geom.current, hover.current);
    ctx.restore();
  };

  useEffect(() => {
    const sock = new ReconnectingSocket(wsUrl(`/ws/video/${source}`), {
      binary: true,
      onMessage: async (data) => {
        if (!(data instanceof ArrayBuffer)) return;
        const dv = new DataView(data);
        const n = dv.getUint32(0);
        const ov = JSON.parse(new TextDecoder().decode(new Uint8Array(data, 4, n))) as FrameOverlay;
        const blob = new Blob([new Uint8Array(data, 4 + n)], { type: "image/jpeg" });
        try {
          const bmp = await createImageBitmap(blob);
          latest.current?.bmp.close();
          latest.current = { bmp, ov };
          lastFrameAt.current = performance.now();
          onFrameRef.current?.(ov);
          requestAnimationFrame(render);
        } catch {
          /* corrupt frame */
        }
      },
    });
    const t = window.setInterval(() => {
      const s = performance.now() - lastFrameAt.current > 1500;
      setStale((prev) => (prev !== s ? s : prev));
    }, 300);
    return () => {
      sock.close();
      window.clearInterval(t);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source]);

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
      render();
    });
    ro.observe(wrap);
    return () => ro.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const toImage = (e: React.MouseEvent) => {
    const rect = canvasRef.current!.getBoundingClientRect();
    const g = geom.current;
    const u = (e.clientX - rect.left - g.ox) / g.scale;
    const v = (e.clientY - rect.top - g.oy) / g.scale;
    return { u, v, inside: u >= 0 && v >= 0 && u <= g.iw && v <= g.ih, x: e.clientX - rect.left, y: e.clientY - rect.top };
  };

  const isOffline = offline.offline || (stale && !offline.quiet);
  return (
    <div ref={wrapRef} className="absolute inset-0 overflow-hidden bg-black">
      <canvas
        ref={canvasRef}
        className={`block ${onImageClick ? "cursor-crosshair" : ""} ${isOffline ? "opacity-35 grayscale" : ""}`}
        onMouseMove={(e) => {
          if (!hitTest || !latest.current) return;
          const p = toImage(e);
          const id = p.inside ? hitTest(latest.current.ov, p.u, p.v) : null;
          if (id !== hover.current) {
            hover.current = id;
            requestAnimationFrame(render);
          }
          const tr = latest.current.ov.tracks.find((t) => t.track_id === id);
          setHoverLabel(tr ? { x: p.x, y: p.y, text: tr.label + (tr.ground_xy_m ? `  (${tr.ground_xy_m[0].toFixed(1)}, ${tr.ground_xy_m[1].toFixed(1)}) m` : "") } : null);
        }}
        onMouseLeave={() => {
          hover.current = null;
          setHoverLabel(null);
          requestAnimationFrame(render);
        }}
        onClick={(e) => {
          if (!onImageClick) return;
          const p = toImage(e);
          if (p.inside) onImageClick(p.u, p.v, e);
        }}
      />
      {hoverLabel && (
        <div
          className="pointer-events-none absolute z-10 rounded border border-line-strong bg-[var(--overlay)] px-2 py-1 text-[11px] text-ink-1 backdrop-blur"
          style={{ left: Math.min(hoverLabel.x + 12, (wrapRef.current?.clientWidth ?? 300) - 220), top: hoverLabel.y + 12 }}
        >
          {hoverLabel.text}
          <div className="text-ink-3">click: select · shift: lock subject · alt: mark responder</div>
        </div>
      )}
      {isOffline && (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-1 text-center">
          <div className="rounded-md bg-[var(--overlay)] px-3 py-2 backdrop-blur">
            <div className="text-[13px] font-bold tracking-wide text-critical">{offline.label}</div>
            <div className="text-[11px] text-ink-2">
              {latest.current ? "Last frame shown dimmed: not live, no claims made from it" : "Waiting for frames"}
            </div>
          </div>
        </div>
      )}
      {children}
    </div>
  );
}
