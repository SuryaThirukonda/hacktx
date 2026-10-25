import { Check, Volume2, VolumeX, Wifi, WifiOff } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { withToken, wsUrl } from "../lib/api";
import { DASH } from "../lib/format";
import { SEVERITY } from "../lib/severity";
import { ReconnectingSocket, type SocketStatus } from "../lib/ws";
import type { Alert, DepthSector, ResponderView } from "../types/contracts";

const VIBRATE: Record<string, number[]> = {
  CLOSING_FAST: [120, 80, 120, 80, 120],
  TARGET_LOST: [400, 150, 400],
  REACQUIRED: [60],
  PROXIMITY: [40, 40, 40, 40, 40, 40],
  DIRECTION_LEFT: [60, 40, 120, 40, 200],
  DIRECTION_RIGHT: [200, 40, 120, 40, 60],
};

function Compass({ v }: { v: ResponderView | null }) {
  const k = v?.kin;
  const b = k?.valid ? k.bearing_rel_deg : null;
  return (
    <svg viewBox="-110 -110 220 220" className="h-auto w-full max-w-[320px]" role="img" aria-label={b !== null ? `Subject at ${k?.clock} o'clock` : "Subject bearing unknown"}>
      <circle r="100" fill="var(--surface-2)" stroke="var(--axis)" strokeWidth="2" />
      <circle r="66" fill="none" stroke="var(--grid)" strokeWidth="1.5" />
      {Array.from({ length: 12 }).map((_, i) => {
        const a = (i * 30 * Math.PI) / 180;
        return (
          <g key={i}>
            <line x1={Math.sin(a) * 88} y1={-Math.cos(a) * 88} x2={Math.sin(a) * 100} y2={-Math.cos(a) * 100} stroke="var(--ink-3)" strokeWidth={i % 3 === 0 ? 3 : 1.5} />
            {i % 3 === 0 && (
              <text x={Math.sin(a) * 76} y={-Math.cos(a) * 76 + 5} textAnchor="middle" fontSize="13" fontWeight="700" fill="var(--ink-2)">{i === 0 ? 12 : i}</text>
            )}
          </g>
        );
      })}
      <polygon points="0,-16 11,12 0,6 -11,12" fill="var(--s1)" />
      <text y="34" textAnchor="middle" fontSize="10" fill="var(--ink-3)">you</text>
      {b !== null && b !== undefined && (
        <g transform={`rotate(${b})`}>
          <line y1="-20" y2="-90" stroke="var(--s2)" strokeWidth="7" strokeLinecap="round" />
          <polygon points="0,-104 12,-84 -12,-84" fill="var(--s2)" />
        </g>
      )}
    </svg>
  );
}

function Sectors({ sectors }: { sectors: DepthSector[] }) {
  const order = ["left", "front_left", "front", "front_right", "right"];
  return (
    <div className="grid grid-cols-5 gap-1">
      {order.map((n) => {
        const s = sectors.find((q) => q.sector === n);
        const unknown = !s || s.confidence === "unknown";
        const d = s?.nearest_m ?? null;
        const bg = unknown ? "var(--surface-3)" : d === null ? "color-mix(in srgb, var(--good) 35%, transparent)" : d < 1.2 ? "var(--critical)" : d < 2.5 ? "var(--warning)" : "color-mix(in srgb, var(--good) 55%, transparent)";
        return (
          <div key={n} className={`flex h-14 flex-col items-center justify-center rounded-md text-center ${unknown ? "hatch" : ""}`}
            style={{ background: bg, color: !unknown && d !== null && d >= 1.2 && d < 2.5 ? "#1a1a19" : "var(--ink-1)" }}>
            <div className="text-[10px] uppercase opacity-80">{n.replace("_", " ")}</div>
            <div className="tnum text-[16px] font-bold">{unknown ? "?" : d === null ? "clear" : `${d.toFixed(1)}m`}</div>
          </div>
        );
      })}
    </div>
  );
}

