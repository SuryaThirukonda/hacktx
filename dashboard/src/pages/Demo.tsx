import { Box, LayoutDashboard, Pause, Play, Rocket, RotateCcw, Shield, Video } from "lucide-react";
import { lazy, Suspense, useEffect, useRef, useState } from "react";
import QRCode from "qrcode";
import { api } from "../lib/api";
import { FusedMap, type MapPin } from "../components/FusedMap";
import { PursuitCard } from "../components/PursuitCard";
import { VoicePanel } from "../components/VoicePanel";
import { Section } from "../components/mission/common";
import { drawDroneOverlay } from "../components/overlays";
import { Badge, Btn, Dot } from "../components/ui";
import { VideoPane } from "../components/VideoPane";
import { actions, chooseScenario, loadHome, type UseCase, useIncident } from "../lib/incident";
import { useStore } from "../lib/store";
import type { AutonomyState, DecisionRecord, DispatchStatus, LockState } from "../types/contracts";
import { Disconnected, Toast } from "./Console";

const Scene3DPanel = lazy(() => import("../components/mission/Scene3DPanel").then((m) => ({ default: m.Scene3DPanel })));

const START_HERE = "PP0"; // a person runs, the drone follows

/** The demo page: every simulation one click away, the drone's view, and what the system is doing in plain words.
 *  Read-only apart from choosing, pausing and restarting a scenario; the full dashboard is at /dashboard. */
