// Overview cards for the three main views. They summarise status in words and numbers; the visual itself opens on
// its own page (/drone, /phone, /map) when the card is clicked.
import { Camera, ChevronRight, Map as MapIcon, Plane } from "lucide-react";
import type { ReactNode } from "react";
import { DASH, meters, mps, pct, secs } from "../lib/format";
import { navigate, VIEW_PATHS, type ViewKey } from "../lib/router";
import { STATUS_COLOR } from "../lib/severity";
import { useStore } from "../lib/store";
import { Badge, Dot } from "./ui";

const PROV: Record<string, string> = { LIVE: "var(--good)", RECORDED: "var(--warning)", SYNTHETIC: "var(--s7)" };

function Row({ k, v, tone }: { k: string; v: ReactNode; tone?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 text-[12px]">
      <span className="text-ink-3">{k}</span>
      <span className="tnum truncate text-right font-medium" style={{ color: tone ?? "var(--ink-1)" }}>{v}</span>
    </div>
  );
}

function Card({ view, title, icon, status, badges, children }: {
  view: ViewKey;
  title: string;
  icon: ReactNode;
  status: { label: string; color: string };
  badges?: ReactNode;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={() => navigate(VIEW_PATHS[view])}
      className="group flex h-full min-h-0 w-full flex-col rounded-lg border border-line bg-surface p-3 text-left transition-colors hover:border-[var(--border-strong)] hover:bg-surface-2 focus-visible:outline-2 focus-visible:outline-[var(--s1)]"
      aria-label={`Open the ${title} view`}
    >
      <div className="flex items-center gap-2">
        <span className="text-ink-2">{icon}</span>
        <span className="text-[13px] font-semibold text-ink-1">{title}</span>
        <span className="ml-auto inline-flex items-center gap-1.5 text-[11px] font-semibold" style={{ color: status.color }}>
          <Dot color={status.color} /> {status.label}
        </span>
      </div>
      {badges && <div className="mt-2 flex flex-wrap gap-1">{badges}</div>}
      <div className="mt-2 flex flex-1 flex-col gap-1">{children}</div>
      <div className="mt-2 flex items-center justify-end gap-1 text-[12px] font-semibold text-[var(--s1)]">
        Open view <ChevronRight size={14} className="transition-transform group-hover:translate-x-0.5" />
      </div>
    </button>
  );
}

function sourceStatus(src: { status: string } | undefined) {
  const s = src?.status ?? "NOT_CONFIGURED";
  return { label: s.replace("_", " "), color: STATUS_COLOR[s] ?? "var(--ink-3)" };
}

export function DroneCard() {
  const src = useStore((s) => s.state?.sources.find((q) => q.source_id === "drone"));
  const drone = useStore((s) => s.state?.drone);
  const calib = useStore((s) => s.state?.calibration);
  const auto = useStore((s) => s.state?.autonomy);
  const tracks = useStore((s) => s.state?.tracks);
  const subj = useStore((s) => s.state?.locks?.subject);
  const people = tracks?.filter((t) => t.state === "CONFIRMED" && t.class_name === "person").length ?? 0;
  return (
    <Card
      view="drone"
      title="Drone · aerial"
      icon={<Plane size={15} />}
      status={sourceStatus(src)}
      badges={
        <>
          {src?.provenance && <Badge color={PROV[src.provenance]}>{src.provenance}</Badge>}
          {calib && <Badge color={calib.valid ? "var(--good)" : "var(--critical)"}>{calib.valid ? `ground ${calib.source} ±${calib.error_bound_m} m` : "no ground transform"}</Badge>}
        </>
      }
    >
      <Row k="video" v={src?.fps != null ? `${src.fps.toFixed(0)} fps` : DASH} />
      <Row k="people tracked" v={people} />
      <Row k="subject lock" v={subj?.state ?? "UNLOCKED"} tone={subj?.state === "LOCKED" ? "var(--good)" : subj && subj.state !== "UNLOCKED" ? "var(--warning)" : undefined} />
      <Row k="autonomy" v={auto ? `${auto.mode}${auto.fsm !== "IDLE" ? ` · ${auto.fsm}` : ""}` : DASH} />
      <Row k="altitude · battery" v={`${meters(drone?.alt_m)} · ${pct(drone?.battery_pct != null ? drone.battery_pct / 100 : null)}`} />
    </Card>
  );
}

export function PhoneCard() {
  const src = useStore((s) => s.state?.sources.find((q) => q.source_id === "phone"));
  const phone = useStore((s) => s.state?.phone);
  const reg = useStore((s) => s.state?.registration);
  return (
    <Card
      view="phone"
      title="Responder · phone + LiDAR"
      icon={<Camera size={15} />}
      status={sourceStatus(src)}
      badges={
        <>
          {phone?.provenance && <Badge color={PROV[phone.provenance]}>{phone.provenance}</Badge>}
          {phone?.local_alert_active && <Badge color="var(--critical)" solid>LiDAR ALERT</Badge>}
          {reg && <Badge color={reg.valid ? "var(--good)" : "var(--warning)"}>{reg.valid ? "registered" : "not registered"}</Badge>}
        </>
      }
    >
      <Row k="ARKit tracking" v={phone?.tracking_state ?? DASH} tone={phone?.tracking_state && phone.tracking_state !== "normal" ? "var(--warning)" : undefined} />
      <Row
        k="nearest obstacle"
        v={phone?.nearest_m != null ? `${meters(phone.nearest_m)} ${phone.nearest_sector?.replace("_", " ") ?? ""}` : "clear / unknown"}
        tone={phone?.local_alert_active ? "var(--critical)" : undefined}
      />
      <Row k="depth valid" v={pct(phone?.depth_valid_fraction)} />
      <Row k="heading" v={phone?.heading_deg != null ? `${phone.heading_deg.toFixed(0)}° ±${phone.heading_accuracy_deg?.toFixed(0) ?? "?"}` : DASH} />
      <Row k="last update" v={secs(phone?.age_ms)} />
    </Card>
  );
}

export function MapCard() {
  const st = useStore((s) => s.state);
  const kin = st?.kinematics;
  const modeColor = st?.map_mode === "REGISTERED_METRIC" ? "var(--good)" : st?.map_mode === "UNCALIBRATED" ? "var(--critical)" : "var(--warning)";
  return (
    <Card
      view="map"
      title="Fused local scene"
      icon={<MapIcon size={15} />}
      status={{ label: (st?.map_mode ?? "NO STATE").replace("_", " "), color: st ? modeColor : "var(--ink-3)" }}
    >
      {kin?.valid ? (
        <>
          <Row k="subject" v={`${kin.clock ?? "?"} o'clock · ${meters(kin.range_m)}`} />
          <Row k="closing speed" v={mps(kin.closing_mps)} tone={(kin.closing_mps ?? 0) > 1 ? "var(--warning)" : undefined} />
          <Row k="time to contact" v={kin.ttc_s != null ? `${kin.ttc_s.toFixed(1)} s` : DASH} />
        </>
      ) : (
        <Row k="subject" v={kin?.reason ? kin.reason.slice(0, 48) : "no position claim"} tone="var(--ink-2)" />
      )}
      <Row k="tracks on map" v={st?.tracks.filter((t) => t.state === "CONFIRMED").length ?? 0} />
      <Row k="LiDAR points" v={st?.obstacles.length ?? 0} />
      <Row k="geofence margin" v={meters(st?.autonomy.safety?.geofence_margin_m)} />
    </Card>
  );
}
