import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import { actions, useIncident } from "../../lib/incident";
import { useStore } from "../../lib/store";
import { Btn } from "../ui";
import { Section } from "./common";
import { KeyboardCamera } from "./KeyboardCamera";

export function BridgeSetup() {
  const mode = useStore((s) => s.state?.mode);
  const bridge = useStore((s) => s.extras?.bridge_control);
  const auto = useStore((s) => s.state?.autonomy);
  const drone = useStore((s) => s.state?.drone);
  const sources = useStore((s) => s.state?.sources);
  const follow = useIncident((s) => s.follow);
  const [profile, setProfile] = useState("visual_follow");
  useEffect(() => { if (auto?.motion_profile === "full") setProfile("full"); }, [auto?.motion_profile]);
  const [props, setProps] = useState(false);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [blockers, setBlockers] = useState<string[]>([]);
  useEffect(() => {
    if (mode !== "LIVE") return;
    let closed = false;
    const refresh = () => { void api.get<{blockers:string[]}>("/api/autonomy/readiness")
      .then((r) => { if (!closed) setBlockers(r.blockers); }).catch((e: Error) => { if (!closed) setMessage(e.message); }); };
    refresh(); const timer = window.setInterval(refresh, 1500);
    return () => { closed = true; window.clearInterval(timer); };
  }, [mode]);
  if (mode !== "LIVE") return null;
  const run = async (task: () => Promise<unknown>, note: string) => {
    setBusy(true); setMessage("");
    try { await task(); setMessage(note); }
    catch (e) { setMessage((e as Error).message); }
    finally { setBusy(false); }
  };
  const setup = (action: string) => api.post("/api/autonomy/bridge/setup", {action, props_removed: props});
  const connected = Boolean(bridge?.connected);
  const armed = connected && bridge?.capable && !bridge.inhibited && auto?.commands_enabled;
  const live = (id: string) => sources?.find((s) => s.source_id === id)?.status === "OK";
  const label = auto?.mode === "FOLLOW" ? (drone?.vstick_enabled ? "Follow active · DJI control enabled" : "Follow requested · awaiting DJI control") : armed ? "Controls enabled" : connected ? "Connected · controls off" : "Waiting for SDK stream";
  const relevant = blockers.filter((b) => !b.startsWith("Designation is not movement"));
  return <>
    <Section title="Drone controls" className="shrink-0"><div className="space-y-3 p-3 text-[12px]">
      <div className="flex items-center justify-between"><b>{label}</b><span>{auto?.mode ?? "MANUAL"}</span></div>
      <p className="text-ink-2">Video {live("drone") ? "live" : "offline"} · DJI telemetry {live("telemetry") ? "live" : "offline"} · RC always has priority</p>
      <label className="flex items-center gap-2">Control capability
        <select aria-label="Control capability" className="rounded border border-line bg-surface-2 p-1" value={profile} onChange={(e) => setProfile(e.target.value)}>
          {auto?.motion_profile === "full" && <option value="full">Full control · site checks required</option>}
          <option value="visual_follow">Movement + camera</option><option value="camera_only">Rotation + camera only</option>
        </select>
      </label>
      <p className="text-ink-3">Selected: {profile === "full" ? "site calibrated full control; GPS, fence and calibration required" : profile === "visual_follow" ? "forward/back, sideways, rotation and camera pitch" : "rotation and camera pitch; no translation"}. Press Enable to apply.</p>
      <div className="flex flex-wrap gap-2">
        <Btn tone="primary" disabled={busy} onClick={() => void run(
          () => setup(profile === "full" ? "enable_full_control" : profile === "visual_follow" ? "enable_visual_follow" : "enable_camera_control"),
          "Enable / re-arm sent. Wait for Controls enabled; then use the keyboard or Start follow.")}>{armed ? "Re-arm controls" : "Enable aircraft controls"}</Btn>
        <Btn tone="danger" onClick={() => void run(() => setup("disable_control"), "Stopped. Aircraft controls off; pilot in control.")}>Stop / RC control</Btn>
      </div>
      <p>Enable arms the selected capability. It does not take off or start following. Keep the RC neutral to re-arm.</p>
      <div className="rounded border border-line p-2 space-y-2">
        <p><b>Automatic control · target:</b> {follow?.status === "tracking" ? "Marked and visible" : follow?.status === "unverified" ? "Confirm this is the same person" : follow?.status === "lost" ? "Out of view · movement stopped" : "Click a person in the video, then Designate target"}</p>
        {follow?.status === "unverified" && <Btn onClick={() => void run(() => actions.followConfirm(), "Target confirmed")}>Confirm same person</Btn>}
        <Btn tone="primary" disabled={busy} onClick={() => void run(async () => {
          if (follow?.status !== "tracking") throw new Error("Designate a visible person first; confirm any changed track.");
          if (!armed) throw new Error(relevant[0] ?? "Press Enable aircraft controls first, then wait for confirmation.");
          await actions.autonomy("FOLLOW");
        }, "Follow requested. Check actual DJI virtual-stick response.")}>Start automatic follow</Btn>
      </div>
      {auto?.motion_profile === "visual_follow" && <p className="text-ink-3">Automatic follow centers the person and maintains their starting image size, not a measured distance. No automatic climb/descent.</p>}
      {!armed && relevant.length > 0 && <p role="status" className="text-[var(--warning)]">{relevant[0]}</p>}
      {bridge?.pilot_reason && !armed && <p className="text-ink-3">RC: {bridge.pilot_reason}</p>}
      {bridge?.error && <p role="alert" className="text-[var(--warning)]">DJI: {bridge.error}</p>}
      {message && <p role="status">{message}</p>}
      <details className="text-ink-3"><summary className="cursor-pointer">Connection details / DJI simulator</summary>
        <ul className="list-disc pl-4 my-2">{relevant.map((b) => <li key={b}>{b}</li>)}</ul>
        <p>DJI virtual stick: {drone?.vstick_enabled ? "enabled by DJI" : "not enabled"}</p>
        <label className="flex gap-2 my-2"><input type="checkbox" checked={props} onChange={(e) => setProps(e.target.checked)} />Landed, propellers removed for simulator</label>
        <div className="flex gap-2"><Btn disabled={!props || busy} onClick={() => void run(() => setup("sim_start"), "Simulator requested; wait for DJI confirmation.")}>Start DJI simulator</Btn>
        <Btn disabled={busy} onClick={() => void run(() => setup("sim_stop"), "Simulator stop requested.")}>Stop simulator</Btn></div>
      </details>
    </div></Section>
    <KeyboardCamera requestedMovement={profile === "visual_follow"} />
  </>;
}
