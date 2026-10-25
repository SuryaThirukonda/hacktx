// three.js scene for the 3D decision view (/3d). World coordinates from the backend are local ENU metres
// (x east, y north, z up); three.js is y-up, so (x, y, z) -> (x, z, -y).
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { OBJLoader } from "three/examples/jsm/loaders/OBJLoader.js";
import { CSS2DObject, CSS2DRenderer } from "three/examples/jsm/renderers/CSS2DRenderer.js";

export type XY = [number, number];

export interface Opening3D {
  id: string;
  center: XY;
  normal: XY;
  width: number;
  z0: number;
  z1: number;
  color: string;
}

export interface HazardGrid3D {
  x0: number;
  y0: number;
  nx: number;
  ny: number;
  res_m: number;
}

export interface WorldHazardFlood3D {
  kind: "flood";
  label: string;
  grid: HazardGrid3D;
  ground_m: number[];
  level0_m: number;
  rise_m_per_min: number;
  level_max_m: number;
  road_closed_depth_m: number;
}

export interface WorldHazardFire3D {
  kind: "fire";
  label: string;
  grid: HazardGrid3D;
  arrival_s: number[];
  ignitions: [number, number, number][];
  wind_to_deg: number;
  wind_speed_mps: number;
  keep_out_margin_s: number;
}

export type WorldHazard3D = WorldHazardFlood3D | WorldHazardFire3D;

export interface FrameHazardFlood3D {
  kind: "flood";
  label: string;
  level_m: number;
  max_depth_m: number;
  road_closed_depth_m: number;
  flooded_area_m2: number;
  deep_area_m2: number;
  areas: { kind: "water" | "deep_water"; polygon: XY[] }[];
  closed_roads: { a: XY; b: XY; depth_m: number }[];
  crew_route: { waypoints: XY[]; length_m: number; max_water_depth_m: number; goal: string } | null;
}

export interface FrameHazardFire3D {
  kind: "fire";
  label: string;
  wind_to_deg: number;
  wind_speed_mps: number;
  keep_out_margin_s: number;
  burning_area_m2: number;
  keep_out_area_m2: number;
  smoke_source_xy: XY | null;
  smoke_strength: number;
  areas: { kind: "fire" | "keep_out" | "smoke"; polygon: XY[] }[];
  openings: { id: string; side: string; kind: string; xy: XY; smoke: number; in_keep_out: boolean; condition: string }[];
  crew_route: { target_id: string; length_m: number; keep_out_cells: number; waypoints: XY[] } | null;
}

export type FrameHazard3D = FrameHazardFlood3D | FrameHazardFire3D;

export interface World3D {
  available: boolean;
  bounds: [number, number, number, number];
  ortho: string;
  trees: { id: string; xy: XY; trunk_r: number; trunk_h: number; canopy_r: number; canopy_z: number; color: string }[];
  boxes: { id: string; kind: string; center: XY; size: [number, number, number]; heading: number; z0: number; color: string; openings?: Opening3D[] }[];
  markers: { id: number; xy: XY; size: number }[];
  zones: { name: string; polygon: XY[] }[];
  geofence: XY[];
  hazards?: WorldHazard3D | null;
  drone_mesh: { model?: string; obj: string; texture: string; scale: number; quat_wxyz: [number, number, number, number]; source: string };
}

export interface Decision3D {
  id: string;
  t_ms: number;
  engine: string;
  model: string;
  mock: boolean;
  status: string;
  selected: string | null;
  confidence: number | null;
  probabilities: [string, number][];
  latency_ms: number | null;
  acted: boolean;
  reason: string;
  baseline: string | null;
  lost_s: number | null;
  cover: boolean | null;
  predicted: string | null;
  at: [number, number, number] | null;
}

export interface Frame3D {
  type: "frame";
  beacon?: { xy: XY; status?: string } | null;
  /** every rescue beacon with its assessed crew route (A* on the hazard grid) */
  beacons?: { id: string; n: number; xy: XY; status: string; route: XY[]; route_ok: boolean; walking: boolean; label: string }[];
  t_s: number;
  scenario: string;
  scenario_name: string;
  duration_s: number;
  paused: boolean;
  speed: number;
  engine: string;
  engine_model: string;
  ai_paused: boolean;
  pause_reason: string | null;
  drone: {
    model: string;
    pos: [number, number, number];
    quat: [number, number, number, number];
    heading: number;
    pitch: number;
    roll: number;
    vel: [number, number, number];
    gimbal_pitch: number;
    battery: number;
    thrusts: number[] | null;
  };
  footprint: XY[] | null;
  actors: {
    id: string; role: string; xy: XY; heading: number; height: number; shirt: string; pants: string; vest: boolean; visible: number;
    skin?: string; hair?: string; sleeves?: string; trousers?: string; jacket?: string | null; hat?: string | null; backpack?: string | null;
    phase?: number; speed?: number;
  }[];
  subject: { state: string; track_id: string | null; est_xy: XY | null; last_seen_xy: XY | null; search_radius: number | null; lost_ms: number | null };
  responder: { state: string; track_id: string | null; xy?: XY | null };
  callout?: string | null; // the latest spoken callout, drawn on the line from the responder to the target
  autonomy: {
    mode: string;
    fsm: string;
    primitive: string | null;
    primitive_source: string;
    geofence_margin_m: number | null;
    clamps: string[];
    command: { fwd: number; right: number; up: number; yaw: number } | null;
  };
  hazards?: FrameHazard3D | null;
  decisions: Decision3D[];
  /** the pipeline's street-graph belief while the subject is lost (what it believes, not ground truth) */
  pursuit?: {
    particles: [number, number, number][];
    hypotheses: { id: string; label: string; weight: number }[];
    candidates: { id: string; xy: XY; coverage: number; unsafe: string | null; chosen: boolean }[];
    footprint: XY[] | null;
    route: XY[];
  } | null;
  /** the live crew route through the hazard (missions/crew_routing.py) */
  crew_route?: { kind: string; waypoints: XY[]; label: string; ok: boolean } | null;
}

/** hypothesis colours, the same as the 2D map and the route reasoning card */
const HYP_HEX = ["#d95926", "#c98500", "#d55181", "#199e70"];

export type CamMode = "chase" | "overview" | "top";

// trail colours: who chose what the drone was doing (validated categorical palette, dark theme)
export const TRAIL = {
  tracking: "#199e70", // following the subject
  ai: "#9085e9", // flying a manoeuvre chosen by the decision model
  mock: "#898781", // ... by the labelled mock stand-in
  rules: "#c98500", // ... by the deterministic rules
  hold: "#d03b3b", // holding / handed to the operator
} as const;

export function trailKind(f: Frame3D): keyof typeof TRAIL {
  const a = f.autonomy;
  if (a.mode !== "FOLLOW" || a.fsm === "HOLD") return "hold";
  if (!a.primitive) return "tracking";
  if (a.primitive_source === "decision:mock") return "mock";
  if (a.primitive_source.startsWith("decision:") && a.primitive_source !== "decision:rules") return "ai";
  return "rules"; // rules primitives, and rules fallbacks when the engine failed or was below the gate
}

/** Colour of a decision: flown model choice, mock, or rules (fallback after an error, or below the gate). */
export function decisionColor(d: Decision3D): string {
  if (d.engine === "rules" || !d.acted) return TRAIL.rules;
  return d.mock ? TRAIL.mock : TRAIL.ai;
}

const v3 = (x: number, y: number, z = 0) => new THREE.Vector3(x, z, -y);
const C_ENU = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1, 0, 0), -Math.PI / 2);
const MAX_TRAIL = 6000;
const FLAME_COUNT = 280;
const SMOKE_COUNT = 96;

function label(text: string, cls: string): CSS2DObject {
  const el = document.createElement("div");
  el.className = cls;
  el.textContent = text;
  return new CSS2DObject(el);
}

function clearGroup(grp: THREE.Object3D) {
  grp.traverse((obj) => {
    if (obj instanceof CSS2DObject) {
      const el = obj.element;
      if (el && el.parentNode) el.parentNode.removeChild(el);
    }
  });
  grp.clear();
}

export class SceneController {
  renderer: THREE.WebGLRenderer;
  labels: CSS2DRenderer;
  scene = new THREE.Scene();
  camera: THREE.PerspectiveCamera;
  controls: OrbitControls;
  mode: CamMode = "chase";
  private staticGroup = new THREE.Group();
  private hazardGroup = new THREE.Group();
  private hazardOverlayGroup = new THREE.Group();
  private people = new Map<string, THREE.Group>();
  private drone = new THREE.Group();
  private droneBody = new THREE.Group();
  private beaconMark: THREE.Group | null = null;
  private beaconKey = "";
  private footFill: THREE.Mesh;
  private footRays: THREE.LineSegments;
  private trail: THREE.Line;
  private trailPos = new Float32Array(MAX_TRAIL * 3);
  private trailCol = new Float32Array(MAX_TRAIL * 3);
  private trailN = 0;
  private lastT = -1;
  private pins = new Map<string, THREE.Group>();
  private subjEst: THREE.Mesh;
  private lastSeen: THREE.Group;
  private searchRing: THREE.Line;
  private belief: THREE.Points;
  private beliefRoute: THREE.Line;
  private beliefFoot: THREE.Mesh;
  private beliefMarks = new THREE.Group();
  private beliefSig = "";
  private boxMeshes = new Map<string, THREE.Mesh>();
  /** the rescue crew's assessed way in (A* on the hazard grid), drawn as soon as a beacon is placed */
  private crewRouteGroup = new THREE.Group();
  private crewRouteKey = "";
  private calloutLine: THREE.Line;
  private calloutLabel: CSS2DObject;
  private focus: string | null = null; // the story beat's entity: actor:<id> | box:<id prefix> | hypothesis:<id> | route
  private focusRing: THREE.Mesh;
  private sun: THREE.DirectionalLight;
  private droneScale = 1;
  private raf = 0;
  private frame: Frame3D | null = null;
  private animationAt = 0;
  private target = new THREE.Vector3();
  private resizeObs: ResizeObserver;
  private camHeading: number | null = null;
  private worldHazards: WorldHazard3D | null = null;
  private waterMesh: THREE.Mesh | null = null;
  private fireGroundMesh: THREE.Mesh | null = null;
  private flameInst: THREE.InstancedMesh | null = null;
  private smokeInst: THREE.InstancedMesh | null = null;
  private fireLight: THREE.PointLight | null = null;
  private activeFlames: [number, number, number, number][] = []; // [wx, wy, intensity, seed]
  private smokeSource: XY | null = null;
  private smokeStrength = 0;
  private fireWindRad = Math.PI / 4;
  private fireWindMps = 2.5;
  private lastHazardOverlayBin = -1;
  private dummy = new THREE.Object3D();
  private tmpColor = new THREE.Color();
  rightInset = 345; // px covered by the decision panel