export function Demo() {
  const usecases = useIncident((s) => s.usecases);
  const conn = useStore((s) => s.stateConn);
  const sim = useStore((s) => s.state?.sim ?? null);
  const showToast = useStore((s) => s.showToast);
  const [tab, setTab] = useState<"video" | "3d">("video");
  const [busy, setBusy] = useState<string | null>(null);
  const src = useStore((s) => s.state?.sources.find((q) => q.source_id === "drone"));
  const offline = !src || src.status === "OFFLINE" || src.status === "NOT_CONFIGURED";

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", localStorage.getItem("overwatch.theme") === "light" ? "light" : "dark");
    document.title = "Overwatch · simulation demo";
    loadHome().catch((e) => showToast(String((e as Error).message), "error"));
    // the world's launchpad and the scenario's dispatch request, refreshed as scenarios change
    let stop = false;
    const meta = () =>
      api.get<NonNullable<ReturnType<typeof useStore.getState>["demoWorld"]>>("/api/world/meta")
        .then((m) => !stop && useStore.setState({ demoWorld: m }))
        .catch(() => undefined);
    void meta();
    const id = window.setInterval(meta, 2000);
    return () => {
      stop = true;
      window.clearInterval(id);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const active = sim?.scenario ?? null;
  const load = async (u: UseCase, sid: string) => {
    setBusy(sid);
    try {
      await chooseScenario(sid, u.mission, `${u.name} · ${sid}`);
      showToast(`Loaded ${sid}`);
    } catch (e) {
      showToast(String((e as Error).message), "error");
    } finally {
      setBusy(null);
    }
  };
  const pursuit = usecases.find((u) => u.scenarios.some((s) => s.id === START_HERE));
  const startHere = pursuit?.scenarios.find((s) => s.id === START_HERE);

  return (
    <div className="flex h-full min-h-0 flex-col bg-page text-ink-1">
      <header className="flex h-11 shrink-0 items-center gap-2 border-b border-line bg-surface px-3">
        <Shield size={16} className="text-[var(--s1)]" />
        <span className="text-[13px] font-extrabold tracking-[0.14em]">OVERWATCH</span>
        <span className="hidden text-[12px] text-ink-3 sm:inline">simulation demo</span>
        <Badge color="var(--s7)" solid className="ml-1">SIMULATED</Badge>
        <div className="ml-auto flex items-center gap-1.5">
          <span className="hidden items-center gap-1 text-[11px] text-ink-3 sm:flex" title="live state stream">
            <Dot color={conn === "open" ? "var(--good)" : "var(--warning)"} /> {conn}
          </span>
          <a href="/3d" className="inline-flex h-7 items-center gap-1 rounded-md px-2 text-[12px] text-ink-2 hover:bg-surface-3" title="Full-screen 3D view">
            <Box size={13} /> <span className="hidden md:inline">3D</span>
          </a>
          <a href="/dashboard" className="inline-flex h-7 items-center gap-1 whitespace-nowrap rounded-md border border-line px-2 text-[12px] font-semibold text-ink-1 hover:bg-surface-3" title="Every control: review queue, structure, calibration, decisions, replay">
            <LayoutDashboard size={13} /> Full dashboard
          </a>
        </div>
      </header>
      <Disconnected />
      <main className="grid min-h-0 flex-1 auto-rows-max grid-cols-1 gap-2 overflow-y-auto p-2 xl:auto-rows-auto xl:grid-cols-[300px_minmax(0,1fr)_360px] xl:overflow-hidden">
        {/* simulations */}
        <div className="order-3 flex flex-col gap-2 xl:order-1 xl:min-h-0 xl:overflow-y-auto">
          {pursuit && startHere && (
            <button
              type="button"
              onClick={() => void load(pursuit, START_HERE)}
              disabled={busy !== null}
              className={`rounded-lg border p-3 text-left transition-colors ${active === START_HERE ? "border-[var(--s1)] bg-surface-2" : "border-line bg-surface hover:bg-surface-2"}`}
            >
              <div className="text-[10.5px] font-bold uppercase tracking-[0.12em] text-[var(--s1)]">Start here</div>
              <div className="mt-1 text-[13.5px] font-semibold">A person runs, the drone follows</div>
              <p className="mt-0.5 text-[11.5px] leading-snug text-ink-3">{startHere.description}</p>
            </button>
          )}
          <Section title="All simulations" className="xl:min-h-0">
            <ul className="divide-y divide-[var(--border)]">
              {usecases.map((u) => (
                <li key={u.id} className="px-2.5 py-2">
                  <div className="text-[12.5px] font-semibold">{u.name}</div>
                  <p className="mt-0.5 text-[11px] leading-snug text-ink-3">{u.summary}</p>
                  <ul className="mt-1.5 flex flex-col gap-0.5">
                    {u.scenarios.map((s) => (
                      <li key={s.id}>
                        <button
                          type="button"
                          title={s.description}
                          disabled={busy !== null}
                          onClick={() => void load(u, s.id)}
                          className={`flex w-full items-baseline gap-2 rounded px-1.5 py-1 text-left text-[12px] ${active === s.id ? "bg-surface-3 text-ink-1" : "text-ink-2 hover:bg-surface-2"}`}
                        >
                          <span className="w-12 shrink-0 font-mono text-[11px] font-semibold">{s.id}</span>
                          <span className="min-w-0 flex-1 leading-snug">{shortName(s.name)}</span>
                          {busy === s.id && <span className="text-[10.5px] text-ink-3">loading…</span>}
                        </button>
                      </li>
                    ))}
                  </ul>
                </li>
              ))}
            </ul>
          </Section>
        </div>

        {/* the drone's view */}
        <section className="order-1 flex h-[62vw] max-h-[640px] min-h-[300px] flex-col rounded-lg border border-line bg-surface xl:order-2 xl:h-auto xl:max-h-none xl:min-h-0">
          <header className="flex h-9 shrink-0 items-center gap-1 border-b border-line px-2">
            <Tab active={tab === "video"} onClick={() => setTab("video")} icon={<Video size={13} />} label="Drone camera" />
            <Tab active={tab === "3d"} onClick={() => setTab("3d")} icon={<Box size={13} />} label="3D world" />
            <Playback />
          </header>
          <div className="relative min-h-0 flex-1">
            <StoryStrip />
            {tab === "video" ? (
              <VideoPane
                source="drone"
                offline={{ offline, label: src?.status === "STALE" ? "DRONE VIDEO STALE" : "NO DRONE VIDEO", quiet: src?.provenance === "SYNTHETIC" }}
                draw={(ctx, o, g, hover) => {
                  const st = useStore.getState().state;
                  drawDroneOverlay(ctx, o, g, hover, { selected: null, locks: st?.locks, showZones: false, t: performance.now() });
                }}
              />
            ) : (
              <Suspense fallback={<div className="p-4 text-[12px] text-ink-3">Loading 3D view…</div>}>
                <Scene3DPanel />
              </Suspense>
            )}
          </div>
        </section>

        {/* what is happening (below xl its parts interleave with the list: status, simulations, map, events) */}
        <div className="flex flex-col gap-2 max-xl:contents xl:order-3 xl:min-h-0 xl:overflow-y-auto">
          <div className="order-2 flex shrink-0 flex-col gap-2">
            <NowPlaying />
            <PursuitCard />
            <VoicePanel />
            <DispatchPanel />
            <PeoplePanel />
            <PhonePanel />
            <TigerPanel />
          </div>
          <DemoMap />
          <RecentEvents />
        </div>
      </main>
      <Toast />
    </div>
  );
}

/** The scenario's story (its manifest): the problem in one line, then each timed beat's caption as the simulation
 *  reaches it, and the success line at the end. The beat's entity is highlighted on the map and in 3D, and the 3D
 *  view takes the beat's camera preset. Captions say what the scenario is built to show; the panels show what
 *  actually happens. */
function StoryStrip() {
  const m = useStore((s) => s.demoWorld?.manifest ?? null);
  const scen = useStore((s) => s.demoWorld?.scenario ?? null);
  const sim = useStore((s) => s.state?.sim ?? null);
  const t = sim?.t_s ?? 0;
  const active = m && sim && scen === sim.scenario ? m : null;
  const i = active ? active.beats.reduce((acc, b, k) => (b.t <= t ? k : acc), -1) : -1;
  const beat = active && i >= 0 ? active.beats[i] : null;
  useEffect(() => {
    useStore.setState({ storyFocus: beat ? { entity: beat.highlight_entity ?? null, camera: beat.camera_preset ?? null, beat: i } : null });
  }, [active?.scenario, i, beat]);
  useEffect(() => () => useStore.setState({ storyFocus: null }), []);
  if (!active || active.default) return null;
  const last = active.beats[active.beats.length - 1];
  const done = !!last && i === active.beats.length - 1 && (t >= last.t + 7 || (sim?.duration_s != null && t >= sim.duration_s - 3));
  return (
    <div className="pointer-events-none absolute inset-x-2 bottom-2 z-10 flex justify-center">
      <div className="pointer-events-auto max-w-[760px] rounded-lg bg-[var(--overlay)] px-3 py-2 shadow-lg backdrop-blur">
        <div className="flex items-center gap-2 text-[10.5px] font-bold uppercase tracking-[0.12em] text-[var(--s1)]">
          <span>{active.title}</span>
          <span className="ml-auto flex gap-1" aria-label={`beat ${i + 1} of ${active.beats.length}`}>
            {active.beats.map((b, k) => (
              <span key={k} title={`${b.t} s: ${b.caption}`} className={`h-1.5 w-1.5 rounded-full ${k <= i ? "bg-[var(--s1)]" : "bg-[var(--border-strong)]"}`} />
            ))}
          </span>
        </div>
        <p className="mt-0.5 text-[13px] leading-snug text-ink-1">
          {done && active.success_line ? <><span className="font-semibold text-[var(--good)]">Outcome to check: </span>{active.success_line}</> : beat ? beat.caption : active.problem_line}
        </p>
      </div>
    </div>
  );
}

/** "Pursuit (autonomous): a person runs, …" → "a person runs, …" when the world is already the heading. */
function shortName(name: string) {
  const i = name.indexOf(": ");
  return i > 0 && i < 30 ? name.slice(i + 2) : name;
}

function Tab({ active, onClick, icon, label }: { active: boolean; onClick: () => void; icon: React.ReactNode; label: string }) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      onClick={onClick}
      className={`inline-flex h-7 items-center gap-1.5 rounded-md px-2 text-[12px] font-semibold ${active ? "bg-surface-3 text-ink-1" : "text-ink-2 hover:bg-surface-2"}`}
    >
      {icon}
      {label}
    </button>
  );
}

