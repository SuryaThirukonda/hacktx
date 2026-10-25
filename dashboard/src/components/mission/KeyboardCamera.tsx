import { useEffect, useRef, useState } from "react";
import { api } from "../../lib/api";
import { useStore } from "../../lib/store";
import { Btn } from "../ui";
import { Section } from "./common";

const CAMERA_KEYS: Record<string, [number, number, number, number]> = {
  KeyA: [-1,0,0,0], ArrowLeft: [-1,0,0,0], KeyD: [1,0,0,0], ArrowRight: [1,0,0,0],
  KeyW: [0,1,0,0], ArrowUp: [0,1,0,0], KeyS: [0,-1,0,0], ArrowDown: [0,-1,0,0],
};
const MOVEMENT_KEYS: Record<string, [number, number, number, number]> = {
  KeyW: [0,0,1,0], KeyS: [0,0,-1,0], KeyA: [0,0,0,-1], KeyD: [0,0,0,1],
  ArrowLeft: [-1,0,0,0], ArrowRight: [1,0,0,0], ArrowUp: [0,1,0,0], ArrowDown: [0,-1,0,0],
};

/** Explicitly acquired, expiring camera-only control. Never re-arms the phone's pilot latch. */
export function KeyboardCamera({ requestedMovement }: { requestedMovement?: boolean } = {}) {
  const auto = useStore((s) => s.state?.autonomy);
  const bridge = useStore((s) => s.extras?.bridge_control);
  const connected = useStore((s) => s.stateConn === "open");
  const sim = useStore((s) => s.state?.mode === "SIMULATED");
  const activeMovement = auto?.motion_profile === "visual_follow";
  const movement = requestedMovement ?? activeMovement;
  const profileMatches = movement === activeMovement;
  const ready = connected && profileMatches && (auto?.motion_profile === "camera_only" || activeMovement) && auto?.mode !== "ESTOP"
    && Boolean(sim || (bridge?.connected && bridge.capable && !bridge.inhibited && (auto?.target_is_sim || auto?.real_flight_allowed)));
  const reason = !profileMatches ? "Press Enable aircraft controls above to apply the selected capability first."
    : !connected ? "Website disconnected from backend. Reconnect before using controls."
    : !sim && !bridge?.connected ? "Open the SDK FPV screen and tap Start Stream."
    : auto?.mode === "ESTOP" ? "E-STOP is active. Recover to manual before enabling controls."
    : auto?.motion_profile !== "camera_only" && !movement ? "Keyboard needs the yaw/camera-tilt profile."
    : "Press Enable aircraft controls above with neutral RC sticks, then wait for confirmation.";
  const [enabled, setEnabled] = useState(false);
  const [message, setMessage] = useState("");
  const held = useRef(new Set<string>());
  const sendNow = useRef<() => void>(() => {});
  useEffect(() => {
    if (!enabled || !ready) { setEnabled(false); return; }
    let closed = false, session: string | null = null, seq = 0, pending = false;
    held.current.clear();
    const KEYS = movement ? MOVEMENT_KEYS : CAMERA_KEYS;
    const stop = () => {
      if (closed) return;
      closed = true;
      held.current.clear();
      setEnabled(false);
      if (session) void api.post("/api/autonomy/keyboard/stop", { session, seq: ++seq }).catch(() => {});
    };
    const send = async () => {
      if (closed || !session || pending) return;
      let yaw = 0, tilt = 0, forward = 0, right = 0;
      for (const key of held.current) { yaw += KEYS[key]?.[0] ?? 0; tilt += KEYS[key]?.[1] ?? 0; forward += KEYS[key]?.[2] ?? 0; right += KEYS[key]?.[3] ?? 0; }
      pending = true;
      try {
        await api.post("/api/autonomy/keyboard/input", {
          session, seq: ++seq, yaw: Math.max(-1, Math.min(1, yaw)), tilt: Math.max(-1, Math.min(1, tilt)),
          forward: Math.max(-1, Math.min(1, forward)), right: Math.max(-1, Math.min(1, right)),
        });
      } catch (e) { setMessage(String((e as Error).message)); stop(); }
      finally { pending = false; }
    };
    sendNow.current = () => { void send(); };
    void api.post<{ session: string }>("/api/autonomy/keyboard/start").then((r) => {
      session = r.session;
      if (closed) void api.post("/api/autonomy/keyboard/stop", { session, seq: ++seq }).catch(() => {});
      else { setMessage("Keyboard active · RC always has priority"); void send(); }
    }).catch((e: Error) => { setMessage(e.message); stop(); });
    const typing = (target: EventTarget | null) => target instanceof HTMLElement
      && (target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName));
    const down = (e: KeyboardEvent) => {
      if (e.code === "Escape") { e.preventDefault(); stop(); return; }
      if (typing(e.target) || e.altKey || e.ctrlKey || e.metaKey) { stop(); return; }
      if (!KEYS[e.code]) return;
      e.preventDefault(); held.current.add(e.code); void send();
    };
    const up = (e: KeyboardEvent) => {
      if (!KEYS[e.code]) return;
      e.preventDefault(); held.current.delete(e.code); void send();
    };
    const visibility = () => { if (document.hidden) stop(); };
    const focus = (e: FocusEvent) => { if (typing(e.target)) stop(); };
    const timer = window.setInterval(() => { void send(); }, 75);
    window.addEventListener("keydown", down); window.addEventListener("keyup", up);
    window.addEventListener("blur", stop); window.addEventListener("pagehide", stop);
    document.addEventListener("visibilitychange", visibility); document.addEventListener("focusin", focus);
    return () => {
      stop(); window.clearInterval(timer); sendNow.current = () => {};
      window.removeEventListener("keydown", down); window.removeEventListener("keyup", up);
      window.removeEventListener("blur", stop); window.removeEventListener("pagehide", stop);
      document.removeEventListener("visibilitychange", visibility); document.removeEventListener("focusin", focus);
    };
  }, [enabled, ready, movement]);
  return <Section title="Manual control" className="shrink-0">
    <div className="flex flex-col gap-2 p-2.5 text-[12px]">
      <p>{movement ? "W/S: forward/back. A/D: sideways. ←/→: rotate. ↑/↓: camera pitch. Speed ≤0.5 m/s; altitude stays with the RC." : "W/S or ↑/↓: camera tilt. A/D or ←/→: drone rotation. No translation."}</p>
      <Btn tone={enabled ? "primary" : "default"} onClick={() => {
        if (!ready) { setMessage(reason); return; }
        setMessage(""); setEnabled((v) => !v);
      }}>{enabled ? "Stop manual keyboard" : "Enable manual keyboard"}</Btn>
      <div className="grid grid-cols-2 gap-1">
        {[...(movement ? [["KeyW", "W Forward"], ["KeyS", "S Backward"], ["KeyA", "A Left"], ["KeyD", "D Right"]] : []), ["ArrowLeft", "← Rotate left"], ["ArrowUp", "↑ Tilt up"], ["ArrowDown", "↓ Tilt down"], ["ArrowRight", "→ Rotate right"]].map(([code, label]) =>
          <button key={code} disabled={!enabled || !ready} className="rounded border border-line px-2 py-1 disabled:opacity-40"
            onPointerDown={(e) => { e.preventDefault(); e.currentTarget.setPointerCapture(e.pointerId); held.current.add(code); sendNow.current(); }}
            onPointerUp={() => { held.current.delete(code); sendNow.current(); }}
            onPointerCancel={() => { held.current.delete(code); sendNow.current(); }}
            onLostPointerCapture={() => { held.current.delete(code); sendNow.current(); }}>{label}</button>)}
      </div>
      <p className="text-ink-3">Release keys to stop. Escape, leaving the tab, or RC input ends keyboard control. Enabling pauses automatic follow.</p>
      {!ready && <p className="text-ink-3">{reason}</p>}
      {message && <p role="status">{message}</p>}
    </div>
  </Section>;
}
