// Incident state for the mission dashboard: REST for collections, /ws/incidents/{id} for the event log.
// Every list is refetched when an event of its kind arrives, so the UI always shows what the store holds.
import { create } from "zustand";
import type { Alert as IAlert, Incident, IncidentEvent, MissionAction, ObservationView, SceneAnchor } from "../types/incident";
import { api, wsUrl } from "./api";
import { ReconnectingSocket, type SocketStatus } from "./ws";

export type Mission = "search" | "follow" | "structure" | "indoor";

export interface UseCaseScenario {
  id: string;
  name: string;
  description: string;
  duration_s: number;
}
export interface UseCase {
  id: string;
  name: string;
  mission: Mission;
  status: "ready" | "partial" | "planned";
  summary: string;
  scenarios: UseCaseScenario[];
  not_built?: string;
}
export interface SourceItem {
  id: string;
  kind: string;
  name: string;
  connection_status: "connected" | "disconnected" | "error" | "not_configured";
  source_mode: "live" | "replay" | "simulated";
  detail: string;
}
export interface SourceList {
  active: string;
  value: string | null;
  mode: string;
  items: SourceItem[];
}
export interface StructureItem {
  observation_id: string;
  label: string;
  status: string;
  blocked: boolean;
  text: string;
  provenance: string;
}
export interface StructureView {
  buildings: { building_id: string; name: string; side_a: string | null; source: string; sides: Record<string, StructureItem[]> }[];
  note: string;
}
export interface Metrics {
  observations: { total: number; with_valid_world_position: number; without_world_position: number; by_provenance: Record<string, number> };
  candidates: { total: number; confirmed: number; rejected: number; pending: number };
  detection_to_review: { n: number; median_s: number | null; max_s: number | null };
  confirmation_to_alert: { n: number; median_s: number | null; max_s: number | null };
  alert_ack_latency: { n: number; median_s: number | null; max_s: number | null };
  alerts: { total: number; acknowledged: number };
  tracks: { total: number; loss_events: number };
  localization_error: { n: number; median_m: number; max_m: number; source: string } | null;
  localization_error_note: string | null;
  events: number;
  computed_from: string;
  sources: Record<string, string>;
}

export interface FollowView {
  status: "none" | "tracking" | "lost" | "unverified" | "released";
  text: string;
  track_id: string | null;
  designation: number;
  designated_by: string | null;
  confirmed_tracks: string[];
  last_seen_xy: [number, number] | null;
  trail: { t_ms: number; track_id: string; xy: [number, number] | null; err_m: number | null; identity: "confirmed" | "unverified" }[];
  motion: { valid: boolean; reason?: string; distance_m?: number; speed_mps?: number; heading_deg?: number | null; window_s?: number; position_uncertainty_m?: number; note?: string; source?: string };
}

interface IncidentStore {
  mode: string;
  usecases: UseCase[];
  activeScenario: string | null;
  incident: Incident | null;
  observations: ObservationView[];
  alerts: IAlert[];
  events: IncidentEvent[];
  anchors: SceneAnchor[];
  sources: SourceList | null;
  metrics: Metrics | null;
  structure: StructureView | null;
  missionActions: MissionAction[];
  decisionMode: string | null;
  follow: FollowView | null;
  conn: SocketStatus;
  selectedObs: string | null;
  placing: boolean;
  error: string | null;
  cursorSeq: number | null; // replay cursor over the event log (null = live)
  gcpMode: "image" | "map" | null; // ground-control-point capture: next click on the video, then on the map
  gcpPending: [number, number] | null;
  gcpPairs: { u: number; v: number; x: number; y: number }[];
  setCursor: (seq: number | null) => void;
  setSelectedObs: (id: string | null) => void;
  setPlacing: (v: boolean) => void;
  setError: (e: string | null) => void;
}