function Playback() {
  const sim = useStore((s) => s.state?.sim ?? null);
  const incident = useIncident((s) => s.incident);
  const showToast = useStore((s) => s.showToast);
  const run = (f: () => Promise<unknown>) => f().catch((e) => showToast(String((e as Error).message), "error"));
  const frac = sim?.duration_s ? Math.min(1, sim.t_s / sim.duration_s) : 0;
  return (
    <div className="ml-auto flex items-center gap-2">
      {sim && (
        <span className="tnum hidden items-center gap-1.5 text-[11px] text-ink-3 sm:flex">
          <span className="h-1 w-20 overflow-hidden rounded-full bg-surface-3">
            <span className="block h-full rounded-full bg-[var(--s1)]" style={{ width: `${frac * 100}%` }} />
          </span>
          {sim.t_s.toFixed(0)} / {sim.duration_s?.toFixed(0) ?? "–"} s
        </span>
      )}
      <Btn title={sim?.paused ? "Play" : "Pause"} disabled={!incident} onClick={() => run(() => actions.replay(sim?.paused ? "start" : "pause"))}>
        {sim?.paused ? <Play size={13} /> : <Pause size={13} />}
      </Btn>
      <Btn title="Restart this scenario" disabled={!incident || !sim} onClick={() => run(() => actions.replay("restart", sim?.scenario))}>
        <RotateCcw size={13} />
      </Btn>
    </div>
  );
}

const FSM_TEXT: Record<AutonomyState["fsm"], string> = {
  TAKEOFF: "Taking off from the launchpad",
  TRANSIT: "Flying to the search area",
  SEARCH: "Flying the search legs, matching people against the description",
  COVERED: "Search area covered, nobody matched: orbiting, the operator decides",
  IDLE: "Waiting for a designated person",
  TRACKING: "Following the designated person",
  VERIFY: "Holding: the camera re-bound to someone nobody has confirmed. The drone does not move towards them until a person confirms",
  COAST: "Person briefly hidden: continuing on their last motion",
  REACQUIRE: "Person lost: searching",
  HOLD: "Holding position",
};

const LOCK_TEXT: Record<LockState["state"], string> = {
  UNLOCKED: "not designated",
  LOCKED: "in view",
  COAST: "briefly hidden",
  REACQUIRE: "lost, searching",
  UNCERTAIN: "uncertain",
  HOLD: "held",
};

