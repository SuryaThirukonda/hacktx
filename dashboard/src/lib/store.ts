import { create } from "zustand";
import type {
  Alert,
  DecisionRecord,
  EventMsg,
  Extras,
  FaultSpec,
  TranscriptLine,
  WorldState,
} from "../types/contracts";
import { wsUrl } from "./api";
import { ReconnectingSocket, type SocketStatus } from "./ws";

export interface KinSample {
  t: number;
  range: number | null;
  closing: number | null;
  speed: number | null;
  ttc: number | null;
}

export interface Layers {
  ortho: boolean;
  grid: boolean;
  tracks: boolean;
  trails: boolean;
  uncertainty: boolean;
  footprint: boolean;
  lidar: boolean;
  occupancy: boolean;
  zones: boolean;
  predicted: boolean;
}

export type RightTab = "ops" | "sim" | "calib" | "system";

/** A scenario's story on the demo page (backend sim/worlds/manifests/<id>.json). */
export interface ScenarioManifest {
  scenario: string;
  title: string;
  problem_line: string;
  beats: { t: number; caption: string; highlight_entity?: string | null; camera_preset?: "chase" | "overview" | "top" | null }[];
  success_line: string | null;
  default?: boolean;
}

interface Store {
  stateConn: SocketStatus;
  // demo page: where the next map click goes (driving a person, picking a search centre) and the world's meta
  demoMapTarget: { label: string; onPlace: (x: number, y: number) => unknown } | null;
  demoWorld: {
    launchpad?: [number, number] | null;
    dispatch?: { description: string; last_seen_xy?: [number, number] | null; radius_m?: number } | null;
    scenario?: string;
    manifest?: ScenarioManifest | null;
  } | null;
  // the scenario story's current beat: what to highlight on the map and in 3D, and the 3D camera preset
  storyFocus: { entity: string | null; camera: "chase" | "overview" | "top" | null; beat: number } | null;
  eventsConn: SocketStatus;
  state: WorldState | null;
  extras: Extras | null;
  config: Record<string, unknown> | null;
  faultSpecs: Record<string, FaultSpec>;
  events: EventMsg[];
  decisions: DecisionRecord[];
  alerts: Alert[];
  transcript: TranscriptLine[];
  kinHistory: KinSample[];
  layers: Layers;
  speak: boolean;
  rightTab: RightTab;
  toast: { text: string; kind: "ok" | "error" } | null;
  setLayer: (k: keyof Layers, v: boolean) => void;
  setSpeak: (v: boolean) => void;
  setRightTab: (t: RightTab) => void;
  showToast: (text: string, kind?: "ok" | "error") => void;
}

/** Stable empty array for selectors (a fresh [] per call would loop useSyncExternalStore). */
export const EMPTY: never[] = [];

const RING = { events: 400, decisions: 300, alerts: 200, transcript: 200, kin: 900 };

function loadBool(key: string, dflt: boolean): boolean {
  try {
    const v = localStorage.getItem(key);
    return v === null ? dflt : v === "1";
  } catch {
    return dflt;
  }
}

export const useStore = create<Store>((set, get) => ({
  stateConn: "connecting",
  demoMapTarget: null,
  demoWorld: null,
  storyFocus: null,
  eventsConn: "connecting",
  state: null,
  extras: null,
  config: null,
  faultSpecs: {},
  events: [],
  decisions: [],
  alerts: [],
  transcript: [],
  kinHistory: [],
  layers: {
    ortho: true,
    grid: true,
    tracks: true,
    trails: true,
    uncertainty: true,
    footprint: true,
    lidar: true,
    occupancy: true,
    zones: true,
    predicted: true,
  },
  speak: loadBool("overwatch.speak", false),
  rightTab: "ops",
  toast: null,
  setLayer: (k, v) => set({ layers: { ...get().layers, [k]: v } }),
  setSpeak: (v) => {
    try {
      localStorage.setItem("overwatch.speak", v ? "1" : "0");
    } catch {
      /* storage unavailable */
    }
    set({ speak: v });
  },
  setRightTab: (t) => set({ rightTab: t }),
  showToast: (text, kind = "ok") => {
    set({ toast: { text, kind } });
    window.setTimeout(() => {
      if (get().toast?.text === text) set({ toast: null });
    }, 3200);
  },
}));