export const useIncident = create<IncidentStore>((set) => ({
  mode: "",
  usecases: [],
  activeScenario: null,
  incident: null,
  observations: [],
  alerts: [],
  events: [],
  anchors: [],
  sources: null,
  metrics: null,
  structure: null,
  missionActions: [],
  decisionMode: null,
  follow: null,
  conn: "connecting",
  selectedObs: null,
  placing: false,
  error: null,
  cursorSeq: null,
  gcpMode: null,
  gcpPending: null,
  gcpPairs: [],
  setCursor: (seq) => set({ cursorSeq: seq }),
  setSelectedObs: (id) => set({ selectedObs: id }),
  setPlacing: (v) => set({ placing: v }),
  setError: (e) => set({ error: e }),
}));

let socket: ReconnectingSocket | null = null;
let currentId: string | null = null;
const pending = new Set<string>();
let flushTimer: number | null = null;

async function refetch(what: string) {
  const iid = currentId;
  if (!iid) return;
  try {
    if (what === "observations") {
      const r = await api.get<{ items: ObservationView[] }>(`/api/incidents/${iid}/observations`);
      useIncident.setState({ observations: r.items });
    } else if (what === "alerts") {
      const r = await api.get<{ items: IAlert[] }>(`/api/incidents/${iid}/alerts`);
      useIncident.setState({ alerts: r.items });
    } else if (what === "incident") {
      const r = await api.get<Incident>(`/api/incidents/${iid}`);
      useIncident.setState({ incident: r });
    } else if (what === "sources") {
      const r = await api.get<SourceList>(`/api/incidents/${iid}/sources`);
      useIncident.setState({ sources: r });
    } else if (what === "scene") {
      const r = await api.get<{ anchors: SceneAnchor[] }>(`/api/incidents/${iid}/scene`);
      useIncident.setState({ anchors: r.anchors });
    } else if (what === "structure") {
      const r = await api.get<StructureView>(`/api/incidents/${iid}/structure`);
      useIncident.setState({ structure: r });
    } else if (what === "actions") {
      const r = await api.get<{ items: MissionAction[]; decisions: { mode_label?: string } | null }>(`/api/incidents/${iid}/actions`);
      useIncident.setState({ missionActions: r.items, decisionMode: r.decisions?.mode_label ?? null });
    } else if (what === "follow") {
      const r = await api.get<FollowView>(`/api/incidents/${iid}/follow`);
      useIncident.setState({ follow: r });
    } else if (what === "metrics") {
      const r = await api.get<Metrics>(`/api/incidents/${iid}/metrics`);
      useIncident.setState({ metrics: r });
    }
  } catch (e) {
    useIncident.setState({ error: String((e as Error).message) });
  }
}

function schedule(...what: string[]) {
  what.forEach((w) => pending.add(w));
  if (flushTimer !== null) return;
  flushTimer = window.setTimeout(() => {
    flushTimer = null;
    const all = [...pending];
    pending.clear();
    all.forEach((w) => void refetch(w));
  }, 150);
}

const KIND_TO_LISTS: Record<string, string[]> = {
  observation: ["observations", "metrics", "structure"],
  review: ["observations", "metrics", "structure"],
  decision_proposal: ["actions"],
  follow_designated: ["follow"],
  follow_redesignated: ["follow"],
  follow_lost: ["follow"],
  follow_reacquired: ["follow"],
  follow_rebind_unverified: ["follow"],
  follow_released: ["follow"],
  follow_point: ["follow"],
  track_started: ["metrics"],
  track_lost: ["metrics"],
  action_approved: ["actions", "alerts"],
  action_rejected: ["actions"],
  action_done: ["actions"],
  side_a_designated: ["structure"],
  alert: ["alerts", "metrics"],
  alert_delivered: ["alerts"],
  alert_acknowledged: ["alerts", "metrics"],
  mission_changed: ["incident"],
  incident_retitled: ["incident"],
  georef_set: ["incident"],
  source_changed: ["sources", "incident"],
  source_updated: ["sources"],
  anchor: ["scene", "structure"],
  transform: ["scene"],
};

function onEvent(ev: IncidentEvent) {
  useIncident.setState((s) => ({ events: s.events.length > 1500 ? [...s.events.slice(-1200), ev] : [...s.events, ev] }));
  const lists = KIND_TO_LISTS[ev.kind];
  if (lists) schedule(...lists);
}