function NowPlaying() {
  const sim = useStore((s) => s.state?.sim ?? null);
  const auto = useStore((s) => s.state?.autonomy ?? null);
  const subject = useStore((s) => s.state?.locks?.subject ?? null);
  const decisions = useStore((s) => s.decisions);
  const usecases = useIncident((s) => s.usecases);
  const scenario = usecases.flatMap((u) => u.scenarios).find((s) => s.id === sim?.scenario);
  const last: DecisionRecord | undefined = [...decisions].reverse().find((d) => !d.shadow);

  const dispatch = useStore((s) => s.state?.dispatch ?? null);
  let drone = "Flown by the scripted pilot (manual)";
  if (auto && (auto.mode === "FOLLOW" || auto.mode === "DISPATCH")) {
    drone = FSM_TEXT[auto.fsm] ?? auto.fsm;
    if (auto.fsm === "REACQUIRE" && auto.route_label) drone += `: ${auto.route_label}`;
    if (auto.mode === "DISPATCH" && auto.fsm === "SEARCH" && dispatch)
      drone = dispatch.description ? `Search leg ${Math.max(1, dispatch.leg)} of ${dispatch.legs}, looking for "${dispatch.description}"` : `Sweep leg ${Math.max(1, dispatch.leg)} of ${dispatch.legs}`;
  } else if (auto && auto.mode === "GOTO") {
    drone = auto.fsm === "HOLD" ? `At ${auto.route_label ?? "the place"}, looking at it` : `Flying to ${auto.route_label ?? "the place"}`;
  } else if (auto && auto.mode !== "MANUAL") drone = `Mode ${auto.mode.toLowerCase()}`;

  return (
    <Section title="Now playing">
      <div className="flex flex-col gap-2 px-2.5 py-2">
        {sim ? (
          <>
            <div>
              <div className="flex items-baseline gap-2">
                <span className="font-mono text-[11px] font-semibold text-ink-3">{sim.scenario}</span>
                <span className="text-[13px] font-semibold leading-snug">{scenario ? shortName(scenario.name) : sim.scenario_name}</span>
              </div>
              {scenario && <p className="mt-0.5 text-[11.5px] leading-snug text-ink-3">{scenario.description}</p>}
            </div>
            <dl className="grid grid-cols-[84px_minmax(0,1fr)] gap-x-2 gap-y-1 text-[12px]">
              <dt className="text-ink-3">Aircraft</dt>
              <dd className="text-ink-1">
                {sim.stack === "jev-x2" ? "Skydio X2 (MuJoCo), jev-drone stack: range-sector reflex, D6 maneuvers" : (sim.drone_model ?? "kinematic Mini 2 stand-in")}
              </dd>
              <dt className="text-ink-3">Drone</dt>
              <dd className={auto?.fsm === "VERIFY" ? "text-[var(--warning)]" : "text-ink-1"}>
                {drone}
                {auto?.reflex && <span className="text-[var(--warning)]"> · reflex: {auto.reflex}</span>}
                {!auto?.reflex && auto?.maneuver && <span className="text-ink-3"> · maneuver: {auto.maneuver.replaceAll("_", " ")}</span>}
                {auto?.scene_ahead_m != null && <span className="text-ink-3"> · {auto.scene_ahead_m.toFixed(1)} m free ahead</span>}
              </dd>
              {subject && subject.state !== "UNLOCKED" && (
                <>
                  <dt className="text-ink-3">Person</dt>
                  <dd className="text-ink-1">{LOCK_TEXT[subject.state]}{subject.verified === false ? " · re-binding not yet confirmed" : ""}</dd>
                </>
              )}
              {dispatch?.last_report && (
                <>
                  <dt className="text-ink-3">Position</dt>
                  <dd className="text-ink-1">
                    {dispatch.last_report.distance_m.toFixed(0)} m from the launchpad, bearing {String(dispatch.last_report.bearing_deg).padStart(3, "0")}° ({dispatch.last_report.compass})
                    {dispatch.last_report.heading_deg != null ? `, moving at ${dispatch.last_report.speed_mps.toFixed(1)} m/s` : ", stationary"}
                    {dispatch.last_report.lat != null && <span className="text-ink-3"> · {dispatch.last_report.lat.toFixed(5)}, {dispatch.last_report.lon?.toFixed(5)}</span>}
                  </dd>
                </>
              )}
              {last && (
                <>
                  <dt className="text-ink-3">Last decision</dt>
                  <dd className="text-ink-1">
                    {last.task.replace("_", " ").replaceAll("_", " ")}: <span className="font-semibold">{last.selected ?? "none"}</span>
                    <span className="text-ink-3"> · {last.engine}{last.acted ? "" : " · not acted on"}</span>
                  </dd>
                </>
              )}
            </dl>
            <p className="text-[10.5px] leading-snug text-ink-3">
              Simulator only. Real flight stays manual; no model output reaches flight control.
            </p>
          </>
        ) : (
          <p className="text-[12px] text-ink-3">No simulation running. Pick one on the left.</p>
        )}
      </div>
    </Section>
  );
}