  constructor(private host: HTMLElement, rightInset = 345) {
    this.rightInset = rightInset;
    const w = host.clientWidth || 800;
    const h = host.clientHeight || 600;
    this.renderer = new THREE.WebGLRenderer({ antialias: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.setSize(w, h);
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    host.appendChild(this.renderer.domElement);
    this.labels = new CSS2DRenderer();
    this.labels.setSize(w, h);
    this.labels.domElement.style.position = "absolute";
    this.labels.domElement.style.inset = "0";
    this.labels.domElement.style.pointerEvents = "none";
    host.appendChild(this.labels.domElement);

    this.scene.background = new THREE.Color("#1c2530");
    this.scene.fog = new THREE.Fog("#1c2530", 90, 220);
    this.camera = new THREE.PerspectiveCamera(50, w / h, 0.1, 600);
    this.camera.position.set(30, 45, 40);
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.maxPolarAngle = Math.PI * 0.49;

    this.scene.add(new THREE.HemisphereLight("#cfe3ff", "#3b4a2f", 1.1));
    this.sun = new THREE.DirectionalLight("#fff4e0", 2.2);
    this.sun.position.set(-30, 60, 25);
    this.sun.castShadow = true;
    this.sun.shadow.mapSize.set(2048, 2048);
    const sc = this.sun.shadow.camera as THREE.OrthographicCamera;
    sc.left = -50;
    sc.right = 50;
    sc.top = 50;
    sc.bottom = -50;
    sc.far = 200;
    this.scene.add(this.sun, this.sun.target);
    this.scene.add(this.staticGroup, this.hazardGroup, this.hazardOverlayGroup);

    // camera footprint (true, from the simulated gimbal camera)
    this.footFill = new THREE.Mesh(
      new THREE.BufferGeometry(),
      new THREE.MeshBasicMaterial({ color: "#6fd0e8", transparent: true, opacity: 0.13, depthWrite: false, side: THREE.DoubleSide }),
    );
    this.footRays = new THREE.LineSegments(new THREE.BufferGeometry(), new THREE.LineBasicMaterial({ color: "#6fd0e8", transparent: true, opacity: 0.55 }));
    this.scene.add(this.footFill, this.footRays);

    // flight trail, coloured by who chose the manoeuvre
    const tg = new THREE.BufferGeometry();
    tg.setAttribute("position", new THREE.BufferAttribute(this.trailPos, 3));
    tg.setAttribute("color", new THREE.BufferAttribute(this.trailCol, 3));
    tg.setDrawRange(0, 0);
    this.trail = new THREE.Line(tg, new THREE.LineBasicMaterial({ vertexColors: true }));
    this.trail.frustumCulled = false;
    this.scene.add(this.trail);

    // what the pipeline believes about the subject (not ground truth)
    this.subjEst = new THREE.Mesh(
      new THREE.RingGeometry(0.75, 1.0, 40),
      new THREE.MeshBasicMaterial({ color: "#d95926", transparent: true, opacity: 0.9, side: THREE.DoubleSide, depthWrite: false }),
    );
    this.subjEst.rotation.x = -Math.PI / 2;
    this.subjEst.add(label("lock estimate", "s3d-lbl s3d-lbl-est"));
    this.lastSeen = new THREE.Group();
    const xm = new THREE.LineBasicMaterial({ color: "#d95926" });
    for (const s of [1, -1]) {
      this.lastSeen.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints([v3(-0.7, -0.7 * s, 0.06), v3(0.7, 0.7 * s, 0.06)]), xm));
    }
    const lsLabel = label("last seen", "s3d-lbl s3d-lbl-est");
    lsLabel.position.set(0, 0.4, 0);
    this.lastSeen.add(lsLabel);
    this.searchRing = new THREE.Line(new THREE.BufferGeometry(), new THREE.LineDashedMaterial({ color: "#d95926", dashSize: 0.6, gapSize: 0.4 }));
    this.scene.add(this.subjEst, this.lastSeen, this.searchRing);

    // the street-graph belief: particles per hypothesis, the chosen viewpoint's footprint and the route to it
    const bg = new THREE.BufferGeometry();
    bg.setAttribute("position", new THREE.BufferAttribute(new Float32Array(0), 3));
    bg.setAttribute("color", new THREE.BufferAttribute(new Float32Array(0), 3));
    this.belief = new THREE.Points(bg, new THREE.PointsMaterial({ size: 0.9, vertexColors: true, transparent: true, opacity: 0.85, depthWrite: false }));
    this.belief.frustumCulled = false;
    this.beliefRoute = new THREE.Line(new THREE.BufferGeometry(), new THREE.LineDashedMaterial({ color: "#5ad2e6", dashSize: 1.2, gapSize: 0.8 }));
    this.beliefFoot = new THREE.Mesh(
      new THREE.BufferGeometry(),
      new THREE.MeshBasicMaterial({ color: "#5ad2e6", transparent: true, opacity: 0.12, depthWrite: false, side: THREE.DoubleSide }),
    );
    this.scene.add(this.belief, this.beliefRoute, this.beliefFoot, this.beliefMarks);
    this.focusRing = new THREE.Mesh(
      new THREE.RingGeometry(1.6, 2.0, 48),
      new THREE.MeshBasicMaterial({ color: "#f4d35e", side: THREE.DoubleSide, transparent: true, opacity: 0.9, depthWrite: false }),
    );
    this.focusRing.rotation.x = -Math.PI / 2;
    this.focusRing.visible = false;
    this.scene.add(this.focusRing);
    // the callout line: from the responder (police or crew) to the drone's estimate of the target, carrying the
    // words the responder is being told, so the demo shows what the callout is relative to
    this.calloutLine = new THREE.Line(new THREE.BufferGeometry(), new THREE.LineDashedMaterial({ color: "#ffd166", dashSize: 1.2, gapSize: 0.8 }));
    this.calloutLine.visible = false;
    this.calloutLabel = label("", "s3d-lbl s3d-lbl-callout");
    this.calloutLabel.visible = false;
    this.scene.add(this.hazardGroup, this.crewRouteGroup, this.calloutLine, this.calloutLabel);

    this.buildDrone();
    this.scene.add(this.drone);
    this.resizeObs = new ResizeObserver(this.onResize);
    this.resizeObs.observe(host);
    this.onResize();
    this.loop();
  }

  dispose() {
    cancelAnimationFrame(this.raf);
    this.resizeObs.disconnect();
    this.controls.dispose();
    this.renderer.dispose();
    this.host.removeChild(this.renderer.domElement);
    this.host.removeChild(this.labels.domElement);
  }

  private onResize = () => {
    const w = this.host.clientWidth;
    const h = this.host.clientHeight;
    if (!w || !h) return;
    this.camera.aspect = w / h;
    if (this.rightInset > 0 && w > 900) {
      // centre the projection in the part of the canvas not covered by the decision panel on the right
      this.camera.setViewOffset(w, h, this.rightInset / 2, 0, w, h);
    } else {
      this.camera.clearViewOffset();
    }
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(w, h);
    this.labels.setSize(w, h);
  };

  setMode(m: CamMode) {
    this.mode = m;
    this.controls.enabled = m === "overview";
    this.droneScale = m === "chase" ? 2 : 5; // drawn larger so it reads at these distances; the HUD says so
    this.droneBody.scale.setScalar(this.droneScale);
    if (m === "overview") {
      if (this.worldHazards?.kind === "flood") {
        // Frame the flooded southern creek, stranded people, closed roads and the northern detour route
        this.controls.target.copy(v3(0, -38, 2));
        this.camera.position.copy(v3(-58, -125, 64));
        this.controls.update();
      } else if (this.worldHazards?.kind === "fire") {
        // Frame the SW fire ignition, NE spread, keep-out zone, two-storey house and crew route from staging
        this.controls.target.copy(v3(-6, -8, 3));
        this.camera.position.copy(v3(54, -76, 50));
        this.controls.update();
      } else if (this.frame) {
        const d = this.frame.drone.pos;
        const p = v3(d[0], d[1], d[2] * 0.45); // between the drone and the ground it is searching
        this.controls.target.copy(p);
        this.camera.position.copy(p).add(new THREE.Vector3(30, 30, 42));
        this.controls.update();
      }
    }
  }

  private buildDrone() {
    // placeholder until the X2 mesh loads: a small cross
    const ph = new THREE.Mesh(new THREE.BoxGeometry(0.6, 0.6, 0.08), new THREE.MeshStandardMaterial({ color: "#d8d8d8" }));
    ph.name = "placeholder";
    this.droneBody.add(ph);
    this.drone.add(this.droneBody);
    const ring = new THREE.Mesh(new THREE.RingGeometry(0.9, 1.05, 40), new THREE.MeshBasicMaterial({ color: "#199e70", side: THREE.DoubleSide }));
    ring.rotation.x = -Math.PI / 2;
    ring.name = "ground-dot";
    this.scene.add(ring);
    const lbl = label("DJI Mini 2 · simulated", "s3d-lbl s3d-lbl-drone");
    lbl.position.set(0, 1.4, 0);
    this.drone.add(lbl);
  }

