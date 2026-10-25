import { Box, Moon, Radio, Shield, Sun, Volume2, VolumeX } from "lucide-react";
import { useEffect, useState } from "react";
import { STATUS_COLOR } from "../lib/severity";
import { useStore } from "../lib/store";
import { Badge, Dot } from "./ui";

const PROV_COLOR: Record<string, string> = { LIVE: "var(--good)", RECORDED: "var(--warning)", SYNTHETIC: "var(--s7)" };

function useTheme(): [string, () => void] {
  const [theme, setTheme] = useState<string>(() => {
    try {
      return localStorage.getItem("overwatch.theme") ?? "auto";
    } catch {
      return "auto";
    }
  });
  useEffect(() => {
    const root = document.documentElement;
    if (theme === "auto") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", theme);
    try {
      localStorage.setItem("overwatch.theme", theme);
    } catch {
      /* ignore */
    }
  }, [theme]);
  const cycle = () => setTheme(theme === "auto" ? "light" : theme === "light" ? "dark" : "auto");
  return [theme, cycle];
}

export function TopBar() {
  const st = useStore((s) => s.state);
  const conn = useStore((s) => s.stateConn);
  const speak = useStore((s) => s.speak);
  const setSpeak = useStore((s) => s.setSpeak);
  const [theme, cycleTheme] = useTheme();
  const sources = st?.sources ?? [];
  const show = ["drone", "phone", "telemetry", "detector", "decisions"];
  return (
    <header className="flex h-12 shrink-0 items-center gap-3 border-b border-line bg-surface px-3">
      <div className="flex items-center gap-2">
        <svg width="22" height="22" viewBox="0 0 32 32" aria-hidden>
          <circle cx="16" cy="16" r="9" fill="none" stroke="var(--s1)" strokeWidth="2.5" />
          <circle cx="16" cy="16" r="3" fill="var(--s2)" />
          <path d="M16 3v5M16 24v5M3 16h5M24 16h5" stroke="var(--s1)" strokeWidth="2.5" strokeLinecap="round" />
        </svg>
        <div className="leading-tight">
          <div className="text-[14px] font-bold tracking-wide">OVERWATCH</div>
          <div className="hidden text-[10px] text-ink-3 2xl:block">responder aerial copilot</div>
        </div>
      </div>
      {st && (
        <Badge color={PROV_COLOR[st.provenance]} solid title="Data provenance of the sensor inputs">
          {st.mode === "SIMULATED" ? "SIMULATED" : st.mode} · {st.provenance}
        </Badge>
      )}
      {st?.sim && (
        <span className="tnum hidden truncate whitespace-nowrap text-[12px] text-ink-2 2xl:inline">
          {st.sim.scenario_name} · t={st.sim.t_s.toFixed(1)}s{st.sim.paused ? " · PAUSED" : ""}{st.sim.speed !== 1 ? ` · ${st.sim.speed}×` : ""}
        </span>
      )}
      <div className="mx-auto hidden min-w-0 items-center gap-1.5 overflow-hidden xl:flex">
        {sources
          .filter((s) => show.includes(s.source_id))
          .map((s) => (
            <span key={s.source_id} title={`${s.source_id}: ${s.status}${s.detail ? " · " + s.detail : ""}`} className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-md bg-surface-2 px-2 py-1 text-[11px]">
              <Dot color={STATUS_COLOR[s.status] ?? "var(--ink-3)"} pulse={s.status === "OFFLINE"} />
              <span className="font-semibold uppercase text-ink-2">{s.source_id}</span>
              <span className="tnum text-ink-3">
                {s.status === "OK" || s.status === "DEGRADED"
                  ? s.fps
                    ? `${s.fps.toFixed(0)} Hz`
                    : s.status
                  : s.status}
              </span>
            </span>
          ))}
        {st && (
          <span title={st.calibration.reason} className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-md bg-surface-2 px-2 py-1 text-[11px]">
            <Dot color={st.calibration.valid ? "var(--good)" : "var(--critical)"} />
            <span className="font-semibold uppercase text-ink-2">calib</span>
            <span className="tnum text-ink-3">{st.calibration.valid ? `${st.calibration.source} ±${st.calibration.error_bound_m}m` : "INVALID"}</span>
          </span>
        )}
      </div>
      <div className="ml-auto flex items-center gap-2">
        {st?.incident.active ? (
          <Badge color="var(--good)" title="Active incident: tracking enabled">
            <Shield size={11} /> {st.incident.incident_id}
          </Badge>
        ) : (
          <Badge color="var(--warning)">
            <Shield size={11} /> no incident
          </Badge>
        )}
        {st?.incident.recording && (
          <Badge color="var(--critical)" solid>
            <Radio size={11} /> REC
          </Badge>
        )}
        <button title={speak ? "Speaking alerts (click to mute)" : "Alerts muted (click to speak on this console)"} onClick={() => setSpeak(!speak)} className="rounded-md p-1.5 text-ink-2 hover:bg-surface-3">
          {speak ? <Volume2 size={16} /> : <VolumeX size={16} />}
        </button>
        <a href="/3d" title="3D decision view (simulator)" className="inline-flex items-center gap-1 rounded-md px-1.5 py-1 text-[11px] font-semibold text-ink-2 hover:bg-surface-3">
          <Box size={14} /> 3D
        </a>
        <button title={`Theme: ${theme}`} onClick={cycleTheme} className="rounded-md p-1.5 text-ink-2 hover:bg-surface-3">
          {theme === "light" ? <Sun size={16} /> : <Moon size={16} />}
        </button>
        <span title={`state socket ${conn}`} className="inline-flex items-center gap-1 text-[11px] text-ink-3">
          <Dot color={conn === "open" ? "var(--good)" : conn === "connecting" ? "var(--warning)" : "var(--critical)"} />
          {conn === "open" ? "linked" : conn}
        </span>
      </div>
    </header>
  );
}
