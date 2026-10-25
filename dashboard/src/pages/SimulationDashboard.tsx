import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { ArrowLeft, ArrowUpRight, Box, Maximize2, Moon, Pause, Play, Radio, RotateCcw, Sun } from "lucide-react";
import { api, withToken } from "../lib/api";
import { actions, loadHome, useIncident } from "../lib/incident";
import { useStore } from "../lib/store";
import { useTheme } from "../lib/theme";
import { useSimulationFeed } from "../lib/useSimulationFeed";
import { useCalloutVoice } from "../lib/useCalloutVoice";

import { SurveyMap } from "../components/SurveyMap";
import { TargetSARPanel, targetPaths, targetPins, useTargetSAR } from "../components/TargetSARPanel";
import { PursuitCard } from "../components/PursuitCard";
import { VoicePanel } from "../components/VoicePanel";
import { BottomPanel } from "../components/mission/BottomPanel";
import { Mission } from "./Mission";
import "./SimulationDashboard.css";


const SharedScene = lazy(() => import("../components/SimulationScene").then((m) => ({ default: m.SimulationScene })));

export function SimulationDashboard() {
  const [theme, toggleTheme] = useTheme();
  const cases = useIncident((s) => s.usecases);
  const incident = useIncident((s) => s.incident);
  const state = useStore((s) => s.state);
  const initialCity = new URLSearchParams(window.location.search).get("scenario") === "T12";
  const [worldId, setWorldId] = useState(initialCity ? "city" : "pursuit");
  const [sid, setSid] = useState(initialCity ? "T12" : "PP0");
  const [running, setRunning] = useState(initialCity && new URLSearchParams(window.location.search).get("running") === "1");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [view, setView] = useState<"drone" | "3d">("drone");
  const [tab, setTab] = useState<"map" | "callouts">("map");
  const [spawn, setSpawn] = useState<[number, number] | null>(null);
  const [speed, setSpeed] = useState(1); // 1x, 2x or 3x real time: the simulation clock, not the drone

  const viewport = useRef<HTMLDivElement>(null);
  const city = worldId === "city" && sid === "T12";
  const feed = useSimulationFeed(city, running);
  const target = useTargetSAR(running && !city ? incident?.id : undefined);
  const serverSpeed = city ? feed.cityState?.speed_target : state?.sim?.speed;
  useEffect(() => {
    // the server keeps the pace across restarts and tabs: show what it is running at
    if (serverSpeed != null && Math.round(serverSpeed) !== speed && [1, 2, 3].includes(Math.round(serverSpeed))) setSpeed(Math.round(serverSpeed));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [serverSpeed]);
  const selected = cases.find((c) => c.id === worldId);
  const scenario = selected?.scenarios.find((s) => s.id === sid);
  const callouts = city ? feed.cityState?.callouts ?? [] : state?.rescue?.callouts ?? [];
  const time = city ? feed.cityState?.t ?? 0 : feed.frame?.t_s ?? 0;
  const voice = useCalloutVoice(city ? feed.cityState?.run_no ?? 0 : state?.session_id ?? "", time, callouts,
    (n) => city ? `/api/test12/voice/${feed.cityState?.run_no}/${n}.mp3` : `/api/rescue/voice/${n}.mp3`);
  useEffect(() => { void loadHome().catch((e: Error) => setError(e.message)); }, []);
  useEffect(() => {
    if (selected && !selected.scenarios.some((s) => s.id === sid)) setSid(selected.scenarios[0]?.id ?? "");
  }, [selected, sid]);
  const perform = async (fn: () => Promise<unknown>) => {
    setBusy(true); setError("");
    try { await fn(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  const launch = () => perform(async () => {
    await api.post("/api/test12/stop");
    if (city && sid === "T12") await api.post("/api/test12/start", { spawn });
    else await actions.startIncident(`${selected?.name} · ${sid}`, selected?.mission ?? "search", sid);
    setRunning(true);
  });
  const back = () => perform(async () => {
    if (city && sid === "T12") await api.post("/api/test12/stop");
    else await actions.replay("pause");
    setRunning(false);
  });
  if (state?.mode === "LIVE") return <Mission />;
  return <div className={`simulation-app ${running ? "is-running" : "is-landing"}`} data-appearance={theme}>
    {!running ? <>
      <nav className="launch-nav"><a className="overwatch-brand" href="/">◈ Overwatch</a>
        <div className="nav-center"><h1>SIMULATION WORKSPACE</h1></div>
        <button className="theme-button" aria-label="Toggle color theme" onClick={toggleTheme}>{theme === "navy" ? <Sun size={17} /> : <Moon size={17} />}</button>
        <a href="/command">Incident command <ArrowUpRight size={15} /></a></nav>
      <main className="launch-main world-picker">
        <div className="launch-intro situation-intro"><p className="eyebrow"><span /> Select a situation</p>
          <h2 className="text-3xl font-medium">An overhead view. A clearer next step.</h2>
          <p>Explore pursuit, search, flood and fire through labelled simulations. A person reviews assistance before dispatch.</p></div>
        <div className="world-cards" aria-label="Simulation situations">
          {cases.map((c, i) => <button key={c.id} className={`world-card world-${c.id} ${worldId === c.id ? "selected" : ""}`}
            aria-pressed={worldId === c.id} onClick={() => { setWorldId(c.id); setSid(c.scenarios[0]?.id ?? ""); }}>
            <img className="world-photo" src={`/images/situations/${c.id}.jpg`} alt="" />
            <span className="world-index">0{i + 1}</span><span className="world-card-content"><span className="world-tag">SIMULATED · {c.mission}</span>
              <strong>{c.name}</strong><span className="world-line">{c.summary}</span><span className="world-count">{c.scenarios.length} scenarios <ArrowUpRight size={14} /></span></span>
          </button>)}
        </div>
        <div className="launch-setup"><section><label className="field-label" htmlFor="scenario">Choose a scenario</label>
          <select id="scenario" value={sid} onChange={(e) => setSid(e.target.value)}>{selected?.scenarios.map((s) => <option key={s.id} value={s.id}>{s.id} · {s.name}</option>)}</select>
          <p className="scenario-description">{scenario?.description}</p></section>
          <section className="launch-action"><div className="view-choice">{(["drone", "3d"] as const).map((v) => <button key={v} aria-pressed={view === v} onClick={() => setView(v)}>{v === "drone" ? "Drone POV" : "3D world"}</button>)}</div>
            <p>Local simulation · physical flight permission remains separate</p><button disabled={busy || !sid} className="launch-button" onClick={launch}>{busy ? "Starting simulation…" : "Launch simulation"} <ArrowUpRight size={16} /></button></section></div>
        {error && <p role="alert" className="simulation-error">{error}</p>}
      </main><footer className="launch-footer"><span className="overwatch-brand">Overwatch</span><span>Measurements, predictions and simulator truth remain distinct.</span><a href="/demo">Classic demo tools</a></footer>
    </> : <>
      <nav className="mission-nav"><button className="back-button" disabled={busy} onClick={back}><ArrowLeft size={15} /><span>Situations</span></button>
        <span className="overwatch-brand">◈ Overwatch</span><span className="mission-name">{selected?.name}<strong>{sid}</strong></span><span className="sim-badge">SIMULATED</span>
        <a href="/command">Incident command <ArrowUpRight size={13} /></a><a href="/responder">Responder</a><button aria-label="Toggle color theme" onClick={toggleTheme}><Sun size={15} /></button></nav>
      <div className="mission-viewport" ref={viewport}>
        <header className="mission-heading"><div><p className="eyebrow">Mission workspace</p><h1>{scenario?.name ?? "City chase"}</h1></div>
          <div className="playback-controls"><span className="mission-clock">{time.toFixed(1)} s</span>
            {!city && <><button onClick={() => void perform(() => actions.replay(state?.sim?.paused ? "start" : "pause"))}>{state?.sim?.paused ? <Play size={13} /> : <Pause size={13} />}<span>{state?.sim?.paused ? "Resume" : "Pause"}</span></button>
            <button onClick={() => void perform(() => actions.replay("restart", sid))}><RotateCcw size={13} /><span>Restart</span></button></>}
            {city && <button onClick={launch}><RotateCcw size={13} /><span>Restart City</span></button>}
            <button aria-label="Fullscreen workspace" onClick={() => void viewport.current?.requestFullscreen()}><Maximize2 size={13} /></button></div></header>
        <div className="mission-panels"><section className="visual-panel"><div className="panel-toolbar"><div role="tablist" aria-label="Camera view">
          <button role="tab" aria-selected={view === "drone"} onClick={() => setView("drone")}>Drone POV</button><button role="tab" aria-selected={view === "3d"} onClick={() => setView("3d")}><Box size={13} />3D world</button></div><span className="panel-meta">
          {city && speed > 1 && (feed.cityState?.speed ?? speed) < speed * 0.85 && (
            <span title="The City engine runs YOLO on every sixth frame; on this CPU it cannot always keep the asked pace">{(feed.cityState?.speed ?? 0).toFixed(1)}x achieved · </span>
          )}
          <span className="speed-controls" role="group" aria-label="Simulation speed">
              {[1, 2, 3].map((x) => (
                <button
                  key={x}
                  aria-pressed={speed === x}
                  className={speed === x ? "selected" : ""}
                  title={`Run the simulation at ${x}x real time`}
                  onClick={() => void perform(() => (city ? actions.citySpeed(x) : actions.simSpeed(x)).then(() => setSpeed(x)))}
                >
                  <span>{x}x</span>
                </button>
              ))}
          </span>
          CAMERA / SYNTHETIC</span></div>
          <div className="mission-visual">{view === "3d" ? <Suspense fallback={<p className="p-4">Loading 3D…</p>}><SharedScene world={feed.world} frame={feed.frame} /></Suspense> :
            <img className="city-video" alt="Simulated drone camera" src={withToken(city && sid === "T12" ? "/api/test12/stream.mjpg" : "/streams/drone.mjpg")} />}
            <div className="view-corner"><span className="record-dot" />SIMULATED VIEW</div></div>
          <div className="mission-metrics"><div className="mission-metric"><span>Mission state</span><strong>{city ? feed.cityState?.phase_label ?? feed.cityState?.phase : state?.rescue?.phase ?? state?.autonomy.fsm ?? "Connecting"}</strong></div>
            <div className="mission-metric"><span>Seen ground</span><strong>{(city ? feed.cityState?.survey?.seen_m2 : feed.frame?.survey?.seen_m2) ?? 0} m²</strong></div>
            <div className="mission-metric"><span>Target</span><strong>{city ? feed.cityState?.target?.fresh ? "Observed" : "Last estimate" : target.target?.state ?? "Searching"}</strong></div>
            <div className="mission-metric"><span>Beacon</span><strong>{feed.frame?.beacon?.status ?? "Awaiting detection"}</strong></div></div></section>
          <section className="situational-panel"><div className="panel-toolbar"><div role="tablist" aria-label="Situation view"><button role="tab" aria-selected={tab === "map"} onClick={() => setTab("map")}>Survey map</button><button role="tab" aria-selected={tab === "callouts"} onClick={() => setTab("callouts")}>Callouts <span className="tab-count">{callouts.length}</span></button></div></div>
            <div className="situational-body">{tab === "map" ? <SurveyMap world={feed.world} frame={feed.frame} city={city ? feed.cityState : null} state={city ? null : state} spawn={spawn} onSpawn={city ? setSpawn : undefined} paths={targetPaths(target)} pins={targetPins(target)} /> :
              <div className="callout-tab"><div className="callout-tab-heading"><h2>Responder callouts</h2><button className="voice-toggle" aria-pressed={voice.enabled} onClick={voice.toggle}><Radio size={13} />Voice {voice.enabled ? "on" : "off"}</button></div>
                {callouts.length ? <><div className="latest-callout"><p>LATEST · SIMULATED</p><blockquote>{callouts.at(-1)?.speech}</blockquote></div><ol className="callout-history">{[...callouts].reverse().map((c) => <li key={c.n}><time>{c.t.toFixed(1)} s</time>{c.speech}</li>)}</ol></> : <p className="empty-callouts">No callouts yet.</p>}</div>}</div>
            <div className="mission-status"><span className="status-dot" />{city ? "City engine · separate simulated tracking" : "Estimated tracks · human review before assistance dispatch"}</div></section></div>
        <div className="below-hint"><span>Coverage records where the camera looked; hidden ground stays unsurveyed.</span><a href="#mission-record">Review, tracking and diagnostics ↓</a></div>
      </div>
      {(error || feed.error) && <p role="alert" className="simulation-error">{error || feed.error}</p>}
      <section id="mission-record" className="mission-record"><header className="record-heading"><p className="eyebrow">Mission record</p><h2>Evidence and operator decisions</h2><p>Predictions are labelled. Confirmed guidance and acknowledgment remain in the incident audit.</p></header>
        {!city && incident?.id && <div className="grid gap-4 mt-6 xl:grid-cols-2"><div><TargetSARPanel iid={incident.id} data={target} /><PursuitCard /><VoicePanel /></div><div className="diagnostic-content"><BottomPanel /></div></div>}
        {city && <div className="city-diagnostics mt-6"><div className="city-diagnostic-body"><p>City pursuit runs in its isolated simulator process. Ground-truth display is opt-in on the map.</p><ol>{feed.cityState?.events?.map((e, i) => <li key={i}><time>{e.t.toFixed(1)} s</time>{e.text}</li>)}</ol></div></div>}
      </section>
    </>}
  </div>;
}