  /** A DJI Mini 2 at true size (0.29 m motor to motor), in the aircraft body frame the attitude is given in:
   *  x forward, y left, z up (ENU, the same frame as the simulator's quaternion), so it sits level and flies nose
   *  first. Mini 2 shape: a slim grey body, a front gimbal with the camera, four arms with motor pods, two-blade
   *  props that spin while airborne, landing feet, rear status lights. */
  private props: THREE.Group[] = [];
  private buildMini2() {
    clearGroup(this.droneBody);
    this.props = [];
    const shell = new THREE.MeshStandardMaterial({ color: "#d9dbd7", roughness: 0.55, metalness: 0.05 });
    const dark = new THREE.MeshStandardMaterial({ color: "#2b3034", roughness: 0.7 });
    const arms = new THREE.MeshStandardMaterial({ color: "#b9bdbe", roughness: 0.6 });
    const g = this.droneBody;
    const add = (geom: THREE.BufferGeometry, mat: THREE.Material, x: number, y: number, z: number, rot?: [number, number, number]) => {
      const m = new THREE.Mesh(geom, mat);
      m.position.set(x, y, z);
      if (rot) m.rotation.set(rot[0], rot[1], rot[2]);
      m.castShadow = true;
      g.add(m);
      return m;
    };
    // body: a flat shell, a darker top deck, the battery hump underneath
    add(new THREE.BoxGeometry(0.138, 0.078, 0.042), shell, 0, 0, 0);
    add(new THREE.BoxGeometry(0.10, 0.06, 0.016), dark, -0.01, 0, 0.028);
    add(new THREE.BoxGeometry(0.085, 0.058, 0.02), dark, -0.012, 0, -0.03);
    // front: the gimbal link and the camera with its lens, looking forward and a little down
    add(new THREE.CylinderGeometry(0.009, 0.009, 0.03, 10), dark, 0.072, 0, -0.012, [0, 0, Math.PI / 2]);
    const cam = add(new THREE.BoxGeometry(0.022, 0.024, 0.022), dark, 0.084, 0, -0.02);
    cam.rotation.y = 0.5;
    const lens = add(new THREE.CylinderGeometry(0.006, 0.0075, 0.008, 12),
      new THREE.MeshStandardMaterial({ color: "#0d1a24", roughness: 0.2, metalness: 0.5 }), 0.098, 0, -0.027, [0, 0, Math.PI / 2]);
    lens.rotation.y = 0.5;
    for (const y of [-0.024, 0.024]) add(new THREE.SphereGeometry(0.004, 8, 8), dark, 0.069, y, 0.006); // sensor eyes
    // arms to the four motors (x forward, y left); the rear arms sit a little lower, as on the aircraft
    const motors: [number, number][] = [[0.105, 0.105], [0.105, -0.105], [-0.105, 0.105], [-0.105, -0.105]];
    for (const [mx, my] of motors) {
      const ang = Math.atan2(my, mx);
      const len = Math.hypot(mx, my) - 0.045;
      const zArm = mx > 0 ? 0.004 : -0.006;
      add(new THREE.BoxGeometry(len, 0.014, 0.011), arms, mx / 2 + 0.01 * Math.sign(mx), my / 2, zArm, [0, 0, ang]);
      add(new THREE.CylinderGeometry(0.0125, 0.0135, 0.022, 14), dark, mx, my, zArm + 0.006, [Math.PI / 2, 0, 0]); // motor pod
      add(new THREE.CylinderGeometry(0.004, 0.004, 0.006, 8), arms, mx, my, zArm + 0.02, [Math.PI / 2, 0, 0]); // shaft
      const hub = new THREE.Group();
      hub.position.set(mx, my, zArm + 0.023);
      hub.rotation.z = (mx * my > 0 ? 0.4 : -0.4) + Math.random() * 0.5;
      for (const k of [0, 1]) {
        const blade = new THREE.Mesh(new THREE.BoxGeometry(0.074, 0.011, 0.0022),
          new THREE.MeshStandardMaterial({ color: "#3a3f44", roughness: 0.5, transparent: true, opacity: 0.9 }));
        blade.position.x = k === 0 ? 0.037 : -0.037;
        blade.rotation.x = k === 0 ? 0.22 : -0.22; // blade pitch
        hub.add(blade);
      }
      g.add(hub);
      this.props.push(hub);
      add(new THREE.BoxGeometry(0.012, 0.012, 0.026), dark, mx * 0.92, my * 0.92, zArm - 0.018); // landing foot
    }
    // rear status lights: red on the left, green on the right
    add(new THREE.SphereGeometry(0.004, 8, 8), new THREE.MeshStandardMaterial({ color: "#ff3b3b", emissive: "#ff2020", emissiveIntensity: 0.9 }), -0.1, 0.1, 0.012);
    add(new THREE.SphereGeometry(0.004, 8, 8), new THREE.MeshStandardMaterial({ color: "#39ff7a", emissive: "#20ff60", emissiveIntensity: 0.9 }), -0.1, -0.1, 0.012);
  }

  private spinProps(airborne: boolean) {
    const w = airborne ? 0.55 : 0.0;
    this.props.forEach((p, i) => { p.rotation.z += i % 2 === 0 ? w : -w; });
  }

  loadDroneMesh(mesh: World3D["drone_mesh"]) {
    if (mesh.model === "mini2") {
      this.buildMini2();
      for (const child of this.drone.children) if (child instanceof CSS2DObject) child.element.textContent = "DJI Mini 2 · simulated";
      return;
    }
    const tex = new THREE.TextureLoader().load(mesh.texture);
    tex.colorSpace = THREE.SRGBColorSpace;
    new OBJLoader().load(mesh.obj, (obj) => {
      obj.traverse((c) => {
        if ((c as THREE.Mesh).isMesh) {
          (c as THREE.Mesh).material = new THREE.MeshStandardMaterial({ map: tex, roughness: 0.6, metalness: 0.1, side: THREE.DoubleSide });
          c.castShadow = true;
        }
      });
      const [w, x, y, z] = mesh.quat_wxyz; // MJCF geom quat, applied in the MuJoCo body frame
      obj.quaternion.set(x, y, z, w).normalize();
      obj.scale.setScalar(mesh.scale);
      this.droneBody.remove(this.droneBody.getObjectByName("placeholder")!);
      // droneBody holds MuJoCo body-frame children; its rotation (C_ENU * q_body) is set per frame on `drone`
      this.droneBody.add(obj);
    });
  }

  /** Highlight the story beat's entity (or nothing). */
  setFocus(entity: string | null) {
    this.focus = entity;
    const pre = entity?.startsWith("box:") ? entity.slice(4) : null;
    for (const [id, m] of this.boxMeshes) {
      const mat = m.material as THREE.MeshLambertMaterial;
      mat.emissive.set(pre && id.startsWith(pre) ? "#6b5a12" : "#000000");
    }
    this.beliefSig = ""; // re-draw the candidate marks with the new emphasis
  }