function RecentEvents() {
  const events = useStore((s) => s.events);
  const recent = events.slice(-8).reverse();
  return (
    <Section title="What just happened" className="order-5 shrink-0 xl:min-h-[160px]">
      {recent.length === 0 ? (
        <p className="px-2.5 py-2 text-[12px] text-ink-3">Nothing yet.</p>
      ) : (
        <ul className="divide-y divide-[var(--border)]">
          {recent.map((e) => (
            <li key={e.event_id} className="flex gap-2 px-2.5 py-1.5 text-[11.5px] leading-snug">
              <span className="tnum w-10 shrink-0 text-ink-3">{(e.ts_ms / 1000).toFixed(0)} s</span>
              <span className="min-w-0 text-ink-2">{e.text}</span>
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}

/** The map with the review pins (people the detector tagged), a click target for driving a person, and the launchpad. */
function DemoMap() {
  const obs = useIncident((s) => s.observations);
  const target = useStore((s) => s.demoMapTarget);
  const pins: MapPin[] = obs
    .filter((v) => v.observation.world_position)
    .map((v) => ({
      id: v.observation.id!,
      x: v.observation.world_position!.x,
      y: v.observation.world_position!.y,
      status: v.observation.provenance === "manual" ? "manual" : v.review_status ?? "pending",
      label: v.observation.label,
      uncertainty: v.observation.horizontal_uncertainty_m,
    }));
  return (
    <Section title={target ? `Map · click where ${target.label} should go` : "Map"} className="order-4 h-[300px] shrink-0">
      <div className="relative h-full min-h-[200px]">
        <FusedMap
          pins={pins}
          placing={!!target}
          onPlace={(x, y) => {
            const t = useStore.getState().demoMapTarget;
            useStore.setState({ demoMapTarget: null });
            if (t) void t.onPlace(x, y);
          }}
        />
      </div>
    </Section>
  );
}

const DISPATCH_TEXT: Record<DispatchStatus["phase"], string> = {
  TAKEOFF: "taking off",
  TRANSIT: "flying to the search area",
  SEARCH: "searching",
  COVERED: "area covered, nobody matched",
  TRACKING: "match found: tracking",
  LOST: "match lost: searching again",
};

/** Dispatch: launch from the pad, search for a described person, track them, report where they are. */
function DispatchPanel() {
  const meta = useStore((s) => s.demoWorld);
  const dispatch = useStore((s) => s.state?.dispatch ?? null);
  const incident = useIncident((s) => s.incident);
  const showToast = useStore((s) => s.showToast);
  const [text, setText] = useState("");
  const [lastSeen, setLastSeen] = useState<[number, number] | null>(null);
  const [radius, setRadius] = useState(50);
  const preset = meta?.dispatch ?? null;
  useEffect(() => {
    // each scenario prefills its own request
    setText(preset?.description ?? "");
    setLastSeen(preset?.last_seen_xy ?? null);
    setRadius(preset?.radius_m ?? 50);
  }, [preset?.description, preset?.last_seen_xy?.[0], preset?.last_seen_xy?.[1], preset?.radius_m]);
  if (!meta?.launchpad) return null;
  const run = (f: () => Promise<unknown>, ok?: string) =>
    f().then(() => ok && showToast(ok)).catch((e) => showToast(String((e as Error).message), "error"));
  return (
    <Section title="Dispatch from the launchpad">
      <div className="flex flex-col gap-1.5 px-2.5 py-2 text-[12px]">
        {dispatch ? (
          <>
            <div>
              <span className="font-semibold">{DISPATCH_TEXT[dispatch.phase]}</span>
              <span className="text-ink-3">{dispatch.description ? ` · looking for "${dispatch.description}"` : " · a sweep (operator command): nothing to match"}</span>
            </div>
            <div className="text-[11px] text-ink-3">
              {dispatch.people_seen} {dispatch.people_seen === 1 ? "person" : "people"} seen by the detector
              {dispatch.rejected ? ` · ${dispatch.rejected} match${dispatch.rejected === 1 ? "" : "es"} rejected` : ""}
              {dispatch.matched_by ? ` · ${dispatch.matched_by}` : ""}
            </div>
            <div className="flex gap-1">
              {dispatch.phase === "TRACKING" && (
                <Btn onClick={() => run(() => actions.followRelease(), "Match rejected: searching again")} title="Wrong person: release the lock and search again">
                  Wrong person
                </Btn>
              )}
              <Btn tone="ghost" onClick={() => run(() => actions.cancelDispatch(), "Dispatch cancelled")}>Cancel</Btn>
            </div>
          </>
        ) : (
          <>
            <label className="flex flex-col gap-0.5">
              <span className="text-[11px] text-ink-3">Who to look for (clothing words, never a face)</span>
              <input
                value={text}
                onChange={(e) => setText(e.target.value)}
                placeholder="red shirt, jeans"
                className="h-7 rounded-md border border-line bg-surface-2 px-2 text-[12px] text-ink-1"
              />
            </label>
            <div className="flex flex-wrap items-center gap-1.5 text-[11px] text-ink-3">
              <span>Last seen {lastSeen ? `at ${lastSeen[0].toFixed(0)}, ${lastSeen[1].toFixed(0)} m` : "near the pad"}</span>
              <Btn
                tone="ghost"
                onClick={() => useStore.setState({ demoMapTarget: { label: "the search", onPlace: async (x, y) => setLastSeen([x, y]) } })}
              >
                Pick on map
              </Btn>
              <label className="flex items-center gap-1">
                radius
                <input type="number" min={20} max={200} value={radius} onChange={(e) => setRadius(Number(e.target.value))} className="h-6 w-14 rounded border border-line bg-surface-2 px-1 text-[11px]" />
                m
              </label>
            </div>
            <Btn tone="primary" disabled={!incident || !text.trim()} onClick={() => run(() => actions.dispatch(text, lastSeen, radius), "Dispatched")}>
              <Rocket size={13} /> Dispatch
            </Btn>
            <p className="text-[10.5px] leading-snug text-ink-3">
              The drone takes off, flies search legs over the area, matches the people its detector finds against the words, tracks the
              first clear match and reports where they are from the pad. Simulator only.
            </p>
          </>
        )}
      </div>
    </Section>
  );
}

interface SimPerson {
  id: string; role: string; xy: [number, number]; heading_deg: number; speed_mps: number; active: boolean; controlled: boolean;
  shirt: [number, number, number]; pants: [number, number, number];
}

const bgr = (c: [number, number, number]) => `rgb(${c[2]},${c[1]},${c[0]})`;

/** Drive the simulated people: pick one, send them to a point on the map, or steer with the arrow keys. */
function PeoplePanel() {
  const [people, setPeople] = useState<SimPerson[]>([]);
  const [sel, setSel] = useState<string | null>(null);
  const [speed, setSpeed] = useState(1.4);
  const showToast = useStore((s) => s.showToast);
  const keys = useRef<Set<string>>(new Set());
  const boxRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    let stop = false;
    const load = () =>
      api.get<{ available: boolean; people: SimPerson[] }>("/api/simulation/people").then((r) => !stop && setPeople(r.available ? r.people : [])).catch(() => undefined);
    void load();
    const id = window.setInterval(load, 1000);
    return () => {
      stop = true;
      window.clearInterval(id);
    };
  }, []);
  // arrow keys (or WASD) while the panel has focus: a direction, renewed while held
  useEffect(() => {
    if (!sel) return;
    const DIR: Record<string, [number, number]> = { ArrowUp: [0, 1], w: [0, 1], ArrowDown: [0, -1], s: [0, -1], ArrowLeft: [-1, 0], a: [-1, 0], ArrowRight: [1, 0], d: [1, 0] };
    const send = () => {
      let dx = 0;
      let dy = 0;
      for (const k of keys.current) {
        const d = DIR[k];
        if (d) {
          dx += d[0];
          dy += d[1];
        }
      }
      if (!dx && !dy) return;
      const heading = (Math.atan2(dx, dy) * 180) / Math.PI;
      void actions.person(sel, "drive", { heading_deg: heading, speed, hold_s: 0.5 }).catch(() => undefined);
    };
    const down = (e: KeyboardEvent) => {
      if (!(e.key in DIR) || !boxRef.current?.contains(document.activeElement)) return;
      e.preventDefault();
      keys.current.add(e.key);
      send();
    };
    const up = (e: KeyboardEvent) => keys.current.delete(e.key);
    const id = window.setInterval(send, 300);
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    return () => {
      window.clearInterval(id);
      window.removeEventListener("keydown", down);
      window.removeEventListener("keyup", up);
    };
  }, [sel, speed]);
  if (people.length === 0) return null;
  const run = (f: () => Promise<unknown>, ok?: string) =>
    f().then(() => ok && showToast(ok)).catch((e) => showToast(String((e as Error).message), "error"));
  const chosen = people.find((p) => p.id === sel) ?? null;
  return (
    <Section title="People (simulator)">
      <div ref={boxRef} tabIndex={0} className="flex flex-col gap-1.5 px-2.5 py-2 text-[12px] outline-none focus:ring-1 focus:ring-[var(--s1)]">
        <ul className="flex flex-col gap-0.5">
          {people.filter((p) => p.active).map((p) => (
            <li key={p.id}>
              <button
                type="button"
                onClick={() => setSel(p.id === sel ? null : p.id)}
                className={`flex w-full items-center gap-2 rounded px-1.5 py-1 text-left ${p.id === sel ? "bg-surface-3 text-ink-1" : "text-ink-2 hover:bg-surface-2"}`}
              >
                <span className="inline-flex h-3 w-3 shrink-0 overflow-hidden rounded-sm border border-line-strong">
                  <span className="h-full w-1/2" style={{ background: bgr(p.shirt) }} />
                  <span className="h-full w-1/2" style={{ background: bgr(p.pants) }} />
                </span>
                <span className="font-mono text-[11px]">{p.id}</span>
                <span className="text-[11px] text-ink-3">{p.role}</span>
                <span className="tnum ml-auto text-[11px] text-ink-3">{p.speed_mps.toFixed(1)} m/s{p.controlled ? " · driven" : ""}</span>
              </button>
            </li>
          ))}
        </ul>
        {chosen && (
          <div className="flex flex-wrap items-center gap-1.5 border-t border-line pt-1.5">
            <Btn onClick={() => useStore.setState({ demoMapTarget: { label: chosen.id, onPlace: (x, y) => run(() => actions.person(chosen.id, "goto", { x, y, speed }), `${chosen.id} walking there`) } })}>
              Send to a map point
            </Btn>
            <Btn onClick={() => run(() => actions.person(chosen.id, "stop"))}>Stop</Btn>
            <Btn tone="ghost" onClick={() => run(() => actions.person(chosen.id, "resume"), `${chosen.id} back on the script`)}>Resume script</Btn>
            <label className="flex items-center gap-1 text-[11px] text-ink-3">
              <select value={speed} onChange={(e) => setSpeed(Number(e.target.value))} className="h-6 rounded border border-line bg-surface-2 px-1 text-[11px] text-ink-1">
                <option value={1.4}>walk</option>
                <option value={3}>jog</option>
                <option value={4.5}>run</option>
              </select>
            </label>
            <span className="w-full text-[10.5px] text-ink-3">Click here, then hold the arrow keys (or WASD) to steer {chosen.id}. The drone follows whoever it is tracking.</span>
          </div>
        )}
      </div>
    </Section>
  );
}

/** The phone controller: the target is driven from a phone. The server opens a Cloudflare quick tunnel on request;
 *  its public address becomes a QR code the phone scans. */
function PhonePanel() {
  const [t, setT] = useState<{ available: boolean; running: boolean; starting: boolean; url: string | null; error: string | null } | null>(null);
  const [busy, setBusy] = useState(false);
  const canvas = useRef<HTMLCanvasElement>(null);
  const showToast = useStore((s) => s.showToast);
  useEffect(() => {
    let stop = false;
    const load = () => api.get<typeof t>("/api/tunnel").then((r) => !stop && setT(r)).catch(() => undefined);
    void load();
    const id = window.setInterval(load, 2000);
    return () => {
      stop = true;
      window.clearInterval(id);
    };
  }, []);
  const tok = (() => {
    try {
      return localStorage.getItem("overwatch.token");
    } catch {
      return null;
    }
  })();
  const link = t?.url ? `${t.url}/drive${tok ? `#token=${encodeURIComponent(tok)}` : ""}` : null;
  useEffect(() => {
    if (!link || !canvas.current) return;
    void QRCode.toCanvas(canvas.current, link, { width: 220, margin: 1, color: { dark: "#101114", light: "#f4f4f2" } });
  }, [link]);
  const run = (f: () => Promise<unknown>) => {
    setBusy(true);
    f().catch((e) => showToast(String((e as Error).message), "error")).finally(() => setBusy(false));
  };
  return (
    <Section title="Drive the target from a phone">
      <div className="flex flex-col items-center gap-2 px-2.5 py-2 text-[12px]">
        {link ? (
          <>
            <canvas ref={canvas} className="rounded-md" />
            <a href={link} target="_blank" rel="noreferrer" className="break-all text-center font-mono text-[11px] text-[var(--s1)] underline">
              {link}
            </a>
            <Btn tone="ghost" disabled={busy} onClick={() => run(() => api.post("/api/tunnel/stop"))}>Stop the tunnel</Btn>
          </>
        ) : t?.starting ? (
          <p className="text-ink-3">Opening the tunnel… (about 30 s)</p>
        ) : (
          <>
            <Btn tone="primary" disabled={busy || t?.available === false} onClick={() => run(() => api.post("/api/tunnel/start"))}>
              Open a tunnel and show the QR code
            </Btn>
            {t?.available === false && <p className="text-center text-[11px] text-[var(--warning)]">cloudflared is not installed on this machine.</p>}
            {t?.error && <p className="break-all text-center text-[11px] text-[var(--warning)]">{t.error}</p>}
          </>
        )}
        <p className="text-center text-[10.5px] leading-snug text-ink-3">Scan with the phone: a joystick drives the target and the drone follows. The address is public while the tunnel runs.</p>
      </div>
    </Section>
  );
}

interface TigerStatus {
  enabled: boolean; connected: boolean; host: string | null; rows_written: Record<string, number>; queued: number; dropped: number;
  last_write_s_ago: number | null; last_error: string | null;
}
interface TigerMetrics {
  available: boolean; reason?: string; totals?: { events: number; telemetry: number; detections: number };
  events_1m?: { bucket: string; kind: string; n: number }[];
  telemetry_10s?: { bucket: string; n: number; alt_m: number | null; speed_mps: number | null; battery_pct: number | null }[];
  detections_10s?: { bucket: string; n: number; tracks: number; confidence: number | null }[];
}

/** A small line of one series over time, drawn from the continuous aggregate's buckets. */
function Spark({ values, color, label, unit }: { values: (number | null)[]; color: string; label: string; unit: string }) {
  const v = values.map((x) => x ?? 0);
  const W = 150;
  const H = 34;
  const max = Math.max(1e-6, ...v);
  const min = Math.min(0, ...v);
  const pts = v.map((y, i) => `${(i / Math.max(1, v.length - 1)) * W},${H - ((y - min) / (max - min || 1)) * (H - 4) - 2}`);
  const last = values.length ? values[values.length - 1] : null;
  return (
    <div className="flex items-center gap-2">
      <div className="w-[88px] text-[11px] text-ink-3">{label}</div>
      <svg width={W} height={H} className="shrink-0" aria-label={`${label} over time`}>
        {pts.length > 1 && <polyline points={pts.join(" ")} fill="none" stroke={color} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />}
        {pts.length === 1 && <circle cx={W / 2} cy={H / 2} r={3} fill={color} />}
      </svg>
      <span className="tnum text-[11.5px] text-ink-1">{last == null ? "–" : `${last.toFixed(unit === "" ? 0 : 1)} ${unit}`}</span>
    </div>
  );
}

/** Live metrics from the Tiger Data mirror: the event log, telemetry and detections land in TimescaleDB hypertables
 *  and the continuous aggregates roll them up per 10 s and per minute. */
function TigerPanel() {
  const incident = useIncident((s) => s.incident);
  const [st, setSt] = useState<TigerStatus | null>(null);
  const [m, setM] = useState<TigerMetrics | null>(null);
  useEffect(() => {
    let stop = false;
    const load = () => {
      api.get<TigerStatus>("/api/tiger").then((r) => !stop && setSt(r)).catch(() => undefined);
      const iid = useIncident.getState().incident?.id;
      if (iid) api.get<TigerMetrics>(`/api/incidents/${iid}/tiger/metrics?minutes=10`).then((r) => !stop && setM(r)).catch(() => undefined);
    };
    void load();
    const id = window.setInterval(load, 5000);
    return () => {
      stop = true;
      window.clearInterval(id);
    };
  }, [incident?.id]);
  if (!st?.enabled) return null;
  const rows = st.rows_written;
  const perMin = (m?.events_1m ?? []).reduce<Record<string, number>>((acc, e) => ({ ...acc, [e.bucket]: (acc[e.bucket] ?? 0) + e.n }), {});
  return (
    <Section
      title="Live metrics · Tiger Data"
      right={<Dot color={st.connected ? "var(--good)" : "var(--warning)"} pulse={st.connected} />}
    >
      <div className="flex flex-col gap-1.5 px-2.5 py-2 text-[12px]">
        <div className="text-[11px] text-ink-3">
          {st.connected ? `TimescaleDB at ${st.host}` : st.last_error ? `reconnecting: ${st.last_error}` : "connecting…"}
          {" · "}
          {(rows.ow_events + rows.ow_telemetry + rows.ow_detections).toLocaleString()} rows written this session
          {st.dropped ? ` · ${st.dropped} dropped while down` : ""}
        </div>
        {m?.available ? (
          <>
            <Spark label="detections / 10 s" unit="" color="var(--s1)" values={(m.detections_10s ?? []).map((d) => d.n)} />
            <Spark label="people tracked" unit="" color="var(--s4)" values={(m.detections_10s ?? []).map((d) => d.tracks)} />
            <Spark label="altitude" unit="m" color="var(--s7)" values={(m.telemetry_10s ?? []).map((t) => t.alt_m)} />
            <Spark label="speed" unit="m/s" color="var(--warning)" values={(m.telemetry_10s ?? []).map((t) => t.speed_mps)} />
            <Spark label="events / min" unit="" color="var(--ink-2)" values={Object.keys(perMin).sort().map((k) => perMin[k])} />
            <div className="text-[10.5px] text-ink-3">
              this incident: {m.totals?.events.toLocaleString()} events · {m.totals?.telemetry.toLocaleString()} telemetry rows · {m.totals?.detections.toLocaleString()} detections, from
              continuous aggregates (ow_detections_10s, ow_telemetry_10s, ow_events_1m)
            </div>
          </>
        ) : (
          <div className="text-[11px] text-ink-3">{m?.reason ?? "no incident metrics yet"}</div>
        )}
      </div>
    </Section>
  );
}
