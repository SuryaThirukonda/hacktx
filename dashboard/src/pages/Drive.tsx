import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";

/** Phone controller for the chase target: a joystick and a speed. Nothing else on the screen. The drone on the
 *  demo page follows whoever it is tracking. Simulator only. */

interface SimPerson {
  id: string; role: string; xy: [number, number]; heading_deg: number; speed_mps: number; active: boolean; controlled: boolean;
  shirt: [number, number, number]; pants: [number, number, number];
}

const SPEEDS: [string, number][] = [["walk", 1.4], ["jog", 3.0], ["run", 4.5], ["sprint", 5.5]];

export function Drive() {
  const [people, setPeople] = useState<SimPerson[]>([]);
  const [sel, setSel] = useState<string | null>(null);
  const [speed, setSpeed] = useState(3.0);
  const [online, setOnline] = useState(false);
  const [push, setPush] = useState<{ dx: number; dy: number } | null>(null);
  const pad = useRef<HTMLDivElement>(null);
  const pushRef = useRef(push);
  pushRef.current = push;
  const selRef = useRef(sel);
  selRef.current = sel;
  const speedRef = useRef(speed);
  speedRef.current = speed;

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", "dark");
    document.title = "Eye in the Sky · drive the target";
    let stop = false;
    const load = () =>
      api.get<{ available: boolean; people: SimPerson[] }>("/api/simulation/people")
        .then((r) => {
          if (stop) return;
          setOnline(true);
          setPeople(r.available ? r.people : []);
          // the target: the scenario's subject, unless the driver picked someone else
          setSel((s) => (s && r.people.some((p) => p.id === s) ? s : r.people.find((p) => p.role === "subject")?.id ?? r.people[0]?.id ?? null));
        })
        .catch(() => !stop && setOnline(false));
    void load();
    const id = window.setInterval(load, 2000);
    return () => {
      stop = true;
      window.clearInterval(id);
    };
  }, []);

  // while the stick is pushed, renew the drive command every 250 ms (the server lets it lapse after 0.6 s)
  useEffect(() => {
    const id = window.setInterval(() => {
      const p = pushRef.current;
      const who = selRef.current;
      if (!p || !who) return;
      const mag = Math.min(1, Math.hypot(p.dx, p.dy));
      if (mag < 0.15) return;
      const heading = (Math.atan2(p.dx, -p.dy) * 180) / Math.PI; // screen up = north
      void api.post(`/api/simulation/people/${encodeURIComponent(who)}`, { action: "drive", heading_deg: heading, speed: speedRef.current * (0.4 + 0.6 * mag), hold_s: 0.6 }).catch(() => undefined);
    }, 250);
    return () => window.clearInterval(id);
  }, []);

  const stick = (e: React.PointerEvent) => {
    const r = pad.current!.getBoundingClientRect();
    setPush({ dx: (e.clientX - (r.left + r.width / 2)) / (r.width / 2), dy: (e.clientY - (r.top + r.height / 2)) / (r.height / 2) });
  };
  const release = () => {
    setPush(null);
    if (sel) void api.post(`/api/simulation/people/${encodeURIComponent(sel)}`, { action: "stop" }).catch(() => undefined);
  };
  const chosen = people.find((p) => p.id === sel) ?? null;
  const knob = push ? { x: Math.max(-1, Math.min(1, push.dx)) * 60, y: Math.max(-1, Math.min(1, push.dy)) * 60 } : { x: 0, y: 0 };

  return (
    <div className="flex h-[100dvh] flex-col overflow-hidden bg-page text-ink-1" style={{ touchAction: "none" }}>
      <header className="flex h-11 shrink-0 items-center gap-2 border-b border-line bg-surface px-3">
        <span className="text-[12px] font-extrabold tracking-[0.14em]">Eye in the Sky</span>
        <select
          aria-label="Who to drive"
          value={sel ?? ""}
          onChange={(e) => setSel(e.target.value || null)}
          className="h-7 rounded-md border border-line bg-surface-2 px-1.5 text-[12px] text-ink-1"
        >
          {people.filter((p) => p.active).map((p) => (
            <option key={p.id} value={p.id}>
              {p.id}
            </option>
          ))}
        </select>
        <span className={`ml-auto rounded px-1.5 py-0.5 text-[10px] font-bold ${online ? "bg-[var(--s7)] text-black" : "bg-[var(--critical)] text-white"}`}>
          {online ? "SIM" : "OFFLINE"}
        </span>
      </header>
      <main className="flex min-h-0 flex-1 flex-col items-center justify-center gap-6 px-4">
        <div
          ref={pad}
          onPointerDown={(e) => {
            (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
            stick(e);
          }}
          onPointerMove={(e) => push && stick(e)}
          onPointerUp={release}
          onPointerCancel={release}
          className="relative h-[min(72vw,320px)] w-[min(72vw,320px)] select-none rounded-full border-2 border-line-strong bg-surface-2"
          aria-label="Joystick"
        >
          <div
            className="pointer-events-none absolute left-1/2 top-1/2 h-[38%] w-[38%] rounded-full border border-line-strong shadow-lg"
            style={{ background: push ? "var(--s1)" : "var(--surface-3)", transform: `translate(calc(-50% + ${knob.x}px), calc(-50% + ${knob.y}px))` }}
          />
        </div>
        <div className="flex gap-2">
          {SPEEDS.map(([label, v]) => (
            <button
              key={label}
              type="button"
              onClick={() => setSpeed(v)}
              className={`rounded-md border px-4 py-2 text-[14px] font-semibold ${speed === v ? "border-[var(--s1)] bg-surface-3 text-ink-1" : "border-line text-ink-2"}`}
            >
              {label}
            </button>
          ))}
        </div>
        {chosen && chosen.speed_mps > 0.2 && <span className="tnum text-[11px] text-ink-3">{chosen.speed_mps.toFixed(1)} m/s</span>}
      </main>
    </div>
  );
}
