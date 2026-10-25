import { Building2, Footprints, Home, Search } from "lucide-react";
import { useState } from "react";
import { actions, chooseScenario, type Mission, useIncident } from "../../lib/incident";
import { useStore } from "../../lib/store";
import { Badge, Btn, Dot } from "../ui";
import { clockTime, ProvBadge, Section } from "./common";

const MISSIONS: { key: Mission; label: string; icon: React.ReactNode; hint: string }[] = [
  { key: "search", label: "Search", icon: <Search size={14} />, hint: "Flag possible people; review before anything reaches a responder." },
  { key: "follow", label: "Follow", icon: <Footprints size={14} />, hint: "Track an operator-designated, consenting volunteer. Video analysis, not pursuit." },
  { key: "structure", label: "Structure", icon: <Building2 size={14} />, hint: "Openings by side (A front, then clockwise). Manual tags first." },
  { key: "indoor", label: "Indoor", icon: <Home size={14} />, hint: "A previously scanned scene with annotations. No indoor flight." },
];

const STATUS_COLOR: Record<string, string> = {
  connected: "var(--good)",
  disconnected: "var(--warning)",
  error: "var(--critical)",
  not_configured: "var(--ink-3)",
};

export function LeftRail() {
  const incident = useIncident((s) => s.incident);
  const usecases = useIncident((s) => s.usecases);
  const active = useIncident((s) => s.activeScenario);
  const sources = useIncident((s) => s.sources);
  const showToast = useStore((s) => s.showToast);
  const [pick, setPick] = useState<string>("");
  const [value, setValue] = useState("");
  const mission = (incident?.mission_mode ?? "search") as Mission;

  const run = async (f: () => Promise<unknown>, ok?: string) => {
    try {
      await f();
      if (ok) showToast(ok);
    } catch (e) {
      showToast(String((e as Error).message), "error");
    }
  };

  const loadScenario = (sid: string, missionFor: Mission, name: string) =>
    run(() => chooseScenario(sid, missionFor, name), `Scenario ${sid} loaded`);

  return (
    <div className="flex min-h-0 flex-col gap-2">
      <Section title="Mission">
        <div className="grid grid-cols-2 gap-1 p-2" role="radiogroup" aria-label="Mission mode">
          {MISSIONS.map((m) => (
            <button
              key={m.key}
              type="button"
              role="radio"
              aria-checked={mission === m.key}
              disabled={!incident}
              onClick={() => run(() => actions.setMission(m.key))}
              className={`flex items-center gap-1.5 rounded-md border px-2 py-1.5 text-[12px] font-semibold disabled:opacity-40 ${
                mission === m.key ? "border-[var(--s1)] bg-[color-mix(in_srgb,var(--s1)_18%,transparent)] text-ink-1" : "border-line text-ink-2 hover:bg-surface-2"
              }`}
            >
              {m.icon}
              {m.label}
            </button>
          ))}
        </div>
        <p className="px-2.5 pb-2 text-[11px] leading-snug text-ink-3">{MISSIONS.find((m) => m.key === mission)?.hint}</p>
      </Section>

      <Section title="World and scenario" className="min-h-[180px] flex-1">
        <ul className="divide-y divide-[var(--border)]">
          {usecases.map((u) => (
            <li key={u.id} className="px-2.5 py-2">
              <div className="flex items-center gap-1.5">
                <span className="text-[12.5px] font-semibold text-ink-1">{u.name}</span>
                <Badge color={u.status === "ready" ? "var(--good)" : u.status === "partial" ? "var(--warning)" : "var(--ink-3)"} className="ml-auto">
                  {u.status}
                </Badge>
              </div>
              <p className="mt-0.5 text-[11px] leading-snug text-ink-3">{u.summary}</p>
              {u.scenarios.length > 0 ? (
                <div className="mt-1 flex flex-wrap gap-1">
                  {u.scenarios.map((s) => (
                    <button
                      key={s.id}
                      type="button"
                      title={s.description}
                      onClick={() => loadScenario(s.id, u.mission, `${u.name} · ${s.id}`)}
                      className={`rounded border px-1.5 py-0.5 text-[11px] font-semibold ${
                        active === s.id ? "border-[var(--s1)] text-ink-1" : "border-line text-ink-2 hover:bg-surface-2"
                      }`}
                    >
                      {s.id}
                    </button>
                  ))}
                </div>
              ) : (
                <p className="mt-1 text-[11px] italic text-ink-3">World not built yet.</p>
              )}
              {u.not_built && <p className="mt-1 text-[10.5px] text-ink-3">Not built: {u.not_built}</p>}
            </li>
          ))}
        </ul>
      </Section>

      <Section title="Sensors and source">
        <ul className="px-2.5 py-1.5">
          {(sources?.items ?? []).map((s) => (
            <li key={s.id} className="flex items-center gap-2 py-[3px] text-[12px]" title={s.detail}>
              <Dot color={STATUS_COLOR[s.connection_status]} pulse={s.connection_status === "connected"} />
              <span className={s.id === sources?.active ? "font-semibold text-ink-1" : "text-ink-2"}>{s.name.length > 22 ? s.id : s.name}</span>
              <span className="ml-auto text-[10.5px] text-ink-3">{s.connection_status.replace("_", " ")}</span>
            </li>
          ))}
        </ul>
        <div className="flex flex-col gap-1 border-t border-line p-2">
          <div className="flex gap-1">
            <select
              aria-label="Video source"
              value={pick || sources?.active || ""}
              onChange={(e) => setPick(e.target.value)}
              className="h-7 min-w-0 flex-1 rounded-md border border-line bg-surface-2 px-1.5 text-[12px] text-ink-1"
            >
              {(sources?.items ?? []).map((s) => (
                <option key={s.id} value={s.id}>
                  {s.id}
                </option>
              ))}
            </select>
            <Btn disabled={!incident} onClick={() => run(() => actions.selectSource(pick || sources?.active || "simulated", value || undefined), "Source switched")}>
              Apply
            </Btn>
          </div>
          {["file", "webcam", "rtsp", "near_ir"].includes(pick) && (
            <input
              aria-label="Path, webcam index or URL"
              value={value}
              onChange={(e) => setValue(e.target.value)}
              placeholder={pick === "file" ? "/path/to/flight.mp4" : pick === "webcam" ? "0" : "rtsp://host/stream"}
              className="h-7 rounded-md border border-line bg-surface-2 px-2 text-[12px] text-ink-1"
            />
          )}
          <p className="text-[10.5px] leading-snug text-ink-3">
            Backend mode {sources?.mode ?? "–"}. Real video needs the LIVE backend; the dashboard never fakes a live link.
          </p>
        </div>
      </Section>

      <Section title="Incident">
        {incident ? (
          <dl className="grid grid-cols-[auto_1fr] gap-x-2 gap-y-[3px] px-2.5 py-2 text-[11.5px]">
            <dt className="text-ink-3">ID</dt>
            <dd className="truncate font-mono text-[11px] text-ink-1">{incident.id}</dd>
            <dt className="text-ink-3">Started</dt>
            <dd className="text-ink-1">{clockTime(incident.created_at)}</dd>
            <dt className="text-ink-3">Data</dt>
            <dd><ProvBadge p={incident.provenance} /></dd>
            <dt className="text-ink-3">Scenario</dt>
            <dd className="text-ink-1">{incident.scenario_id ?? "–"}</dd>
            <dt className="text-ink-3">Frame</dt>
            <dd className="truncate font-mono text-[11px] text-ink-2">{incident.coordinate_reference?.enu_frame}</dd>
          </dl>
        ) : (
          <p className="p-2.5 text-[12px] text-ink-3">No incident. Start the demo incident, or pick a scenario.</p>
        )}
      </Section>
    </div>
  );
}