export function connectIncident(iid: string) {
  if (currentId === iid && socket) return;
  socket?.close();
  currentId = iid;
  useIncident.setState({ events: [], observations: [], alerts: [], anchors: [], missionActions: [], selectedObs: null });
  schedule("incident", "observations", "alerts", "sources", "scene", "metrics", "structure", "actions", "follow");
  socket = new ReconnectingSocket(wsUrl(`/ws/incidents/${iid}`), {
    onStatus: (st) => useIncident.setState({ conn: st }),
    onMessage: (raw) => {
      const m = raw as { type: string; items?: IncidentEvent[] } & IncidentEvent;
      if (m.type === "backlog") {
        useIncident.setState({ events: m.items ?? [] });
        schedule("observations", "alerts", "metrics", "actions");
      } else if (m.type === "incident_event") onEvent(m);
    },
  });
}

export async function loadHome() {
  const [u, list] = await Promise.all([
    api.get<{ mode: string; active_scenario: string | null; items: UseCase[] }>("/api/usecases"),
    api.get<{ current: string | null; items: Incident[] }>("/api/incidents"),
  ]);
  useIncident.setState({ usecases: u.items, mode: u.mode, activeScenario: u.active_scenario });
  if (list.current) connectIncident(list.current);
}

setInterval(() => {
  if (currentId) void refetch("metrics");
}, 5000);

/** Load a scenario. An incident belongs to one place: a scenario in another world starts a new incident, one in the
 *  same world restarts in place. */
export async function chooseScenario(sid: string, mission: Mission, title: string) {
  const g = useIncident.getState();
  const worldOf = (id: string | null | undefined) => g.usecases.find((u) => u.scenarios.some((s) => s.id === id))?.id;
  if (!g.incident || worldOf(g.incident.scenario_id ?? g.activeScenario) !== worldOf(sid)) {
    await actions.startIncident(title, mission, sid);
  } else {
    // the same world restarts in place; the incident's title and scenario follow the new scenario
    await api.post(`/api/incidents/${g.incident.id}/replay/restart`, { value: sid, title });
    useIncident.setState({ activeScenario: sid });
    void refetch("incident");
  }
}

// -- actions (each performs a real backend call; the event stream refreshes the views) --------------------------
const iidOrThrow = () => {
  const iid = useIncident.getState().incident?.id ?? currentId;
  if (!iid) throw new Error("start an incident first");
  return iid;
};

