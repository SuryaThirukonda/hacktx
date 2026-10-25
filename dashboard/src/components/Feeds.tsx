import { Camera, Plane } from "lucide-react";
import { useRef, useState } from "react";
import { api } from "../lib/api";
import { ms } from "../lib/format";
import { useStore } from "../lib/store";
import type { FrameOverlay } from "../types/contracts";
import { drawDroneOverlay, drawPhoneOverlay, hitTestTracks } from "./overlays";
import { Badge, Panel } from "./ui";
import { VideoPane } from "./VideoPane";

const PROV: Record<string, string> = { LIVE: "var(--good)", RECORDED: "var(--warning)", SYNTHETIC: "var(--s7)" };

export function DroneFeed() {
  const src = useStore((s) => s.state?.sources.find((q) => q.source_id === "drone"));
  const layersZones = useStore((s) => s.layers.zones);
  const showToast = useStore((s) => s.showToast);
  const [ov, setOv] = useState<FrameOverlay | null>(null);
  const lastSet = useRef(0);
  const offline = !src || src.status === "OFFLINE" || src.status === "NOT_CONFIGURED";
  return (
    <Panel
      title="Drone · aerial"
      icon={<Plane size={14} />}
      right={
        <>
          {ov && <Badge color={PROV[ov.provenance]}>{ov.provenance}</Badge>}
          {ov && <span className="tnum text-[11px] text-ink-3">#{ov.frame_id} · {ov.detections} det · {ms(ov.detector_ms)}</span>}
          {src && <span className="tnum text-[11px] text-ink-3">{src.fps?.toFixed(0) ?? "—"} fps</span>}
        </>
      }
    >
      <VideoPane
        source="drone"
        offline={{ offline, label: src?.status === "STALE" ? "DRONE VIDEO STALE" : "DRONE VIDEO OFFLINE" }}
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
          drawDroneOverlay(ctx, o, g, hover, { selected: st?.selected_track_id ?? null, locks: st?.locks, showZones: layersZones, t: performance.now() });
        }}
        onImageClick={async (u, v, e) => {
          const action = e.shiftKey ? "lock_subject" : e.altKey ? "lock_responder" : "select";
          try {
            const r = await api.post<{ ok: boolean; track_id: string | null; reason?: string }>("/api/click", { u, v, action });
            if (!r.ok) showToast(r.reason ?? "no track there", "error");
            else if (action !== "select") showToast(`${action === "lock_subject" ? "Subject" : "Responder"} locked on ${r.track_id}`);
          } catch (err) {
            showToast(String((err as Error).message), "error");
          }
        }}
      >
        {ov && (
          <div className="pointer-events-none absolute bottom-1.5 right-2 flex gap-1">
            <Badge color={ov.calibration_valid ? "var(--good)" : "var(--critical)"} className="bg-[var(--overlay)]">
              {ov.calibration_valid ? `GROUND: ${String(ov.extra?.calibration_source ?? "").toUpperCase()}` : "NO GROUND TRANSFORM"}
            </Badge>
            {ov.cmc && (
              <Badge color={ov.cmc.ok ? "var(--ink-2)" : "var(--warning)"} className="bg-[var(--overlay)]">
                CMC {ov.cmc.ok ? `${ov.cmc.shift_px.toFixed(1)}px` : "lost"}
              </Badge>
            )}
            <Badge color="var(--ink-2)" className="bg-[var(--overlay)]">{ov.model_id}</Badge>
          </div>
        )}
      </VideoPane>
    </Panel>
  );
}

export function PhoneFeed() {
  const src = useStore((s) => s.state?.sources.find((q) => q.source_id === "phone"));
  const phone = useStore((s) => s.state?.phone);
  const reg = useStore((s) => s.state?.registration);
  const offline = !src || src.status === "OFFLINE" || src.status === "NOT_CONFIGURED";
  return (
    <Panel
      title="Responder · phone + LiDAR"
      icon={<Camera size={14} />}
      right={
        <>
          {phone?.provenance && <Badge color={PROV[phone.provenance]}>{phone.provenance}</Badge>}
          {phone && (
            <Badge color={phone.tracking_state === "normal" ? "var(--good)" : "var(--warning)"} title="ARKit tracking state">
              AR {phone.tracking_state ?? "—"}
            </Badge>
          )}
          {phone && phone.lidar_supported === false && <Badge color="var(--critical)">NO LIDAR</Badge>}
        </>
      }
    >
      <VideoPane
        source="phone"
        offline={{ offline, label: "RESPONDER PHONE OFFLINE" }}
        hitTest={hitTestTracks}
        draw={(ctx, o, g, hover) => drawPhoneOverlay(ctx, o, g, hover, { t: performance.now() })}
      >
        <div className="pointer-events-none absolute left-2 top-2 flex flex-col gap-1">
          {phone?.local_alert_active && (
            <Badge color="var(--critical)" solid className="pulse-critical">
              LOCAL LIDAR ALERT {phone.nearest_m !== null ? `${phone.nearest_m.toFixed(1)} m` : ""}
            </Badge>
          )}
          {phone && (
            <Badge color="var(--ink-2)" className="bg-[var(--overlay)]">
              depth valid {phone.depth_valid_fraction !== null ? `${Math.round(phone.depth_valid_fraction * 100)}%` : "—"}
            </Badge>
          )}
          {reg && (
            <Badge color={reg.valid ? "var(--good)" : "var(--warning)"} className="bg-[var(--overlay)]" title={reg.reason}>
              {reg.valid ? `REGISTERED ±${reg.residual_m?.toFixed(2) ?? "?"} m` : "NOT REGISTERED"}
            </Badge>
          )}
        </div>
      </VideoPane>
    </Panel>
  );
}