  private makeGridSurface(g: HazardGrid3D, mat: THREE.Material): THREE.Mesh {
    const { x0, y0, nx, ny, res_m } = g;
    const nVerts = nx * ny;
    const pos = new Float32Array(nVerts * 3);
    const col = new Float32Array(nVerts * 3);
    for (let r = 0; r < ny; r++) {
      const wy = y0 + (r + 0.5) * res_m;
      for (let c = 0; c < nx; c++) {
        const wx = x0 + (c + 0.5) * res_m;
        const i = (r * nx + c) * 3;
        pos[i] = wx;
        pos[i + 1] = -0.2;
        pos[i + 2] = -wy;
        col[i] = 0.2;
        col[i + 1] = 0.5;
        col[i + 2] = 0.8;
      }
    }
    const idx: number[] = [];
    for (let r = 0; r < ny - 1; r++) {
      for (let c = 0; c < nx - 1; c++) {
        const i00 = r * nx + c;
        const i10 = i00 + 1;
        const i01 = (r + 1) * nx + c;
        const i11 = i01 + 1;
        idx.push(i00, i10, i01, i10, i11, i01);
      }
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    geo.setAttribute("color", new THREE.BufferAttribute(col, 3));
    geo.setIndex(idx);
    geo.computeVertexNormals();
    const mesh = new THREE.Mesh(geo, mat);
    mesh.frustumCulled = false;
    return mesh;
  }

  private initWorldHazards(hz: WorldHazard3D | null | undefined) {
    clearGroup(this.hazardGroup);
    clearGroup(this.hazardOverlayGroup);
    this.worldHazards = hz ?? null;
    this.waterMesh = null;
    this.fireGroundMesh = null;
    this.flameInst = null;
    this.smokeInst = null;
    this.fireLight = null;
    this.activeFlames = [];
    this.smokeSource = null;
    this.smokeStrength = 0;
    this.lastHazardOverlayBin = -1;
    if (!hz) return;

    if (hz.kind === "flood") {
      const mat = new THREE.MeshBasicMaterial({
        vertexColors: true,
        transparent: true,
        opacity: 0.68,
        side: THREE.DoubleSide,
        depthWrite: false,
      });
      this.waterMesh = this.makeGridSurface(hz.grid, mat);
      this.hazardGroup.add(this.waterMesh);
    } else if (hz.kind === "fire") {
      this.fireWindRad = (hz.wind_to_deg * Math.PI) / 180;
      this.fireWindMps = hz.wind_speed_mps;
      const groundMat = new THREE.MeshBasicMaterial({
        vertexColors: true,
        transparent: true,
        opacity: 0.84,
        side: THREE.DoubleSide,
        depthWrite: false,
      });
      this.fireGroundMesh = this.makeGridSurface(hz.grid, groundMat);
      this.hazardGroup.add(this.fireGroundMesh);

      // 3D animated flames along the active fire front
      const coneGeo = new THREE.ConeGeometry(1.15, 3.8, 6);
      coneGeo.translate(0, 1.9, 0);
      const flameMat = new THREE.MeshBasicMaterial({
        color: "#ffffff",
        transparent: true,
        opacity: 0.92,
        depthWrite: false,
        blending: THREE.AdditiveBlending,
      });
      this.flameInst = new THREE.InstancedMesh(coneGeo, flameMat, FLAME_COUNT);
      this.flameInst.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
      this.flameInst.frustumCulled = false;
      for (let i = 0; i < FLAME_COUNT; i++) {
        this.dummy.position.set(0, -20, 0);
        this.dummy.scale.setScalar(0.001);
        this.dummy.updateMatrix();
        this.flameInst.setMatrixAt(i, this.dummy.matrix);
        this.tmpColor.setHSL(0.05 + (i % 7) * 0.014, 1.0, 0.56);
        this.flameInst.setColorAt(i, this.tmpColor);
      }
      this.flameInst.instanceMatrix.needsUpdate = true;
      if (this.flameInst.instanceColor) this.flameInst.instanceColor.needsUpdate = true;
      this.hazardGroup.add(this.flameInst);

      // Warm fire glow light
      this.fireLight = new THREE.PointLight("#ff7020", 0, 110, 1.4);
      this.fireLight.position.set(-45, 8, 45);
      this.hazardGroup.add(this.fireLight);

      // 3D volumetric smoke plume drifting downwind (unlit grey ash so grass hemisphere light does not tint it green)
      const puffGeo = new THREE.IcosahedronGeometry(1.0, 1);
      const smokeMat = new THREE.MeshBasicMaterial({
        color: "#ffffff",
        transparent: true,
        opacity: 0.26,
        depthWrite: false,
      });
      this.smokeInst = new THREE.InstancedMesh(puffGeo, smokeMat, SMOKE_COUNT);
      this.smokeInst.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
      this.smokeInst.frustumCulled = false;
      for (let i = 0; i < SMOKE_COUNT; i++) {
        this.dummy.position.set(0, -30, 0);
        this.dummy.scale.setScalar(0.001);
        this.dummy.updateMatrix();
        this.smokeInst.setMatrixAt(i, this.dummy.matrix);
        const shade = 0.48 + (i / SMOKE_COUNT) * 0.34;
        this.tmpColor.setRGB(shade, shade + 0.01, shade + 0.025);
        this.smokeInst.setColorAt(i, this.tmpColor);
      }
      this.smokeInst.instanceMatrix.needsUpdate = true;
      if (this.smokeInst.instanceColor) this.smokeInst.instanceColor.needsUpdate = true;
      this.hazardGroup.add(this.smokeInst);
    }
  }

  setWorld(w: World3D) {
    clearGroup(this.staticGroup);
    this.boxMeshes.clear();
    const [x0, x1, y0, y1] = w.bounds;
    const span = Math.max(x1 - x0, y1 - y0);
    // scale fog and far plane to fit large worlds (flood 500 m, fire 200 m, SAR 1500 m)
    if (this.scene.fog instanceof THREE.Fog) {
      this.scene.fog.near = span > 80 ? Math.max(140, span * 0.5) : 90;
      this.scene.fog.far = span > 80 ? Math.max(380, span * 1.4) : 220;
    }
    this.camera.far = span > 80 ? Math.max(900, span * 2.2) : 600;
    this.camera.updateProjectionMatrix();

    // ground: the simulator's synthetic orthophoto, plus a plain apron around it
    const apronSize = Math.max(600, span * 1.8);
    const apron = new THREE.Mesh(new THREE.PlaneGeometry(apronSize, apronSize), new THREE.MeshLambertMaterial({ color: "#36502f" }));
    apron.rotation.x = -Math.PI / 2;
    apron.position.y = -0.03;
    apron.receiveShadow = true;
    this.staticGroup.add(apron);
    const tex = new THREE.TextureLoader().load(w.ortho);
    tex.colorSpace = THREE.SRGBColorSpace;
    tex.anisotropy = 8;
    const ground = new THREE.Mesh(new THREE.PlaneGeometry(x1 - x0, y1 - y0), new THREE.MeshLambertMaterial({ map: tex }));
    ground.rotation.x = -Math.PI / 2;
    ground.position.copy(v3((x0 + x1) / 2, (y0 + y1) / 2, 0));
    ground.receiveShadow = true;
    this.staticGroup.add(ground);

    for (const t of w.trees) {
      const g = new THREE.Group();
      const trunk = new THREE.Mesh(new THREE.CylinderGeometry(t.trunk_r * 0.8, t.trunk_r, t.trunk_h, 10), new THREE.MeshLambertMaterial({ color: "#4e463d" }));
      trunk.position.y = t.trunk_h / 2;
      trunk.castShadow = true;
      g.add(trunk);
      const mat = new THREE.MeshLambertMaterial({ color: t.color });
      for (const [dx, dz, dy, r] of [[0, 0, 0, 0.8], [0.35, 0.2, 0.15, 0.62], [-0.3, 0.25, 0.1, 0.62], [0.05, -0.38, 0.2, 0.62]]) {
        const s = new THREE.Mesh(new THREE.SphereGeometry(t.canopy_r * r, 18, 14), mat);
        s.position.set(dx * t.canopy_r, t.canopy_z + dy * t.canopy_r, dz * t.canopy_r);
        s.castShadow = true;
        g.add(s);
      }
      g.position.copy(v3(t.xy[0], t.xy[1], 0));
      this.staticGroup.add(g);
    }
    for (const b of w.boxes) {
      const [L, W, H] = b.size;
      const m = new THREE.Mesh(new THREE.BoxGeometry(W, H, L), new THREE.MeshLambertMaterial({ color: b.color }));
      m.position.copy(v3(b.center[0], b.center[1], b.z0 + H / 2));
      m.rotation.y = -(b.heading * Math.PI) / 180;
      m.castShadow = true;
      m.receiveShadow = true;
      this.staticGroup.add(m);
      this.boxMeshes.set(b.id, m);
      if (b.kind === "shelter") {
        const l = label("shelter", "s3d-lbl s3d-lbl-static");
        l.position.set(0, H / 2 + 0.6, 0);
        m.add(l);
      }
      // doors, windows, garage doors and stairs on building faces
      for (const op of b.openings ?? []) {
        const oh = Math.max(0.2, op.z1 - op.z0);
        const [nx, ny] = op.normal;
        const decal = new THREE.Mesh(
          new THREE.PlaneGeometry(op.width, oh),
          new THREE.MeshLambertMaterial({ color: op.color, side: THREE.DoubleSide }),
        );
        decal.position.copy(v3(op.center[0] + nx * 0.05, op.center[1] + ny * 0.05, op.z0 + oh / 2));
        decal.lookAt(v3(op.center[0] + nx * 1.0, op.center[1] + ny * 1.0, op.z0 + oh / 2));
        this.staticGroup.add(decal);
      }
    }
    for (const z of w.zones) {
      const pts = [...z.polygon, z.polygon[0]].map(([x, y]) => v3(x, y, 0.05));
      const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), new THREE.LineDashedMaterial({ color: "#c3c2b7", dashSize: 0.8, gapSize: 0.5 }));
      line.computeLineDistances();
      this.staticGroup.add(line);
    }
    // geofence: a faint wall the safety supervisor will not let the drone cross
    const gf = w.geofence;
    for (let i = 0; i < gf.length; i++) {
      const a = gf[i];
      const b = gf[(i + 1) % gf.length];
      const len = Math.hypot(b[0] - a[0], b[1] - a[1]);
      const wall = new THREE.Mesh(
        new THREE.PlaneGeometry(len, 30),
        new THREE.MeshBasicMaterial({ color: "#d03b3b", transparent: true, opacity: 0.07, side: THREE.DoubleSide, depthWrite: false }),
      );
      wall.position.copy(v3((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, 15));
      wall.rotation.y = Math.atan2(-(b[1] - a[1]), b[0] - a[0]);
      this.staticGroup.add(wall);
      for (const hgt of [0.1, 30]) {
        this.staticGroup.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints([v3(a[0], a[1], hgt), v3(b[0], b[1], hgt)]), new THREE.LineBasicMaterial({ color: "#d03b3b", transparent: true, opacity: 0.5 })));
      }
    }
    this.initWorldHazards(w.hazards);
    if (this.mode === "overview") this.setMode("overview");
    // reset dynamic history for the new scenario
    this.trailN = 0;
    this.trail.geometry.setDrawRange(0, 0);
    for (const p of this.pins.values()) clearGroup(p);
    for (const p of this.pins.values()) this.scene.remove(p);
    this.pins.clear();
  }

  private updateHazards(f: Frame3D) {
    const hz = f.hazards;
    const whz = this.worldHazards;
    if (!hz || !whz) return;

    const bin = Math.round(f.t_s * 2);
    const rebuildOverlays = bin !== this.lastHazardOverlayBin;
    if (rebuildOverlays) this.lastHazardOverlayBin = bin;

    if (hz.kind === "flood" && whz.kind === "flood" && this.waterMesh) {
      const { x0, y0, nx, ny, res_m } = whz.grid;
      const gnd = whz.ground_m;
      const geo = this.waterMesh.geometry;
      const posAttr = geo.getAttribute("position") as THREE.BufferAttribute;
      const colAttr = geo.getAttribute("color") as THREE.BufferAttribute;
      const pos = posAttr.array as Float32Array;
      const col = colAttr.array as Float32Array;
      const lvl = hz.level_m;
      const closedLim = hz.road_closed_depth_m;
      for (let r = 0; r < ny; r++) {
        const wy = y0 + (r + 0.5) * res_m;
        for (let c = 0; c < nx; c++) {
          const idx = r * nx + c;
          const i = idx * 3;
          const depth = lvl - gnd[idx];
          if (depth > 0.01) {
            const wx = x0 + (c + 0.5) * res_m;
            const wave = 0.025 * Math.sin(f.t_s * 2.4 + wx * 0.14 + wy * 0.17);
            pos[i + 1] = Math.min(2.4, 0.08 + depth * 1.15) + wave;
            if (depth >= closedLim) {
              // Deep water (> 0.3 m road closure depth): rich deep flood blue
              const k = Math.min(1, (depth - closedLim) / 0.65);
              col[i] = 0.11 * (1 - k) + 0.05 * k;
              col[i + 1] = 0.42 * (1 - k) + 0.24 * k;
              col[i + 2] = 0.78 * (1 - k) + 0.56 * k;
            } else {
              // Shallow water (0.01 - 0.3 m): brighter cyan-blue
              const k = depth / closedLim;
              col[i] = 0.32 * (1 - k) + 0.14 * k;
              col[i + 1] = 0.72 * (1 - k) + 0.48 * k;
              col[i + 2] = 0.94 * (1 - k) + 0.82 * k;
            }
          } else {
            pos[i + 1] = -0.22;
            col[i] = 0.25;
            col[i + 1] = 0.6;
            col[i + 2] = 0.85;
          }
        }
      }
      posAttr.needsUpdate = true;
      colAttr.needsUpdate = true;
      if (rebuildOverlays) {
        geo.computeVertexNormals();
        this.rebuildFloodOverlays(hz);
      }
    } else if (hz.kind === "fire" && whz.kind === "fire" && this.fireGroundMesh) {
      if (rebuildOverlays) {
        const { x0, y0, nx, ny, res_m } = whz.grid;
        const arr = whz.arrival_s;
        const geo = this.fireGroundMesh.geometry;
        const posAttr = geo.getAttribute("position") as THREE.BufferAttribute;
        const colAttr = geo.getAttribute("color") as THREE.BufferAttribute;
        const pos = posAttr.array as Float32Array;
        const col = colAttr.array as Float32Array;
        const t = f.t_s;
        const margin = hz.keep_out_margin_s;
        const candidates: [number, number, number, number][] = [];
        let sumX = 0;
        let sumY = 0;
        let countFront = 0;

        for (let r = 0; r < ny; r++) {
          const wy = y0 + (r + 0.5) * res_m;
          for (let c = 0; c < nx; c++) {
            const idx = r * nx + c;
            const i = idx * 3;
            const a = arr[idx];
            const wx = x0 + (c + 0.5) * res_m;
            if (a >= 0 && a <= t) {
              const age = t - a;
              if (age <= 7.0) {
                // Active flame front
                const k = 1 - age / 7.0;
                pos[i + 1] = 0.12 + 0.22 * k;
                col[i] = 1.0;
                col[i + 1] = 0.22 + 0.55 * k;
                col[i + 2] = 0.04 + 0.12 * k;
                candidates.push([wx, wy, 0.55 + 0.65 * k, (r * 131 + c * 17) % 97]);
                sumX += wx;
                sumY += wy;
                countFront++;
              } else {
                // Burned interior / glowing embers
                pos[i + 1] = 0.06;
                const ember = ((r * 31 + c * 53) % 10) / 10;
                col[i] = 0.22 + 0.25 * ember;
                col[i + 1] = 0.11 + 0.06 * ember;
                col[i + 2] = 0.09;
                if ((r + c) % 5 === 0) {
                  candidates.push([wx, wy, 0.32, (r * 131 + c * 17) % 97]);
                }
              }
            } else if (a > t && a <= t + margin) {
              // 2-minute keep-out warning band ahead of the fire
              const k = 1 - (a - t) / margin;
              pos[i + 1] = 0.04;
              col[i] = 0.72 + 0.18 * k;
              col[i + 1] = 0.2 + 0.08 * k;
              col[i + 2] = 0.14;
            } else {
              pos[i + 1] = -0.22;
              col[i] = 0.2;
              col[i + 1] = 0.1;
              col[i + 2] = 0.1;
            }
          }
        }
        posAttr.needsUpdate = true;
        colAttr.needsUpdate = true;

        // Pick up to FLAME_COUNT evenly spaced active flame cells
        this.activeFlames = [];
        if (candidates.length > 0) {
          const step = Math.max(1, candidates.length / FLAME_COUNT);
          for (let k = 0; k < FLAME_COUNT && Math.floor(k * step) < candidates.length; k++) {
            this.activeFlames.push(candidates[Math.floor(k * step)]);
          }
        }
        if (this.fireLight) {
          if (countFront > 0) {
            this.fireLight.position.copy(v3(sumX / countFront, sumY / countFront, 9));
            this.fireLight.intensity = Math.min(4.5, 1.2 + countFront * 0.015);
          } else {
            this.fireLight.intensity = 0;
          }
        }
        this.smokeSource = hz.smoke_source_xy;
        this.smokeStrength = hz.smoke_strength;
        this.rebuildFireOverlays(hz);
      }
    }
  }

  private rebuildFloodOverlays(hz: FrameHazardFlood3D) {
    clearGroup(this.hazardOverlayGroup);
    // Filled water surfaces + shoreline and >0.3 m deep-water contours
    for (const area of hz.areas) {
      if (area.polygon.length < 3) continue;
      const isDeep = area.kind === "deep_water";
      const z = isDeep ? 0.34 : 0.16;
      const shape = new THREE.Shape(area.polygon.map(([x, y]) => new THREE.Vector2(x, y)));
      const surf = new THREE.Mesh(
        new THREE.ShapeGeometry(shape),
        new THREE.MeshBasicMaterial({
          color: isDeep ? "#0b6bcb" : "#2da8ff",
          transparent: true,
          opacity: isDeep ? 0.56 : 0.44,
          side: THREE.DoubleSide,
          depthWrite: false,
        }),
      );
      surf.rotation.x = -Math.PI / 2;
      surf.position.y = z;
      this.hazardOverlayGroup.add(surf);

      const pts = [...area.polygon, area.polygon[0]].map(([x, y]) => v3(x, y, z + 0.04));
      const mat = isDeep
        ? new THREE.LineDashedMaterial({ color: "#ff9f1c", dashSize: 1.2, gapSize: 0.7 })
        : new THREE.LineBasicMaterial({ color: "#7ce0ff", transparent: true, opacity: 0.9 });
      const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), mat);
      if (isDeep) line.computeLineDistances();
      this.hazardOverlayGroup.add(line);
    }

    // Closed road segments (> 0.3 m water depth)
    let deepestRoad: { a: XY; b: XY; depth_m: number } | null = null;
    for (const rd of hz.closed_roads) {
      if (!deepestRoad || rd.depth_m > deepestRoad.depth_m) deepestRoad = rd;
      const pA = v3(rd.a[0], rd.a[1], 0.55);
      const pB = v3(rd.b[0], rd.b[1], 0.55);
      const line = new THREE.Line(
        new THREE.BufferGeometry().setFromPoints([pA, pB]),
        new THREE.LineBasicMaterial({ color: "#ff4d4f" }),
      );
      this.hazardOverlayGroup.add(line);
      // Barricade posts at road ends
      for (const end of [rd.a, rd.b]) {
        const post = new THREE.Mesh(
          new THREE.CylinderGeometry(0.28, 0.28, 0.9, 8),
          new THREE.MeshBasicMaterial({ color: "#ff4d4f" }),
        );
        post.position.copy(v3(end[0], end[1], 0.45));
        this.hazardOverlayGroup.add(post);
      }
    }
    if (deepestRoad) {
      const mx = (deepestRoad.a[0] + deepestRoad.b[0]) / 2;
      const my = (deepestRoad.a[1] + deepestRoad.b[1]) / 2;
      const anchor = new THREE.Group();
      anchor.position.copy(v3(mx, my, 1.5));
      anchor.add(label(`road closed · ${deepestRoad.depth_m.toFixed(2)} m water (>0.3 m)`, "s3d-lbl s3d-pin-rules"));
      this.hazardOverlayGroup.add(anchor);
    }

    // Safe crew route avoiding closed roads
    const cr = hz.crew_route;
    if (cr && cr.waypoints.length >= 2) {
      this.addCrewRoute3D(
        cr.waypoints,
        `crew route · ${cr.length_m.toFixed(0)} m (max ${cr.max_water_depth_m.toFixed(2)} m water)`,
      );
    }
  }

  private rebuildFireOverlays(hz: FrameHazardFire3D) {
    clearGroup(this.hazardOverlayGroup);
    let keepOutLabelPos: XY | null = null;
    for (const area of hz.areas) {
      if (area.polygon.length < 3) continue;
      if (area.kind === "smoke") continue; // smoke is rendered as a 3D volumetric plume
      const isKeep = area.kind === "keep_out";
      const z = isKeep ? 0.09 : 0.18;
      const shape = new THREE.Shape(area.polygon.map(([x, y]) => new THREE.Vector2(x, y)));
      const surf = new THREE.Mesh(
        new THREE.ShapeGeometry(shape),
        new THREE.MeshBasicMaterial({
          color: isKeep ? "#e03131" : "#ff4500",
          transparent: true,
          opacity: isKeep ? 0.18 : 0.55,
          side: THREE.DoubleSide,
          depthWrite: false,
        }),
      );
      surf.rotation.x = -Math.PI / 2;
      surf.position.y = z;
      this.hazardOverlayGroup.add(surf);

      const lineZ = isKeep ? 0.36 : 0.28;
      const pts = [...area.polygon, area.polygon[0]].map(([x, y]) => v3(x, y, lineZ));
      const mat = isKeep
        ? new THREE.LineDashedMaterial({ color: "#ff4d4f", dashSize: 1.4, gapSize: 0.8 })
        : new THREE.LineBasicMaterial({ color: "#ff922b" });
      const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), mat);
      if (isKeep) {
        line.computeLineDistances();
        if (!keepOutLabelPos) keepOutLabelPos = area.polygon[0];
      }
      this.hazardOverlayGroup.add(line);
    }
    if (keepOutLabelPos) {
      const anchor = new THREE.Group();
      anchor.position.copy(v3(keepOutLabelPos[0], keepOutLabelPos[1], 1.4));
      anchor.add(label("2-min keep-out zone", "s3d-lbl s3d-pin-rules"));
      this.hazardOverlayGroup.add(anchor);
    }

    // Building openings status markers (smoke / keep-out / target door)
    const targetId = hz.crew_route?.target_id;
    let smokeLabeled = false;
    for (const op of hz.openings) {
      const isTarget = op.id === targetId;
      const color = isTarget ? "#22c55e" : op.in_keep_out ? "#ff4d4f" : op.condition === "unclear" ? "#f59e0b" : "#94a3b8";
      const ring = new THREE.Mesh(
        new THREE.RingGeometry(0.45, 0.65, 24),
        new THREE.MeshBasicMaterial({ color, side: THREE.DoubleSide }),
      );
      ring.rotation.x = -Math.PI / 2;
      ring.position.copy(v3(op.xy[0], op.xy[1], 0.22));
      this.hazardOverlayGroup.add(ring);

      if (isTarget || (op.condition === "unclear" && !smokeLabeled)) {
        if (op.condition === "unclear") smokeLabeled = true;
        const g = new THREE.Group();
        g.position.copy(v3(op.xy[0], op.xy[1], 2.8));
        const txt = isTarget
          ? `Side ${op.side} ${op.kind} · crew target`
          : `Side ${op.side} openings · unclear (smoke)`;
        g.add(label(txt, `s3d-lbl ${isTarget ? "s3d-lbl-drone" : "s3d-pin-rules"}`));
        this.hazardOverlayGroup.add(g);
      }
    }

    // Safe crew walking route from staging to the nearest door outside keep-out
    const cr = hz.crew_route;
    if (cr && cr.waypoints.length >= 2) {
      this.addCrewRoute3D(
        cr.waypoints,
        `crew route → ${cr.target_id} · ${cr.length_m.toFixed(1)} m (${cr.keep_out_cells} keep-out cells)`,
      );
    }
  }

  private addCrewRoute3D(waypoints: XY[], text: string, into: THREE.Group = this.hazardOverlayGroup) {
    const pts = waypoints.map(([x, y]) => v3(x, y, 0.45));
    const curve = new THREE.CatmullRomCurve3(pts);
    const tube = new THREE.Mesh(
      new THREE.TubeGeometry(curve, Math.max(16, waypoints.length * 4), 0.24, 8, false),
      new THREE.MeshBasicMaterial({ color: "#22c55e", transparent: true, opacity: 0.9 }),
    );
    into.add(tube);
    for (const [x, y] of [waypoints[0], waypoints[waypoints.length - 1]]) {
      const node = new THREE.Mesh(
        new THREE.SphereGeometry(0.5, 12, 10),
        new THREE.MeshBasicMaterial({ color: "#22c55e" }),
      );
      node.position.copy(v3(x, y, 0.5));
      into.add(node);
    }
    const mid = waypoints[Math.floor(waypoints.length / 2)];
    const anchor = new THREE.Group();
    anchor.position.copy(v3(mid[0], mid[1], 1.8));
    anchor.add(label(text, "s3d-lbl s3d-lbl-drone"));
    into.add(anchor);
  }

  private animateHazards(nowSec: number) {
    // Animate 3D fire flames and smoke plume at 60 FPS
    if (this.flameInst) {
      const n = this.activeFlames.length;
      const wx = Math.sin(this.fireWindRad);
      const wy = Math.cos(this.fireWindRad);
      for (let i = 0; i < FLAME_COUNT; i++) {
        if (i < n) {
          const [fx, fy, intensity, seed] = this.activeFlames[i];
          const flicker = 0.72 + 0.38 * Math.sin(nowSec * 9.5 + seed) * Math.cos(nowSec * 6.3 + seed * 0.7);
          const hScale = Math.max(0.25, intensity * flicker);
          const rScale = 0.65 + 0.35 * intensity;
          const sway = 0.14 * Math.sin(nowSec * 5.0 + seed);
          this.dummy.position.copy(v3(fx + wx * sway, fy + wy * sway, 0.05));
          this.dummy.rotation.set(-wy * (0.18 + sway * 0.2), 0, wx * (0.18 + sway * 0.2));
          this.dummy.scale.set(rScale, hScale, rScale);
          this.dummy.updateMatrix();
          this.flameInst.setMatrixAt(i, this.dummy.matrix);
        } else {
          this.dummy.position.set(0, -25, 0);
          this.dummy.scale.setScalar(0.001);
          this.dummy.updateMatrix();
          this.flameInst.setMatrixAt(i, this.dummy.matrix);
        }
      }
      this.flameInst.instanceMatrix.needsUpdate = true;
    }

    if (this.smokeInst) {
      if (this.smokeSource && this.smokeStrength > 0.02) {
        const [sx, sy] = this.smokeSource;
        const wx = Math.sin(this.fireWindRad);
        const wy = Math.cos(this.fireWindRad);
        const px = -wy;
        const py = wx;
        const speed = Math.max(2.0, this.fireWindMps * 3.2);
        const maxAlong = 135;
        for (let i = 0; i < SMOKE_COUNT; i++) {
          const seed = i * 1.6180339;
          const phase = ((nowSec * speed + i * (maxAlong / SMOKE_COUNT)) % maxAlong) / maxAlong; // 0..1 downwind
          const along = phase * maxAlong - 4.0;
          const spread = Math.max(3.2, Math.abs(along) * Math.tan((18 * Math.PI) / 180));
          // Deterministic pseudo-Gaussian lateral offset per puff
          const lat = (Math.sin(seed * 17.3) * 0.65 + Math.cos(seed * 31.7 + nowSec * 0.4) * 0.35) * spread;
          const wxPuff = sx + wx * along + px * lat;
          const wyPuff = sy + wy * along + py * lat;
          const wzPuff = 1.6 + phase * 24.0 + 1.2 * Math.sin(nowSec * 1.4 + seed);
          const radius = (1.6 + phase * 6.8) * (0.65 + 0.45 * this.smokeStrength);
          this.dummy.position.copy(v3(wxPuff, wyPuff, wzPuff));
          this.dummy.rotation.set(nowSec * 0.25 + seed, nowSec * 0.35 + seed, 0);
          this.dummy.scale.set(radius * 1.15, radius * 0.72, radius * 1.15);
          this.dummy.updateMatrix();
          this.smokeInst.setMatrixAt(i, this.dummy.matrix);
        }
      } else {
        for (let i = 0; i < SMOKE_COUNT; i++) {
          this.dummy.position.set(0, -35, 0);
          this.dummy.scale.setScalar(0.001);
          this.dummy.updateMatrix();
          this.smokeInst.setMatrixAt(i, this.dummy.matrix);
        }
      }
      this.smokeInst.instanceMatrix.needsUpdate = true;
    }
  }

  private person(id: string, a: Frame3D["actors"][number]): THREE.Group {
    let g = this.people.get(id);
    if (g) return g;
    g = new THREE.Group();
    const h = a.height;
    const s = h / 1.75;
    const skin = new THREE.MeshLambertMaterial({ color: a.skin ?? "#c8966e" });
    const shirt = new THREE.MeshLambertMaterial({ color: a.vest ? "#d9e021" : a.shirt });
    const top = a.jacket ? new THREE.MeshLambertMaterial({ color: a.jacket }) : shirt;
    const pants = new THREE.MeshLambertMaterial({ color: a.pants });
    const shoe = new THREE.MeshLambertMaterial({ color: "#26262a" });
    const limb = (r0: number, r1: number, len: number, mat: THREE.Material) => {
      const m = new THREE.Mesh(new THREE.CylinderGeometry(r0, r1, len, 8), mat);
      m.castShadow = true;
      return m;
    };
    // legs: thigh and shin hinge at the hip and knee (animated in update)
    for (const side of [-1, 1]) {
      const hip = new THREE.Group();
      hip.name = `hip${side}`;
      hip.position.set(0.1 * s * side, 0.92 * s, 0);
      const thigh = limb(0.085 * s, 0.065 * s, 0.42 * s, pants);
      thigh.position.y = -0.21 * s;
      const knee = new THREE.Group();
      knee.name = "knee";
      knee.position.y = -0.42 * s;
      const shin = limb(0.062 * s, 0.045 * s, 0.42 * s, a.trousers === "shorts" ? skin : pants);
      shin.position.y = -0.21 * s;
      const foot = new THREE.Mesh(new THREE.BoxGeometry(0.11 * s, 0.07 * s, 0.24 * s), shoe);
      foot.position.set(0, -0.45 * s, 0.05 * s);
      knee.add(shin, foot);
      hip.add(thigh, knee);
      g.add(hip);
    }
    // torso: hips to chest, a wider chest to the shoulders, an open jacket's shirt strip, a backpack
    const lower = limb(0.2 * s, 0.17 * s, 0.4 * s, a.jacket ? top : shirt);
    lower.position.y = 1.12 * s;
    const upper = limb(0.23 * s, 0.2 * s, 0.32 * s, top);
    upper.position.y = 1.28 * s;
    lower.scale.z = upper.scale.z = 0.62;
    g.add(lower, upper);
    if (a.jacket) {
      const strip = new THREE.Mesh(new THREE.BoxGeometry(0.1 * s, 0.5 * s, 0.04 * s), shirt);
      strip.position.set(0, 1.18 * s, 0.11 * s);
      g.add(strip);
    }
    if (a.backpack) {
      const pack = new THREE.Mesh(new THREE.BoxGeometry(0.3 * s, 0.42 * s, 0.15 * s), new THREE.MeshLambertMaterial({ color: a.backpack }));
      pack.position.set(0, 1.23 * s, -0.19 * s);
      pack.castShadow = true;
      g.add(pack);
    }
    // arms: upper arm and forearm hinge at the shoulder and elbow
    for (const side of [-1, 1]) {
      const sh = new THREE.Group();
      sh.name = `shoulder${side}`;
      sh.position.set(0.25 * s * side, 1.4 * s, 0);
      const upperArm = limb(0.055 * s, 0.045 * s, 0.3 * s, top);
      upperArm.position.y = -0.15 * s;
      const elbow = new THREE.Group();
      elbow.name = "elbow";
      elbow.position.y = -0.3 * s;
      const fore = limb(0.045 * s, 0.035 * s, 0.27 * s, a.sleeves === "short" ? skin : shirt);
      fore.position.y = -0.135 * s;
      const hand = new THREE.Mesh(new THREE.SphereGeometry(0.045 * s, 8, 8), skin);
      hand.position.y = -0.3 * s;
      elbow.add(fore, hand);
      sh.add(upperArm, elbow);
      g.add(sh);
    }
    // neck, head, hair, hat
    const neck = limb(0.06 * s, 0.065 * s, 0.1 * s, skin);
    neck.position.y = 1.49 * s;
    const head = new THREE.Mesh(new THREE.SphereGeometry(0.105 * s, 14, 12), skin);
    head.position.y = 1.63 * s;
    head.castShadow = true;
    const hair = new THREE.Mesh(new THREE.SphereGeometry(0.1 * s, 12, 10, 0, Math.PI * 2, 0, Math.PI * 0.55), new THREE.MeshLambertMaterial({ color: a.hair ?? "#1e2026" }));
    hair.position.set(0, 1.655 * s, -0.012 * s);
    g.add(neck, head, hair);
    if (a.hat) {
      const hatMat = new THREE.MeshLambertMaterial({ color: a.hat });
      const brim = new THREE.Mesh(new THREE.CylinderGeometry(0.17 * s, 0.17 * s, 0.015 * s, 16), hatMat);
      brim.position.y = 1.72 * s;
      const crown = new THREE.Mesh(new THREE.CylinderGeometry(0.09 * s, 0.1 * s, 0.1 * s, 12), hatMat);
      crown.position.y = 1.77 * s;
      g.add(brim, crown);
    }
    if (a.role !== "bystander") {
      const ring = new THREE.Mesh(
        new THREE.RingGeometry(0.55, 0.7, 36),
        new THREE.MeshBasicMaterial({ color: a.role === "subject" ? "#d95926" : "#3987e5", side: THREE.DoubleSide }),
      );
      ring.rotation.x = -Math.PI / 2;
      ring.position.y = 0.05;
      g.add(ring);
    }
    const l = label(a.role === "subject" ? "subject (true position)" : a.role, `s3d-lbl s3d-lbl-${a.role}`);
    l.position.set(0, h + 0.45, 0);
    l.name = "label";
    g.add(l);
    this.scene.add(g);
    this.people.set(id, g);
    return g;
  }

  /** Swing the limbs with the walking phase the simulator reports. */
  private animatePerson(g: THREE.Group, a: Frame3D["actors"][number]) {
    const phase = a.phase ?? 0;
    const amp = Math.min(0.75, 0.2 + 0.11 * (a.speed ?? 0));
    const sw = Math.sin(phase) * amp;
    for (const side of [-1, 1]) {
      const hip = g.getObjectByName(`hip${side}`);
      const sh = g.getObjectByName(`shoulder${side}`);
      if (hip) {
        hip.rotation.x = -sw * side;
        const knee = hip.getObjectByName("knee");
        if (knee) knee.rotation.x = Math.max(0, sw * side) * 0.9;
      }
      if (sh) {
        sh.rotation.x = sw * side * 0.8;
        const elbow = sh.getObjectByName("elbow");
        if (elbow) elbow.rotation.x = -Math.max(0, -sw * side) * 0.6 - 0.2;
      }
    }
  }

  update(f: Frame3D) {
    const resetPose = !this.frame || f.t_s < this.lastT - 0.5;
    this.frame = f;
    this.spinProps(f.drone.pos[2] > 0.4);
    // every rescue beacon: a ring and label, and its assessed crew route (amber; the one the crew is walking is
    // drawn green by the crew-route block below)
    const beacons = f.beacons ?? (f.beacon ? [{ id: "B1", n: 1, xy: f.beacon.xy, status: f.beacon.status ?? "pending", route: [], route_ok: false, walking: false, label: "" }] : []);
    const beaconKey = beacons.map((b) => `${b.id}|${b.status}|${b.xy}|${b.walking}|${b.label}|${b.route.length}`).join(";");
    if (beaconKey !== this.beaconKey) {
      this.beaconKey = beaconKey;
      if (this.beaconMark) { clearGroup(this.beaconMark); this.scene.remove(this.beaconMark); this.beaconMark = null; }
      const all = new THREE.Group();
      for (const b of beacons) {
        if (b.status === "rejected") continue;
        const group = new THREE.Group();
        const color = b.status === "reached" ? "#9ef0bf" : b.status === "confirmed" ? "#8fe0c0" : "#ffe196";
        const ring = new THREE.Mesh(new THREE.RingGeometry(.7, 1, 32), new THREE.MeshBasicMaterial({ color, side: THREE.DoubleSide }));
        ring.rotation.x = -Math.PI / 2; group.add(ring);
        const text = label(`Beacon ${b.n} · ${b.status}`, "s3d-lbl");
        text.position.y = 1.8; group.add(text);
        group.position.copy(v3(b.xy[0], b.xy[1], .2));
        all.add(group);
        if (b.route_ok && b.route.length > 1 && !b.walking) {
          const pts = b.route.map(([x, y]) => v3(x, y, 0.35));
          const tube = new THREE.Mesh(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(pts), Math.max(16, pts.length * 2), 0.16, 6, false),
            new THREE.MeshBasicMaterial({ color: "#ffb703", transparent: true, opacity: 0.7 }));
          all.add(tube);
          const mid = b.route[Math.floor(b.route.length / 2)];
          const anchor = new THREE.Group();
          anchor.position.copy(v3(mid[0], mid[1], 1.4));
          anchor.add(label(`route ${b.label}`, "s3d-lbl s3d-lbl-static"));
          all.add(anchor);
        } else if (!b.route_ok && b.label) {
          const anchor = new THREE.Group();
          anchor.position.copy(v3(b.xy[0], b.xy[1], 3.2));
          anchor.add(label(b.label, "s3d-lbl s3d-lbl-warn"));
          all.add(anchor);
        }
      }
      this.scene.add(all); this.beaconMark = all;
    }
    if (f.t_s < this.lastT - 0.5) {
      // scenario restarted
      this.trailN = 0;
      this.lastHazardOverlayBin = -1;
      for (const p of this.pins.values()) clearGroup(p);
      for (const p of this.pins.values()) this.scene.remove(p);
      this.pins.clear();
    }
    this.lastT = f.t_s;
    const d = f.drone;
    if (resetPose) this.drone.position.copy(v3(d.pos[0], d.pos[1], d.pos[2]));
    const [qw, qx, qy, qz] = d.quat;
    if (resetPose) this.droneBody.quaternion.copy(C_ENU).multiply(new THREE.Quaternion(qx, qy, qz, qw));
    const dot = this.scene.getObjectByName("ground-dot");
    if (dot) dot.position.copy(v3(d.pos[0], d.pos[1], 0.06));
    this.sun.target.position.copy(v3(d.pos[0], d.pos[1], 0));
    this.sun.position.copy(v3(d.pos[0] - 30, d.pos[1] - 25, 60));

    this.updateHazards(f);

    // footprint fill + rays from the camera to its corners
    if (f.footprint && f.footprint.length >= 3) {
      const pts = f.footprint.map(([x, y]) => v3(x, y, 0.08));
      const shapeIdx: number[] = [];
      for (let i = 1; i < pts.length - 1; i++) shapeIdx.push(0, i, i + 1);
      const g = new THREE.BufferGeometry().setFromPoints(pts);
      g.setIndex(shapeIdx);
      this.footFill.geometry.dispose();
      this.footFill.geometry = g;
      const cam = v3(d.pos[0], d.pos[1], d.pos[2]);
      const n = f.footprint.length;
      const corners = [0, Math.round(n / 4), Math.round(n / 2), Math.round((3 * n) / 4)].map((i) => pts[i % n]);
      const segs: THREE.Vector3[] = [];
      for (const c of corners) segs.push(cam, c);
      this.footRays.geometry.dispose();
      this.footRays.geometry = new THREE.BufferGeometry().setFromPoints(segs);
      this.footFill.visible = this.footRays.visible = true;
    } else {
      this.footFill.visible = this.footRays.visible = false;
    }

    // trail
    if (this.trailN < MAX_TRAIL) {
      const i = this.trailN * 3;
      const p = v3(d.pos[0], d.pos[1], d.pos[2]);
      this.trailPos[i] = p.x;
      this.trailPos[i + 1] = p.y;
      this.trailPos[i + 2] = p.z;
      const c = new THREE.Color(TRAIL[trailKind(f)]);
      this.trailCol[i] = c.r;
      this.trailCol[i + 1] = c.g;
      this.trailCol[i + 2] = c.b;
      this.trailN++;
      const g = this.trail.geometry;
      g.setDrawRange(0, this.trailN);
      (g.attributes.position as THREE.BufferAttribute).needsUpdate = true;
      (g.attributes.color as THREE.BufferAttribute).needsUpdate = true;
    }

    // people (ground truth)
    const seen = new Set<string>();
    for (const a of f.actors) {
      const g = this.person(a.id, a);
      g.visible = true;
      g.position.copy(v3(a.xy[0], a.xy[1], 0));
      g.rotation.y = -(a.heading * Math.PI) / 180;
      this.animatePerson(g, a);
      const l = g.getObjectByName("label") as CSS2DObject | undefined;
      if (l && a.role === "subject") l.element.textContent = a.visible < 0.3 ? "subject (hidden from drone)" : "subject (true position)";
      seen.add(a.id);
    }
    for (const [id, g] of this.people) if (!seen.has(id)) g.visible = false;

    // the callout: responder to target, with the latest words
    const s = f.subject;
    const rxy = f.responder?.xy ?? null;
    if (rxy && s.est_xy && s.state !== "UNLOCKED") {
      const a = v3(rxy[0], rxy[1], 1.2);
      const b = v3(s.est_xy[0], s.est_xy[1], 1.2);
      this.calloutLine.geometry.dispose();
      this.calloutLine.geometry = new THREE.BufferGeometry().setFromPoints([a, b]);
      this.calloutLine.computeLineDistances();
      this.calloutLine.visible = true;
      this.calloutLabel.position.copy(a.clone().add(b).multiplyScalar(0.5).add(v3(0, 0, 2.5)));
      this.calloutLabel.element.textContent = f.callout ?? "";
      this.calloutLabel.visible = !!f.callout;
    } else {
      this.calloutLine.visible = false;
      this.calloutLabel.visible = false;
    }
    this.subjEst.visible = !!s.est_xy && s.state !== "UNLOCKED";
    if (s.est_xy) this.subjEst.position.copy(v3(s.est_xy[0], s.est_xy[1], 0.07));
    const lost = s.state !== "LOCKED" && s.state !== "UNLOCKED" && !!s.last_seen_xy;
    this.lastSeen.visible = lost;
    this.searchRing.visible = lost && !!s.search_radius;
    if (lost && s.last_seen_xy) {
      this.lastSeen.position.copy(v3(s.last_seen_xy[0], s.last_seen_xy[1], 0));
      if (s.search_radius) {
        const pts: THREE.Vector3[] = [];
        for (let k = 0; k <= 64; k++) {
          const a = (k / 64) * Math.PI * 2;
          pts.push(v3(s.last_seen_xy[0] + s.search_radius * Math.cos(a), s.last_seen_xy[1] + s.search_radius * Math.sin(a), 0.07));
        }
        this.searchRing.geometry.dispose();
        this.searchRing.geometry = new THREE.BufferGeometry().setFromPoints(pts);
        this.searchRing.computeLineDistances();
      }
    }

    this.updateBelief(f);
    this.updateFocus(f);
    // the rescue crew's way in: assessed (A* on the hazard grid) the moment a beacon is placed, redrawn as it is
    // replanned; a blocked assessment is said at the beacon instead
    const crew = f.crew_route;
    const routeKey = crew ? `${crew.ok}|${crew.label}|${crew.waypoints.map(([x, y]) => `${x},${y}`).join(";")}` : "";
    if (routeKey !== this.crewRouteKey) {
      this.crewRouteKey = routeKey;
      clearGroup(this.crewRouteGroup);
      if (crew?.ok && crew.waypoints.length > 1) {
        this.addCrewRoute3D(crew.waypoints, crew.label, this.crewRouteGroup);
      } else if (crew && !crew.ok && f.beacon && f.beacon.status !== "rejected") {
        const anchor = new THREE.Group();
        anchor.position.copy(v3(f.beacon.xy[0], f.beacon.xy[1], 3.2));
        anchor.add(label(`crew route: ${crew.label}`, "s3d-lbl s3d-lbl-warn"));
        this.crewRouteGroup.add(anchor);
      }
    }

    // decision pins where each D1 answer reached the drone
    for (const dec of f.decisions) {
      if (!dec.at || this.pins.has(dec.id)) continue;
      const g = new THREE.Group();
      const [x, y, z] = dec.at;
      const col = decisionColor(dec);
      g.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints([v3(x, y, 0), v3(x, y, z)]), new THREE.LineBasicMaterial({ color: col, transparent: true, opacity: 0.7 })));
      const ball = new THREE.Mesh(new THREE.SphereGeometry(0.14, 12, 10), new THREE.MeshBasicMaterial({ color: col }));
      ball.position.copy(v3(x, y, z));
      g.add(ball);
      const name = (dec.selected ?? "?").replaceAll("_", " ");
      const byModel = dec.acted && dec.engine !== "rules";
      const l = label(`${name}${byModel ? "" : dec.engine === "rules" ? " (rules)" : " (below gate)"}`, `s3d-lbl s3d-pin ${byModel ? "s3d-pin-ai" : "s3d-pin-rules"}`);
      l.position.copy(v3(x, y, z + 1.0));
      g.add(l);
      l.name = "pin-label";
      this.scene.add(g);
      this.pins.set(dec.id, g);
    }
    // label only the latest few pins; older ones stay as coloured markers
    const ids = [...this.pins.keys()];
    ids.forEach((id, i) => {
      const lab = this.pins.get(id)?.getObjectByName("pin-label");
      if (lab) lab.visible = i >= ids.length - 3;
    });
  }

  private updateFocus(f: Frame3D) {
    const e = this.focus;
    let at: [number, number] | null = null;
    if (e?.startsWith("actor:")) {
      const a = f.actors.find((x) => x.id === e.slice(6));
      if (a) at = a.xy;
    } else if (e?.startsWith("box:")) {
      const pre = e.slice(4);
      const ms = [...this.boxMeshes].filter(([id]) => id.startsWith(pre)).map(([, m]) => m.position);
      if (ms.length) at = [ms.reduce((s, p) => s + p.x, 0) / ms.length, -ms.reduce((s, p) => s + p.z, 0) / ms.length];
    } else if (e === "route" && f.pursuit) {
      const c = f.pursuit.candidates.find((x) => x.chosen);
      if (c) at = c.xy;
    }
    this.focusRing.visible = !!at;
    if (at) this.focusRing.position.copy(v3(at[0], at[1], 0.2));
  }

  private updateBelief(f: Frame3D) {
    const b = f.pursuit;
    const on = !!b && b.particles.length > 0;
    this.belief.visible = this.beliefRoute.visible = this.beliefFoot.visible = this.beliefMarks.visible = on;
    if (!b || !on) {
      if (this.beliefSig) {
        this.beliefSig = "";
        for (const o of [...this.beliefMarks.children]) this.beliefMarks.remove(o);
      }
      return;
    }
    const n = b.particles.length;
    const pos = new Float32Array(n * 3);
    const col = new Float32Array(n * 3);
    const c = new THREE.Color();
    const focusHyp = this.focus?.startsWith("hypothesis:") ? b.hypotheses.findIndex((h) => h.id === this.focus!.slice(11)) : -1;
    b.particles.forEach(([x, y, k], i) => {
      const p = v3(x, y, 0.35);
      pos.set([p.x, p.y, p.z], i * 3);
      c.set(HYP_HEX[k % HYP_HEX.length]);
      if (focusHyp >= 0 && k !== focusHyp) c.multiplyScalar(0.3); // the story's hypothesis stands out
      col.set([c.r, c.g, c.b], i * 3);
    });
    const g = this.belief.geometry;
    g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    g.setAttribute("color", new THREE.BufferAttribute(col, 3));
    g.computeBoundingSphere();
    if (b.route.length) {
      const pts = [v3(f.drone.pos[0], f.drone.pos[1], f.drone.pos[2]), ...b.route.map(([x, y]) => v3(x, y, f.drone.pos[2]))];
      this.beliefRoute.geometry.dispose();
      this.beliefRoute.geometry = new THREE.BufferGeometry().setFromPoints(pts);
      this.beliefRoute.computeLineDistances();
    }
    this.beliefRoute.visible = b.route.length > 0;
    if (b.footprint && b.footprint.length >= 3) {
      const pts = b.footprint.map(([x, y]) => v3(x, y, 0.12));
      const idx: number[] = [];
      for (let i = 1; i < pts.length - 1; i++) idx.push(0, i, i + 1);
      const fg = new THREE.BufferGeometry().setFromPoints(pts);
      fg.setIndex(idx);
      this.beliefFoot.geometry.dispose();
      this.beliefFoot.geometry = fg;
    }
    this.beliefFoot.visible = !!b.footprint;
    (this.beliefFoot.material as THREE.MeshBasicMaterial).opacity = this.focus === "route" ? 0.28 : 0.12;
    // candidate viewpoints: rebuilt only when they change (labels sit directly in the group so removing them also
    // removes their DOM elements)
    const sig = b.candidates.map((cd) => `${cd.id}:${cd.chosen}:${cd.unsafe ? 1 : 0}:${Math.round(100 * cd.coverage)}:${cd.xy[0].toFixed(0)},${cd.xy[1].toFixed(0)}`).join("|");
    if (sig !== this.beliefSig) {
      this.beliefSig = sig;
      for (const o of [...this.beliefMarks.children]) {
        this.beliefMarks.remove(o);
        const m = o as THREE.Mesh;
        if (m.isMesh) {
          m.geometry.dispose();
          (m.material as THREE.Material).dispose();
        }
      }
      for (const cd of b.candidates) {
        const ring = new THREE.Mesh(
          new THREE.RingGeometry(cd.chosen ? 1.4 : 1.0, cd.chosen ? 1.75 : 1.2, 32),
          new THREE.MeshBasicMaterial({ color: cd.unsafe ? "#d03b3b" : cd.chosen ? "#5ad2e6" : "#c3c2b7", side: THREE.DoubleSide, transparent: true, opacity: 0.9 }),
        );
        ring.rotation.x = -Math.PI / 2;
        ring.position.copy(v3(cd.xy[0], cd.xy[1], 0.15));
        const l = label(`${cd.id} ${Math.round(100 * cd.coverage)}%`, `s3d-lbl ${cd.chosen ? "s3d-pin s3d-pin-ai" : "s3d-lbl-static"}`);
        l.position.copy(v3(cd.xy[0], cd.xy[1], 1.4));
        this.beliefMarks.add(ring, l);
      }
    }
  }

  private loop = () => {
    this.raf = requestAnimationFrame(this.loop);
    const now = performance.now();
    if (now - this.animationAt < 1000 / 30 - 1) return;
    const dt = Math.min(.1, (now - this.animationAt) / 1000);
    this.animationAt = now;
    if (this.focusRing.visible) this.focusRing.scale.setScalar(1 + 0.25 * Math.sin(performance.now() / 260));
    const f = this.frame;
    this.animateHazards(performance.now() / 1000);
    if (f) {
      const d = f.drone;
      const blend = 1 - Math.exp(-18 * dt);
      this.drone.position.lerp(v3(d.pos[0], d.pos[1], d.pos[2]), blend);
      const [qw, qx, qy, qz] = d.quat;
      this.droneBody.quaternion.slerp(C_ENU.clone().multiply(new THREE.Quaternion(qx, qy, qz, qw)), blend);
      const p = this.drone.position;
      // the chase camera turns with a smoothed heading so fast yaw scans do not whip the view around
      if (this.camHeading === null) this.camHeading = d.heading;
      const dh = ((d.heading - this.camHeading + 540) % 360) - 180;
      this.camHeading = (this.camHeading + dh * 0.025 + 360) % 360;
      const h = (this.camHeading * Math.PI) / 180;
      const fwd = new THREE.Vector3(Math.sin(h), 0, -Math.cos(h));
      if (this.mode === "chase") {
        const want = p.clone().add(fwd.clone().multiplyScalar(-11)).add(new THREE.Vector3(0, 5, 0));
        this.camera.position.lerp(want, 0.06);
        this.target.lerp(p.clone().add(fwd.clone().multiplyScalar(9)).setY(Math.max(0, p.y - 9)), 0.08);
        this.camera.lookAt(this.target);
      } else if (this.mode === "top") {
        const topY = this.worldHazards ? Math.max(115, p.y * 3.4) : 85;
        const want = new THREE.Vector3(p.x, topY, p.z + 0.01);
        this.camera.position.lerp(want, 0.05);
        this.target.lerp(new THREE.Vector3(p.x, 0, p.z), 0.08);
        this.camera.lookAt(this.target);
      } else {
        this.controls.update();
      }
    }
    this.renderer.render(this.scene, this.camera);
    this.labels.render(this.scene, this.camera);
  };
}