function push<T>(arr: T[], items: T[], cap: number): T[] {
  const out = arr.concat(items);
  return out.length > cap ? out.slice(out.length - cap) : out;
}

function speakAlert(a: Alert) {
  if (!useStore.getState().speak || !a.spoken_text || !a.delivery.includes("phone_speech")) return;
  try {
    const synth = window.speechSynthesis;
    if (a.severity === "critical") synth.cancel();
    const u = new SpeechSynthesisUtterance(a.spoken_text);
    u.rate = a.severity === "critical" ? 1.15 : 1.05;
    synth.speak(u);
  } catch {
    /* speech unavailable */
  }
}

let started = false;
let eventsSocket: ReconnectingSocket | null = null;
const byTs = <T extends { ts_ms?: number; request_ts_ms?: number }>(a: T, b: T) =>
  (a.ts_ms ?? a.request_ts_ms ?? 0) - (b.ts_ms ?? b.request_ts_ms ?? 0);

export function connectStreams() {
  if (started) return;
  started = true;
  new ReconnectingSocket(wsUrl("/ws/state"), {
    onStatus: (s) => useStore.setState({ stateConn: s }),
    onMessage: (raw) => {
      const msg = raw as { type: string };
      if (msg.type === "state") {
        const st = raw as WorldState;
        const prev = useStore.getState();
        if (prev.state && st.session_id !== prev.state.session_id) {
          // new session (scenario restart / server restart): drop old history and re-fetch this session's backlog
          useStore.setState({ kinHistory: [], events: [], decisions: [], alerts: [], transcript: [] });
          eventsSocket?.restart();
        }
        const k = st.kinematics;
        const sample: KinSample = {
          t: st.server_ts_ms,
          range: k.valid ? k.range_m : null,
          closing: k.valid ? k.closing_mps : null,
          speed: k.valid ? k.subject_speed_mps : null,
          ttc: k.valid ? k.ttc_s : null,
        };
        useStore.setState({ state: st, kinHistory: push(useStore.getState().kinHistory, [sample], RING.kin) });
      } else if (msg.type === "extras") {
        useStore.setState({ extras: raw as Extras });
      } else if (msg.type === "hello") {
        const h = raw as { config: Record<string, unknown>; faults: Record<string, FaultSpec> };
        useStore.setState({ config: h.config, faultSpecs: h.faults });
      }
    },
  });
  eventsSocket = new ReconnectingSocket(wsUrl("/ws/events"), {
    onStatus: (s) => useStore.setState({ eventsConn: s }),
    onMessage: (raw) => {
      const msg = raw as { type: string; items?: unknown[] };
      if (msg.type === "backlog" && msg.items) {
        const items = msg.items as { type: string }[];
        useStore.setState({
          events: (items.filter((i) => i.type === "event") as EventMsg[]).sort(byTs),
          decisions: (items.filter((i) => i.type === "decision") as DecisionRecord[]).sort(byTs),
          alerts: (items.filter((i) => i.type === "alert") as Alert[]).sort(byTs),
          transcript: (items.filter((i) => i.type === "transcript") as TranscriptLine[]).sort(byTs),
        });
        return;
      }
      const s = useStore.getState();
      switch (msg.type) {
        case "event":
          useStore.setState({ events: push(s.events, [raw as EventMsg], RING.events) });
          break;
        case "decision":
          useStore.setState({ decisions: push(s.decisions, [raw as DecisionRecord], RING.decisions) });
          break;
        case "alert": {
          const a = raw as Alert;
          useStore.setState({ alerts: push(s.alerts, [a], RING.alerts) });
          speakAlert(a);
          break;
        }
        case "transcript":
          useStore.setState({ transcript: push(s.transcript, [raw as TranscriptLine], RING.transcript) });
          break;
        case "ack": {
          const id = (raw as { alert_id: string }).alert_id;
          useStore.setState({ alerts: s.alerts.map((a) => (a.alert_id === id ? { ...a, acked: true } : a)) });
          break;
        }
      }
    },
  });
}