export const actions = {
  async startIncident(title: string, mission: Mission, scenarioId: string | null) {
    const inc = await api.post<Incident>("/api/incidents", { title, mission_mode: mission, scenario_id: scenarioId });
    useIncident.setState({ incident: inc, activeScenario: scenarioId ?? useIncident.getState().activeScenario });
    connectIncident(inc.id!);
    return inc;
  },
  setMission: (m: Mission) => api.post(`/api/incidents/${iidOrThrow()}/mission`, { mission: m }),
  review: (obsId: string, status: "confirmed" | "rejected" | "pending") =>
    api.post(`/api/observations/${obsId}/review`, { status, reviewer: "operator" }),
  flagTrack: (trackId: string, label = "possible person", category = "person") =>
    api.post(`/api/incidents/${iidOrThrow()}/observations/from-track`, { track_id: trackId, label, category }),
  addManualMarker: (x: number, y: number, label: string, category: string) => {
    const iid = iidOrThrow();
    return api.post(`/api/incidents/${iid}/observations`, {
      category, label, provenance: "manual", sensor_source_id: "manual",
      world_position: { frame: `enu:${iid}`, x, y, z: 0 }, horizontal_uncertainty_m: 2.0, position_estimation_method: "manual",
    });
  },
  brief: (message: string, obsIds: string[], severity: "info" | "attention" | "warning" | "critical" = "attention") =>
    api.post(`/api/incidents/${iidOrThrow()}/alerts`, { message, source_observation_ids: obsIds, severity }),
  ack: (alertId: string) => api.post(`/api/alerts/${alertId}/acknowledge`, { by: "operator" }),
  replay: (what: "start" | "pause" | "restart", value?: unknown) =>
    api.post(`/api/incidents/${iidOrThrow()}/replay/${what}`, what === "restart" ? { value: value ?? null } : {}),
  selectSource: (source: string, value?: string) => api.post(`/api/incidents/${iidOrThrow()}/sources`, { source, value }),
  setGeoref: (lat: number, lon: number, note: string) =>
    api.post(`/api/incidents/${iidOrThrow()}/georef`, { origin_lat: lat, origin_lon: lon, note }),
  fitGround: (pairs: { u: number; v: number; x: number; y: number }[]) =>
    api.post("/api/calibration/ground-points", {
      points: pairs.map((p, i) => ({ ...p, holdout: pairs.length >= 5 && i % 4 === 3 })),
    }),
  followDesignate: (trackId: string) => api.post(`/api/incidents/${iidOrThrow()}/follow/designate`, { track_id: trackId, by: "operator" }),
  followConfirm: () => api.post(`/api/incidents/${iidOrThrow()}/follow/confirm`, { by: "operator" }),
  followReject: () => api.post(`/api/incidents/${iidOrThrow()}/follow/reject`, { by: "operator" }),
  followRelease: () => api.post(`/api/incidents/${iidOrThrow()}/follow/release`, { by: "operator" }),
  followDescribe: (text: string) => api.post<{ ok: boolean; note: string }>(`/api/incidents/${iidOrThrow()}/follow/describe`, { text }),
  followDescribeConfirm: () => api.post(`/api/incidents/${iidOrThrow()}/follow/describe/confirm`, { by: "operator" }),
  lookFor: (text: string) => api.post(`/api/incidents/${iidOrThrow()}/look-for`, { text }),
  autonomy: (mode: "FOLLOW" | "HOLD" | "MANUAL") => api.post("/api/autonomy/mode", { mode }),
  dispatch: (description: string, lastSeen: [number, number] | null, radiusM: number) =>
    api.post("/api/autonomy/dispatch", { description, last_seen_xy: lastSeen, radius_m: radiusM }),
  cancelDispatch: () => api.post("/api/autonomy/dispatch/cancel", {}),
  person: (id: string, action: "goto" | "drive" | "stop" | "resume", extra: { x?: number; y?: number; speed?: number; heading_deg?: number; hold_s?: number } = {}) =>
    api.post(`/api/simulation/people/${encodeURIComponent(id)}`, { action, ...extra }),
  decideAction: (id: string, approve: boolean) => api.post(`/api/actions/${id}/${approve ? "approve" : "reject"}`, { by: "operator" }),
  setSideA: (buildingId: string, compass: string) =>
    api.post(`/api/incidents/${iidOrThrow()}/structure/side-a`, { building_id: buildingId, compass }),
  tagStructure: (side: string, kind: string) => {
    const blocked = kind === "blocked entrance";
    return api.post(`/api/incidents/${iidOrThrow()}/observations`, {
      category: kind === "hazard" ? "hazard" : "opening", label: blocked ? "blocked entrance" : kind, provenance: "manual",
      sensor_source_id: "manual", attributes: { side, blocked, tagged_by: "operator" },
    });
  },
};

/** Observations as they stood at event ``cursor`` (replaying the incident log); live view when cursor is null. */
export function asOf(obs: ObservationView[], events: IncidentEvent[], cursor: number | null): ObservationView[] {
  if (cursor === null) return obs;
  const createdAt = new Map<string, number>();
  const review = new Map<string, { status: string; reviewer: string | null; at: string | null }>();
  for (const e of events) {
    if (e.seq! > cursor) break;
    const p = e.payload as Record<string, unknown>;
    if (e.kind === "observation") {
      const o = p.observation as { id?: string } | undefined;
      if (o?.id) createdAt.set(o.id, e.seq!);
    } else if (e.kind === "review") {
      review.set(String(p.observation_id), { status: String(p.status), reviewer: (p.reviewer as string) ?? null, at: (p.at as string) ?? null });
    }
  }
  return obs
    .filter((v) => createdAt.has(v.observation.id!))
    .map((v) => {
      const r = review.get(v.observation.id!);
      return { ...v, review_status: (r?.status ?? "pending") as ObservationView["review_status"], reviewer: r?.reviewer ?? null, review_timestamp: r?.at ?? null };
    });
}
