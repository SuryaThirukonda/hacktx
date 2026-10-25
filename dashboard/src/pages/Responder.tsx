// Responder app (mobile-first PWA). Joins an incident, receives alerts in real time, announces them by voice
// through the phone's current audio output (so paired AirPods work), vibrates where the browser allows it,
// and lists confirmed targets with bearing and distance only when both positions are grounded.
import { Check, LocateFixed, MapPinOff, Navigation, Radio, Volume2, VolumeX, Wifi, WifiOff } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, wsUrl } from "../lib/api";
import { ReconnectingSocket, type SocketStatus } from "../lib/ws";
import type { Alert as IAlert, Incident, IncidentEvent } from "../types/incident";
import { PersonMark } from "../components/PersonMark";

interface Target {
  observation_id: string;
  label: string;
  category: string;
  provenance: string;
  review_status: string;
  last_observed_s: number;
  stale: boolean;
  side: string | null;
  grounded: boolean;
  range_m?: number;
  bearing_deg?: number;
  uncertainty_m?: number;
  cardinal?: string;
  clock?: number;
  text: string;
}
interface Brief {
  mission: string;
  responder: { grounded: boolean; how: string; accuracy_m: number | null; heading_deg: number | null; heading_trusted: boolean };
  targets: Target[];
}

const PROV: Record<string, string> = { live: "var(--good)", replay: "var(--warning)", simulated: "var(--s7)", manual: "var(--s4)" };
const VIB: Record<string, number[]> = { critical: [300, 120, 300, 120, 300], warning: [200, 100, 200], attention: [120, 80, 120], info: [80] };

function speak(text: string) {
  if (!("speechSynthesis" in window)) return;
  const u = new SpeechSynthesisUtterance(text);
  u.rate = 1.0;
  window.speechSynthesis.speak(u);
}