export function ResponderSensors() {
  const [view, setView] = useState<ResponderView | null>(null);
  const [alert, setAlert] = useState<Alert | null>(null);
  const [alertAt, setAlertAt] = useState(0);
  const [now, setNow] = useState(() => Date.now());
  const [conn, setConn] = useState<SocketStatus>("connecting");
  const [audio, setAudio] = useState(false);
  const sock = useRef<ReconnectingSocket | null>(null);
  const audioRef = useRef(audio);
  audioRef.current = audio;
  const ttsOk = useRef<boolean | null>(null);

  // Server-side ElevenLabs voice when configured; on-device speech otherwise (and on any failure).
  const say = async (text: string, critical: boolean) => {
    if (ttsOk.current !== false) {
      try {
        const r = await fetch(withToken(`/api/tts?text=${encodeURIComponent(text)}`));
        if (r.ok) {
          const audioEl = new Audio(URL.createObjectURL(await r.blob()));
          await audioEl.play();
          ttsOk.current = true;
          return;
        }
        ttsOk.current = false;
      } catch {
        ttsOk.current = false;
      }
    }
    try {
      if (critical) speechSynthesis.cancel();
      const u = new SpeechSynthesisUtterance(text);
      u.rate = 1.1;
      speechSynthesis.speak(u);
    } catch {
      /* no speech available */
    }
  };
  const sayRef = useRef(say);
  sayRef.current = say;

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", "dark");
    const s = new ReconnectingSocket(wsUrl("/ws/alerts"), {
      onStatus: setConn,
      onMessage: (raw) => {
        const m = raw as { type: string };
        if (m.type === "responder_view") setView(raw as ResponderView);
        else if (m.type === "alert") {
          const a = raw as Alert;
          if (!a.delivery.includes("phone_speech") && !a.delivery.includes("phone_haptic")) return;
          setAlert(a);
          setAlertAt(Date.now());
          if (a.haptic && "vibrate" in navigator) navigator.vibrate(VIBRATE[a.haptic] ?? [80]);
          if (audioRef.current && a.spoken_text) void sayRef.current(a.spoken_text, a.severity === "critical");
        } else if (m.type === "ack") {
          const id = (raw as { alert_id: string }).alert_id;
          setAlert((cur) => (cur && cur.alert_id === id ? { ...cur, acked: true } : cur));
        }
      },
    });
    sock.current = s;
    const tick = setInterval(() => setNow(Date.now()), 1000);
    return () => {
      clearInterval(tick);
      s.close();
    };
  }, []);

  const k = view?.kin;
  const sev = alert ? SEVERITY[alert.severity] : null;
  // The banner is the last spoken alert, not a live readout: show its age and dim it once past its TTL (alerts that
  // need an acknowledgement stay prominent until acknowledged). The gauge below is the live position.
  const ageS = alert ? Math.max(0, Math.floor((now - alertAt) / 1000)) : 0;
  const stale = !!alert && !(alert.ack_required && !alert.acked) && ageS > Math.max(1, (alert.expires_ts_ms - alert.ts_ms) / 1000);
  return (
    <div className="mx-auto flex min-h-full max-w-md flex-col gap-3 p-4">
      <header className="flex items-center gap-2">
        <div className="text-[15px] font-bold tracking-wide">Eye in the Sky · responder</div>
        <span className="ml-auto inline-flex items-center gap-1 text-[12px]" style={{ color: conn === "open" ? "var(--good)" : "var(--critical)" }}>
          {conn === "open" ? <Wifi size={14} /> : <WifiOff size={14} />} {conn === "open" ? "linked" : conn}
        </span>
        <button onClick={() => setAudio(!audio)} className="rounded-md border border-line-strong px-2 py-1 text-[12px]">
          {audio ? <Volume2 size={14} className="inline" /> : <VolumeX size={14} className="inline" />} {audio ? "voice on" : "tap for voice"}
        </button>
      </header>
      {alert && sev && (
        <div className={`rounded-xl p-4 ${alert.severity === "critical" && !alert.acked && !stale ? "pulse-critical" : ""}`} style={{ background: sev.color, color: sev.ink, opacity: alert.acked || stale ? 0.55 : 1 }}>
          <div className="flex items-baseline text-[12px] font-black tracking-widest">
            {sev.label}
            <span className="tnum ml-auto font-semibold tracking-normal opacity-80">{ageS < 1 ? "now" : `${ageS} s ago`}</span>
          </div>
          <div className="text-[22px] font-bold leading-7">{alert.spoken_text || alert.text}</div>
          {alert.ack_required && !alert.acked && (
            <button
              onClick={() => sock.current?.send({ type: "ack", alert_id: alert.alert_id })}
              className="mt-3 inline-flex h-12 w-full items-center justify-center gap-2 rounded-lg text-[18px] font-black"
              style={{ background: sev.ink, color: sev.color }}
            >
              <Check size={20} /> ACKNOWLEDGE
            </button>
          )}
          {alert.acked && <div className="mt-1 text-[12px] font-semibold">acknowledged</div>}
        </div>
      )}
      {!alert && <div className="rounded-xl bg-surface p-4 text-center text-[13px] text-ink-3">No alerts. Subject: {view?.subject_state ?? DASH}</div>}
      <div className="flex flex-col items-center rounded-xl bg-surface p-4">
        <Compass v={view} />
        <div className="mt-2 grid w-full grid-cols-3 text-center">
          <div>
            <div className="text-[11px] uppercase text-ink-3">range</div>
            <div className="tnum text-[26px] font-bold">{k?.valid && k.range_m !== null ? k.range_m.toFixed(0) : DASH}<span className="text-[13px] text-ink-3"> m</span></div>
          </div>
          <div>
            <div className="text-[11px] uppercase text-ink-3">closing</div>
            <div className="tnum text-[26px] font-bold" style={{ color: k?.valid && (k.closing_mps ?? 0) > 2.5 ? "var(--critical)" : undefined }}>
              {k?.valid && k.closing_mps !== null ? k.closing_mps.toFixed(1) : DASH}<span className="text-[13px] text-ink-3"> m/s</span>
            </div>
          </div>
          <div>
            <div className="text-[11px] uppercase text-ink-3">contact</div>
            <div className="tnum text-[26px] font-bold">{k?.valid && k.ttc_s !== null ? k.ttc_s.toFixed(1) : DASH}<span className="text-[13px] text-ink-3"> s</span></div>
          </div>
        </div>
        {!k?.valid && <div className="mt-1 text-center text-[12px] text-ink-3">{k?.reason ?? "waiting"}</div>}
        {view && !view.verified && view.subject_state === "LOCKED" && <div className="mt-1 text-[12px] text-warning">subject re-acquired, not yet verified</div>}
      </div>
      <div className="rounded-xl bg-surface p-3">
        <div className="mb-2 flex items-center text-[12px] text-ink-3">
          LiDAR around you {view?.local_alert && <span className="ml-auto font-bold text-critical">OBSTACLE</span>}
        </div>
        <Sectors sectors={view?.sectors ?? []} />
      </div>
      <footer className="pb-4 text-center text-[11px] text-ink-3">
        Web stand-in for the native responder app (ios/OverwatchResponder). Vibration works on Android browsers; iOS needs the native app for haptics and LiDAR.
        {view?.ai_paused && <div className="mt-1 text-warning">AI paused: rule-based alerts active</div>}
      </footer>
    </div>
  );
}