export function Responder() {
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [iid, setIid] = useState<string | null>(null);
  const [incident, setIncident] = useState<Incident | null>(null);
  const [alerts, setAlerts] = useState<Record<string, IAlert>>({});
  const [brief, setBrief] = useState<Brief | null>(null);
  const [conn, setConn] = useState<SocketStatus>("connecting");
  const [audio, setAudio] = useState(false);
  const [posMode, setPosMode] = useState<"off" | "gps" | "sim">("off");
  const [posNote, setPosNote] = useState<string>("Location is off.");
  const [flash, setFlash] = useState(false);
  const [mode, setMode] = useState<string>("");
  const audioRef = useRef(false);
  audioRef.current = audio;
  const joinedSeq = useRef<number>(Number.MAX_SAFE_INTEGER);
  const heading = useRef<{ deg: number | null; acc: number | null }>({ deg: null, acc: null });
  const canVibrate = typeof navigator !== "undefined" && "vibrate" in navigator;

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", "dark");
    document.title = "Overwatch · responder";
    if ("serviceWorker" in navigator && window.isSecureContext) navigator.serviceWorker.register("/sw.js").catch(() => undefined);
    api.get<{ current: string | null; items: Incident[] }>("/api/incidents").then((r) => {
      setIncidents(r.items);
      const want = new URLSearchParams(window.location.search).get("incident");
      setIid(want ?? r.current ?? null);
    }).catch(() => setConn("closed"));
    api.get<{ mode: string }>("/api/health").then((h) => setMode(h.mode)).catch(() => undefined);
  }, []);

  const notify = useCallback((a: IAlert) => {
    if (audioRef.current) speak(a.message);
    if (canVibrate) navigator.vibrate(VIB[a.severity] ?? VIB.info);
    else {
      setFlash(true);
      window.setTimeout(() => setFlash(false), 900);
    }
    api.post(`/api/alerts/${a.id}/delivered`).catch(() => undefined);
  }, [canVibrate]);

  // incident event stream: alerts and acknowledgments in real time
  useEffect(() => {
    if (!iid) return;
    api.get<Incident>(`/api/incidents/${iid}`).then(setIncident).catch(() => undefined);
    api.get<{ items: IAlert[] }>(`/api/incidents/${iid}/alerts`).then((r) => setAlerts(Object.fromEntries(r.items.map((a) => [a.id!, a])))).catch(() => undefined);
    const s = new ReconnectingSocket(wsUrl(`/ws/incidents/${iid}`), {
      onStatus: setConn,
      onMessage: (raw) => {
        const m = raw as { type: string; items?: IncidentEvent[] } & IncidentEvent;
        if (m.type === "backlog") {
          const items = m.items ?? [];
          joinedSeq.current = items.length ? items[items.length - 1].seq! : 0;
          return;
        }
        if (m.type !== "incident_event") return;
        const p = m.payload as Record<string, unknown>;
        if (m.kind === "alert") {
          const a = p as unknown as IAlert;
          setAlerts((cur) => ({ ...cur, [a.id!]: a }));
          if (m.seq! > joinedSeq.current) notify(a);
        } else if (m.kind === "alert_acknowledged") {
          const id = String(p.alert_id);
          setAlerts((cur) => (cur[id] ? { ...cur, [id]: { ...cur[id], acknowledged_at: m.at, acknowledged_by: String(p.by), status: "acknowledged" } } : cur));
        } else if (m.kind === "mission_changed") {
          setIncident((inc) => (inc ? { ...inc, mission_mode: p.to as Incident["mission_mode"] } : inc));
        }
      },
    });
    return () => s.close();
  }, [iid, notify]);

  // targets: recomputed by the server every 2 s (all numbers come from computed fields)
  useEffect(() => {
    if (!iid) return;
    const tick = () => api.get<Brief>(`/api/incidents/${iid}/responder-brief`).then(setBrief).catch(() => undefined);
    tick();
    const t = window.setInterval(tick, 2000);
    return () => window.clearInterval(t);
  }, [iid]);

  // position sharing
  useEffect(() => {
    if (!iid || posMode === "off") return;
    if (posMode === "sim") {
      const send = () => api.post(`/api/incidents/${iid}/responder-position`, { source: "simulated" }).then(() => setPosNote("Using the simulated responder position (SIMULATED).")).catch((e) => setPosNote(String((e as Error).message)));
      send();
      const t = window.setInterval(send, 2000);
      return () => window.clearInterval(t);
    }
    if (!("geolocation" in navigator) || !window.isSecureContext) {
      setPosNote("This browser cannot share location here (it needs HTTPS). Targets stay on the map; no bearing or distance.");
      setPosMode("off");
      return;
    }
    let last = 0;
    const id = navigator.geolocation.watchPosition(
      (p) => {
        const now = Date.now();
        if (now - last < 2000) return;
        last = now;
        setPosNote(`Location shared · ±${Math.round(p.coords.accuracy)} m`);
        api.post(`/api/incidents/${iid}/responder-position`, {
          lat: p.coords.latitude, lon: p.coords.longitude, accuracy_m: p.coords.accuracy,
          heading_deg: heading.current.deg, heading_accuracy_deg: heading.current.acc, source: "browser_geolocation",
        }).catch(() => undefined);
      },
      (err) => {
        setPosNote(`Location unavailable: ${err.message}`);
        setPosMode("off");
      },
      { enableHighAccuracy: true, maximumAge: 2000 },
    );
    return () => navigator.geolocation.clearWatch(id);
  }, [iid, posMode]);

  const enableAudio = () => {
    setAudio(true);
    speak("Audio on. Alerts will be announced.");
  };

  const startGps = async () => {
    // iOS asks for compass permission separately; heading is only trusted when the reported accuracy is good
    const DOE = (window as unknown as { DeviceOrientationEvent?: { requestPermission?: () => Promise<string> } }).DeviceOrientationEvent;
    try {
      if (DOE?.requestPermission) await DOE.requestPermission();
    } catch {
      /* compass optional */
    }
    window.addEventListener("deviceorientation", (e: DeviceOrientationEvent & { webkitCompassHeading?: number; webkitCompassAccuracy?: number }) => {
      if (typeof e.webkitCompassHeading === "number") heading.current = { deg: e.webkitCompassHeading, acc: e.webkitCompassAccuracy ?? null };
    });
    setPosMode("gps");
  };

  const ack = (a: IAlert) => api.post(`/api/alerts/${a.id}/acknowledge`, { by: "responder" }).catch(() => undefined);

  const list = useMemo(() => Object.values(alerts).sort((a, b) => (b.created_at ?? "").localeCompare(a.created_at ?? "")), [alerts]);
  const open = list.find((a) => !a.acknowledged_at) ?? null;

  if (!iid) {
    return (
      <div className="min-h-full bg-page p-4 text-ink-1">
        <Header conn={conn} incident={null} />
        <p className="mt-6 text-[15px] text-ink-2">No active incident yet. Ask the operator to start one, then refresh.</p>
        {incidents.length > 0 && (
          <ul className="mt-3 space-y-2">
            {incidents.map((i) => (
              <li key={i.id}>
                <button type="button" onClick={() => setIid(i.id!)} className="w-full rounded-lg border border-line bg-surface p-3 text-left">
                  <div className="font-semibold">{i.title}</div>
                  <div className="text-[12px] text-ink-3">{i.id}</div>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    );
  }

  return (
    <div className={`min-h-full bg-page pb-8 text-ink-1 ${flash ? "outline outline-8 outline-[var(--critical)]" : ""}`}>
      <div className="mx-auto max-w-[560px] px-3 pt-3">
        <Header conn={conn} incident={incident} />

        <PersonMark incidentId={iid} announce={(text) => { if (audioRef.current) speak(text); }} />

        <div className="mt-3 grid grid-cols-2 gap-2">
          <button type="button" onClick={audio ? () => setAudio(false) : enableAudio}
            className={`flex h-14 items-center justify-center gap-2 rounded-xl border text-[15px] font-bold ${audio ? "border-[var(--good)] text-ink-1" : "border-line bg-surface text-ink-2"}`}>
            {audio ? <Volume2 size={20} /> : <VolumeX size={20} />} {audio ? "Voice on" : "Enable voice"}
          </button>
          <button type="button" onClick={posMode === "off" ? startGps : () => setPosMode("off")}
            className={`flex h-14 items-center justify-center gap-2 rounded-xl border text-[15px] font-bold ${posMode !== "off" ? "border-[var(--good)] text-ink-1" : "border-line bg-surface text-ink-2"}`}>
            {posMode !== "off" ? <LocateFixed size={20} /> : <MapPinOff size={20} />} {posMode !== "off" ? "Location on" : "Share location"}
          </button>
        </div>
        <p className="mt-1.5 text-[12px] leading-snug text-ink-3">
          Voice plays through this phone's current audio output, including paired AirPods. {canVibrate ? "Vibration on alerts." : "This browser cannot vibrate; alerts flash the screen instead."}
        </p>
        <p className="text-[12px] leading-snug text-ink-3">{posNote}</p>
        {mode === "SIMULATED" && posMode !== "sim" && (
          <button type="button" onClick={() => setPosMode("sim")} className="mt-1 text-[12px] font-semibold text-[var(--s7)] underline">
            Use the simulated responder position (SIMULATED)
          </button>
        )}

        {open ? (
          <section className="mt-4 rounded-2xl border-2 p-4" style={{ borderColor: open.severity === "critical" ? "var(--critical)" : open.severity === "warning" ? "var(--serious)" : "var(--warning)" }} aria-live="assertive">
            <div className="flex items-center gap-2 text-[12px] font-bold uppercase tracking-wider text-ink-2">
              <Radio size={14} /> {open.severity} · {new Date(open.created_at!).toLocaleTimeString([], { hour12: false })}
              <span className="ml-auto rounded px-1.5 py-0.5 text-[11px]" style={{ color: PROV[open.provenance ?? "simulated"], boxShadow: "inset 0 0 0 1px currentColor" }}>{(open.provenance ?? "simulated").toUpperCase()}</span>
            </div>
            <p className="mt-2 text-[20px] font-semibold leading-snug">{open.message}</p>
            <button type="button" onClick={() => ack(open)} className="mt-4 flex h-16 w-full items-center justify-center gap-2 rounded-xl bg-[var(--s1)] text-[20px] font-extrabold text-white active:brightness-90">
              <Check size={26} /> ACKNOWLEDGE
            </button>
            {audio && (
              <button type="button" onClick={() => speak(open.message)} className="mt-2 w-full text-center text-[13px] text-ink-3 underline">Repeat</button>
            )}
          </section>
        ) : (
          <section className="mt-4 rounded-2xl border border-line bg-surface p-4 text-[15px] text-ink-2" aria-live="polite">No unacknowledged alerts.</section>
        )}

        <h2 className="mt-5 text-[12px] font-bold uppercase tracking-wider text-ink-2">Confirmed targets</h2>
        {!brief || brief.targets.length === 0 ? (
          <p className="mt-1 text-[14px] text-ink-3">Nothing confirmed yet. Unconfirmed detections are never sent here.</p>
        ) : (
          <ul className="mt-2 space-y-2">
            {brief.targets.map((t) => (
              <li key={t.observation_id} className="flex gap-3 rounded-xl border border-line bg-surface p-3">
                <Arrow t={t} trusted={brief.responder.heading_trusted} />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="text-[15px] font-semibold">{t.label}</span>
                    {t.side && <span className="rounded px-1.5 text-[11px] font-bold text-[var(--s1)] ring-1 ring-[var(--s1)]">Side {t.side}</span>}
                    <span className="rounded px-1.5 text-[11px] font-bold" style={{ color: PROV[t.provenance], boxShadow: "inset 0 0 0 1px currentColor" }}>{t.provenance.toUpperCase()}</span>
                    {t.stale && <span className="text-[11px] text-ink-3">stale</span>}
                  </div>
                  <p className="mt-1 text-[14px] leading-snug text-ink-1">{t.text}</p>
                  <p className="text-[12px] text-ink-3">
                    observed {t.last_observed_s} s ago{t.grounded ? ` · ±${t.uncertainty_m} m` : ""}
                  </p>
                </div>
              </li>
            ))}
          </ul>
        )}
        {brief && (
          <p className="mt-3 text-[12px] leading-snug text-ink-3">
            Your position: {brief.responder.grounded ? `placed on the incident map (${brief.responder.how})` : brief.responder.how}.
            {" "}Direction: {brief.responder.heading_trusted ? "clock face from your compass heading" : "compass points (your heading is not trusted)"}.
          </p>
        )}

        {list.length > 1 && (
          <>
            <h2 className="mt-5 text-[12px] font-bold uppercase tracking-wider text-ink-2">Earlier alerts</h2>
            <ul className="mt-1 space-y-1">
              {list.filter((a) => a !== open).slice(0, 12).map((a) => (
                <li key={a.id} className="rounded-lg bg-surface px-3 py-2 text-[13px] text-ink-2">
                  {a.message}
                  <span className="block text-[11px] text-ink-3">{a.acknowledged_at ? `acknowledged by ${a.acknowledged_by ?? "?"}` : "not acknowledged"}</span>
                </li>
              ))}
            </ul>
          </>
        )}
        <p className="mt-6 text-center text-[11px] text-ink-3">Research and training prototype. Not for operational use.</p>
      </div>
    </div>
  );
}

function Header({ conn, incident }: { conn: SocketStatus; incident: Incident | null }) {
  const ok = conn === "open";
  return (
    <header className="flex items-center gap-2">
      <Navigation size={18} className="text-[var(--s1)]" />
      <div className="min-w-0">
        <div className="truncate text-[15px] font-extrabold tracking-wide">{incident ? incident.title : "Overwatch responder"}</div>
        {incident && <div className="text-[12px] text-ink-3">mission: {incident.mission_mode} · {(incident.provenance ?? "").toUpperCase()}</div>}
      </div>
      <span className={`ml-auto inline-flex items-center gap-1 rounded-full px-2 py-1 text-[12px] font-semibold ${ok ? "text-[var(--good)]" : "text-[var(--warning)]"}`}>
        {ok ? <Wifi size={14} /> : <WifiOff size={14} />} {ok ? "connected" : conn === "connecting" ? "connecting…" : "reconnecting…"}
      </span>
    </header>
  );
}

function Arrow({ t, trusted }: { t: Target; trusted: boolean }) {
  if (!t.grounded || t.bearing_deg === undefined) {
    return <div className="flex h-14 w-14 shrink-0 items-center justify-center rounded-full bg-surface-2 text-center text-[10px] leading-tight text-ink-3">no bearing</div>;
  }
  const rot = trusted && t.clock !== undefined ? (t.clock % 12) * 30 : t.bearing_deg;
  return (
    <div className="flex h-14 w-14 shrink-0 flex-col items-center justify-center rounded-full bg-surface-2" title={trusted ? `${t.clock} o'clock` : `${t.cardinal} (north up)`}>
      <svg viewBox="-20 -20 40 40" className="h-9 w-9" aria-hidden="true">
        <g transform={`rotate(${rot})`}>
          <polygon points="0,-17 7,6 0,2 -7,6" fill="var(--s2)" />
        </g>
      </svg>
      <span className="text-[9px] font-bold text-ink-3">{trusted ? `${t.clock} o'clk` : "N up"}</span>
    </div>
  );
}
