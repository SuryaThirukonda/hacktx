import { Box, Crosshair, FileUp, Home, Move3d, Tag } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { OBJLoader } from "three/examples/jsm/loaders/OBJLoader.js";
import { PLYLoader } from "three/examples/jsm/loaders/PLYLoader.js";
import { CSS2DObject, CSS2DRenderer } from "three/examples/jsm/renderers/CSS2DRenderer.js";
import { api, withToken } from "../../lib/api";
import { useIncident } from "../../lib/incident";
import { useStore } from "../../lib/store";
import { Badge, Btn } from "../ui";
import { EvidenceModal } from "./EvidenceModal";

interface Asset {
  id: string;
  name: string;
  kind: "mesh" | "point_cloud" | "splat";
  format: "ply" | "obj" | "splat" | "ksplat";
  frame: string;
  url: string;
  provenance: string;
  origin: string;
  reconstructed: boolean;
  points: number | null;
  up_axis: "z" | "y";
  transforms: { id: string; source_frame: string; target_frame: string; method: string; residual_m: number | null }[];
}
interface OverlayItem {
  kind: "anchor" | "observation";
  id: string;
  label: string;
  type: string;
  position: { x: number; y: number; z: number };
  frame_of_record: string;
  via?: string[];
  provenance: string;
  observation_id: string | null;
  review_status?: string;
  uncertainty_m?: number | null;
  created_at: string;
}
interface Overlay {
  items: OverlayItem[];
  unaligned_frames: string[];
  note: string | null;
}

const COLORS: Record<string, number> = { observation: 0xf0b429, room: 0x7fb4ff, anchor: 0x9adf8f };

/** Scene viewer: orbit, pan and zoom over a mesh, point cloud or Gaussian splat in its own frame. Annotations made
 *  on the scene always show; incident observations appear only through a valid alignment (the server refuses
 *  the rest and says which frame is missing). */
export function SceneViewer() {
  const incident = useIncident((s) => s.incident);
  const showToast = useStore((s) => s.showToast);
  const host = useRef<HTMLDivElement>(null);
  const three = useRef<{ scene: THREE.Scene; camera: THREE.PerspectiveCamera; controls: OrbitControls; content: THREE.Group; marks: THREE.Group; renderer: THREE.WebGLRenderer; labels: CSS2DRenderer } | null>(null);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [current, setCurrent] = useState<string | null>(null);
  const [overlay, setOverlay] = useState<Overlay | null>(null);
  const [status, setStatus] = useState<string>("");
  const [picked, setPicked] = useState<OverlayItem | null>(null);
  const [evidence, setEvidence] = useState<string | null>(null);
  const [labeling, setLabeling] = useState<{ x: number; y: number; z: number } | null>(null);
  const [labelMode, setLabelMode] = useState(false);
  const [roomName, setRoomName] = useState("Room");
  const [align, setAlign] = useState({ yaw: "0", east: "0", north: "0" });
  const fileRef = useRef<HTMLInputElement>(null);
  const asset = assets.find((a) => a.id === current) ?? null;
  const iid = incident?.id;

  const run = async (f: () => Promise<unknown>, ok?: string) => {
    try {
      await f();
      if (ok) showToast(ok);
    } catch (e) {
      showToast(String((e as Error).message), "error");
    }
  };

  useEffect(() => {
    setCurrent(null);
    setAssets([]);
    setPicked(null);
  }, [iid]);

  const loadAssets = useCallback(async () => {
    if (!iid) return;
    const r = await api.get<{ items: Asset[] }>(`/api/incidents/${iid}/scene/assets`);
    setAssets(r.items);
    setCurrent((c) => c ?? r.items[r.items.length - 1]?.id ?? null);
  }, [iid]);

  useEffect(() => {
    void loadAssets();
  }, [loadAssets]);

  // three.js setup once
  useEffect(() => {
    const el = host.current;
    if (!el) return;
    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(Math.min(2, window.devicePixelRatio));
    renderer.setClearColor(0x161d26);
    el.appendChild(renderer.domElement);
    const labels = new CSS2DRenderer();
    labels.domElement.style.position = "absolute";
    labels.domElement.style.inset = "0";
    labels.domElement.style.pointerEvents = "none";
    el.appendChild(labels.domElement);
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(55, 1, 0.05, 2000);
    camera.up.set(0, 0, 1);
    camera.position.set(20, -25, 22);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    scene.add(new THREE.AmbientLight(0xffffff, 0.8));
    const sun = new THREE.DirectionalLight(0xffffff, 1.2);
    sun.position.set(30, -20, 50);
    scene.add(sun);
    const grid = new THREE.GridHelper(100, 50, 0x34404d, 0x262f3a);
    grid.rotation.x = Math.PI / 2;
    scene.add(grid);
    const content = new THREE.Group();
    const marks = new THREE.Group();
    scene.add(content, marks);
    three.current = { scene, camera, controls, content, marks, renderer, labels };
    const resize = () => {
      const w = el.clientWidth || 1;
      const h = el.clientHeight || 1;
      renderer.setSize(w, h);
      labels.setSize(w, h);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
    };
    const ro = new ResizeObserver(resize);
    ro.observe(el);
    resize();
    let raf = 0;
    const tick = () => {
      controls.update();
      renderer.render(scene, camera);
      labels.render(scene, camera);
      raf = requestAnimationFrame(tick);
    };
    tick();
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      controls.dispose();
      renderer.dispose();
      el.innerHTML = "";
      three.current = null;
    };
  }, []);

  // load the selected asset
  useEffect(() => {
    const t = three.current;
    if (!t) return;
    t.content.clear(); // never leave another scene (or another incident's scene) on screen
    if (!asset) {
      setStatus("");
      return;
    }
    let cancelled = false;
    const url = withToken(asset.url);
    const fit = (obj: THREE.Object3D) => {
      const b = new THREE.Box3().setFromObject(obj);
      if (b.isEmpty()) return;
      const c = b.getCenter(new THREE.Vector3());
      const r = b.getSize(new THREE.Vector3()).length() / 2;
      t.controls.target.copy(c);
      t.camera.position.copy(c.clone().add(new THREE.Vector3(r * 0.9, -r * 1.1, r * 0.9)));
    };
    const place = (obj: THREE.Object3D) => {
      if (asset.up_axis === "y") obj.rotation.x = Math.PI / 2;
      t.content.add(obj);
      fit(obj);
    };
    const asPoints = (geo: THREE.BufferGeometry) => {
      const mat = new THREE.PointsMaterial({ size: 0.06, vertexColors: geo.hasAttribute("color"), color: geo.hasAttribute("color") ? 0xffffff : 0xb8c2cc });
      return new THREE.Points(geo, mat);
    };
    const debug = (n: number, how: string) => {
      (window as unknown as { __sceneViewer?: unknown }).__sceneViewer = { asset: asset.id, rendered: how, points: n };
      setStatus(how);
    };
    setStatus("loading…");
    if (asset.kind === "splat") {
      import("@mkkellogg/gaussian-splats-3d")
        .then(async (G) => {
          const viewer = new G.DropInViewer({ gpuAcceleratedSort: false, sharedMemoryForWorkers: false });
          const format = asset.format === "ply" ? G.SceneFormat.Ply : asset.format === "ksplat" ? G.SceneFormat.KSplat : G.SceneFormat.Splat;
          await viewer.addSplatScene(url, { format, showLoadingUI: false, progressiveLoad: false });
          if (cancelled) return;
          place(viewer);
          debug(asset.points ?? 0, "Gaussian splat");
        })
        .catch((e) => {
          if (cancelled) return;
          if (asset.format !== "ply") {
            setStatus(`splat renderer failed: ${String(e)}`);
            return;
          }
          new PLYLoader().load(url, (geo) => {
            place(asPoints(geo));
            debug(geo.getAttribute("position").count, "splat renderer failed: showing splat centres as points");
          });
        });
    } else if (asset.format === "ply") {
      new PLYLoader().load(
        url,
        (geo) => {
          if (cancelled) return;
          if (asset.kind === "mesh" && geo.index) {
            geo.computeVertexNormals();
            place(new THREE.Mesh(geo, new THREE.MeshStandardMaterial({ vertexColors: geo.hasAttribute("color"), color: 0xc8ccd0, side: THREE.DoubleSide })));
          } else place(asPoints(geo));
          debug(geo.getAttribute("position").count, asset.kind === "mesh" ? "mesh" : "point cloud");
        },
        undefined,
        (e) => setStatus(`could not load: ${String(e)}`),
      );
    } else if (asset.format === "obj") {
      new OBJLoader().load(url, (obj) => {
        if (cancelled) return;
        obj.traverse((o) => {
          if ((o as THREE.Mesh).isMesh) (o as THREE.Mesh).material = new THREE.MeshStandardMaterial({ color: 0xc8ccd0, side: THREE.DoubleSide });
        });
        place(obj);
        debug(0, "mesh");
      });
    }
    return () => {
      cancelled = true;
    };
  }, [asset]);

  // annotations
  const loadOverlay = useCallback(async () => {
    if (!iid || !asset) {
      setOverlay(null);
      return;
    }
    setOverlay(await api.get<Overlay>(`/api/incidents/${iid}/scene/overlay?frame=${encodeURIComponent(asset.frame)}`));
  }, [iid, asset]);
  useEffect(() => {
    void loadOverlay();
  }, [loadOverlay]);
  useEffect(() => {
    const t = three.current;
    if (!t) return;
    t.marks.clear();
    for (const it of overlay?.items ?? []) {
      const color = COLORS[it.kind === "observation" ? "observation" : it.type === "room" ? "room" : "anchor"];
      const s = new THREE.Mesh(new THREE.SphereGeometry(0.25, 16, 12), new THREE.MeshBasicMaterial({ color }));
      s.position.set(it.position.x, it.position.y, (it.position.z ?? 0) + 0.25);
      const div = document.createElement("button");
      div.type = "button";
      div.textContent = it.label;
      div.className = "rounded bg-black/70 px-1.5 py-0.5 text-[11px] font-semibold text-white ring-1 ring-white/20 hover:bg-black/90";
      div.style.pointerEvents = "auto";
      div.onclick = () => setPicked(it);
      const lab = new CSS2DObject(div);
      lab.position.set(0, 0, 0.6);
      s.add(lab);
      t.marks.add(s);
    }
    (window as unknown as { __sceneOverlay?: unknown }).__sceneOverlay = { items: overlay?.items.length ?? 0, unaligned: overlay?.unaligned_frames ?? [] };
  }, [overlay]);

  // pick a point for a room label
  useEffect(() => {
    const t = three.current;
    if (!t || !labelMode) return;
    const el = t.renderer.domElement;
    const ray = new THREE.Raycaster();
    ray.params.Points = { threshold: 0.15 };
    const onClick = (ev: MouseEvent) => {
      const r = el.getBoundingClientRect();
      ray.setFromCamera(new THREE.Vector2(((ev.clientX - r.left) / r.width) * 2 - 1, -((ev.clientY - r.top) / r.height) * 2 + 1), t.camera);
      const hit = ray.intersectObjects(t.content.children, true)[0];
      if (!hit) {
        showToast("No scan surface there: click on the points", "error");
        return;
      }
      setLabeling({ x: hit.point.x, y: hit.point.y, z: hit.point.z });
      setLabelMode(false);
    };
    el.addEventListener("click", onClick);
    el.style.cursor = "crosshair";
    return () => {
      el.removeEventListener("click", onClick);
      el.style.cursor = "";
    };
  }, [labelMode, showToast]);

  const upload = async (f: File) => {
    if (!iid) return;
    const ext = f.name.split(".").pop()?.toLowerCase() ?? "";
    if (ext === "json" && asset) {
      await run(async () => {
        await api.postRaw(`/api/scene-assets/${asset.id}/transform`, await f.text());
        await loadAssets();
        await loadOverlay();
      }, "Transform imported");
      return;
    }
    if (!["ply", "obj", "splat", "ksplat"].includes(ext)) {
      showToast("Import a .ply, .obj, .splat or .ksplat (or a transform.json for the selected scene)", "error");
      return;
    }
    await run(async () => {
      const a = await api.postRaw<Asset>(
        `/api/incidents/${iid}/scene/assets?name=${encodeURIComponent(f.name)}&format=${ext}&origin=${encodeURIComponent("imported file (origin not stated)")}`,
        f,
      );
      await loadAssets();
      setCurrent(a.id);
    }, "Scene imported");
  };

  const aligned = asset ? asset.transforms.length > 0 : false;
  const simulated = incident?.provenance === "simulated";

  return (
    <div className="absolute inset-0 overflow-hidden bg-[#161d26]">
      <div ref={host} className="absolute inset-0" />
      <div className="absolute left-2 right-2 top-2 z-10 flex flex-wrap items-center gap-1 rounded-md bg-black/55 p-1 text-[11px] text-white backdrop-blur-sm">
        <Box size={13} className="mx-1 text-[#c9c2ff]" />
        <select
          aria-label="Scene"
          value={current ?? ""}
          onChange={(e) => setCurrent(e.target.value || null)}
          className="h-6 max-w-[220px] rounded bg-white/10 px-1 text-[11px] text-white"
        >
          {assets.length === 0 && <option value="">No scenes yet</option>}
          {assets.map((a) => (
            <option key={a.id} value={a.id} className="text-black">
              {a.name}
            </option>
          ))}
        </select>
        {simulated && (
          <button type="button" className="rounded px-1.5 py-0.5 hover:bg-white/15" onClick={() => run(async () => { const a = await api.post<Asset>(`/api/incidents/${iid}/scene/sim-export`); await loadAssets(); setCurrent(a.id); }, "Simulator geometry exported")}>
            Simulator geometry
          </button>
        )}
        <button type="button" className="rounded px-1.5 py-0.5 hover:bg-white/15" onClick={() => run(async () => { const a = await api.post<Asset>(`/api/incidents/${iid}/scene/sample-indoor`); await loadAssets(); setCurrent(a.id); }, "Synthetic sample scan loaded")}>
          <Home size={12} className="mr-0.5 inline" /> Sample indoor scan
        </button>
        <button type="button" className="rounded px-1.5 py-0.5 hover:bg-white/15" onClick={() => fileRef.current?.click()} title="PLY, OBJ, .splat, .ksplat, or transform.json for the selected scene">
          <FileUp size={12} className="mr-0.5 inline" /> Import
        </button>
        <input ref={fileRef} type="file" accept=".ply,.obj,.splat,.ksplat,.json" className="hidden" onChange={(e) => { const f = e.target.files?.[0]; if (f) void upload(f); e.target.value = ""; }} />
        {asset && (
          <button type="button" className={`rounded px-1.5 py-0.5 ${labelMode ? "bg-[#7fb4ff]/40" : "hover:bg-white/15"}`} onClick={() => setLabelMode((v) => !v)}>
            <Tag size={12} className="mr-0.5 inline" /> {labelMode ? "Click the scan…" : "Room label"}
          </button>
        )}
        <span className="ml-auto pr-1 text-[10px] text-[#c3c2b7]">{status}</span>
      </div>

      {asset && (
        <div className="absolute bottom-2 left-2 z-10 flex max-w-[min(560px,calc(100%-1rem))] flex-col gap-1 rounded-md bg-black/60 p-2 text-[11px] text-[#e6e4dc] backdrop-blur-sm">
          <div className="flex flex-wrap items-center gap-1">
            <Badge color={asset.provenance === "simulated" ? "var(--s7)" : "var(--ink-2)"} solid>{asset.provenance.toUpperCase()}</Badge>
            <Badge color="var(--ink-2)">{asset.kind.replace("_", " ")}</Badge>
            <Badge color={aligned ? "var(--good)" : "var(--warning)"}>{aligned ? `aligned · ${asset.transforms[0].method}${asset.transforms[0].residual_m != null ? ` · ±${asset.transforms[0].residual_m.toFixed(2)} m` : ""}` : "not aligned to the incident map"}</Badge>
            {!asset.reconstructed && <Badge color="var(--ink-3)">not a reconstruction</Badge>}
          </div>
          <div className="text-[#a9a79f]">{asset.origin} · frame {asset.frame}{asset.points ? ` · ${asset.points.toLocaleString()} points` : ""}</div>
          {overlay?.note && <div className="text-[#e0a83a]">{overlay.note}</div>}
          {!aligned && (
            <div className="flex flex-wrap items-center gap-1">
              <Move3d size={12} />
              <span>Align (operator-entered):</span>
              {(["yaw", "east", "north"] as const).map((k) => (
                <label key={k} className="flex items-center gap-0.5">
                  {k}
                  <input aria-label={`align ${k}`} value={align[k]} onChange={(e) => setAlign({ ...align, [k]: e.target.value })} className="h-5 w-12 rounded bg-white/10 px-1 text-white" />
                </label>
              ))}
              <Btn tone="primary" onClick={() => run(async () => {
                const y = (Number(align.yaw) * Math.PI) / 180;
                const m = [[Math.cos(y), -Math.sin(y), 0, Number(align.east)], [Math.sin(y), Math.cos(y), 0, Number(align.north)], [0, 0, 1, 0], [0, 0, 0, 1]];
                if (m.flat().some((v) => !Number.isFinite(v))) throw new Error("yaw, east and north must be numbers");
                await api.postRaw(`/api/scene-assets/${asset.id}/transform`, JSON.stringify({ matrix: m, method: "manual (operator-entered offset)", manually_verified: true }));
                await loadAssets();
                await loadOverlay();
              }, "Alignment saved")}>Save</Btn>
            </div>
          )}
        </div>
      )}

      {labeling && asset && (
        <div className="absolute right-2 top-12 z-20 flex items-center gap-1 rounded-md bg-black/75 p-2 text-[11px] text-white">
          <Crosshair size={12} />
          <input aria-label="Room label" value={roomName} onChange={(e) => setRoomName(e.target.value)} className="h-6 w-32 rounded bg-white/10 px-1" />
          <Btn tone="primary" onClick={() => run(async () => {
            await api.post(`/api/incidents/${iid}/scene/anchors`, { name: roomName, type: "room", position: { frame: asset.frame, ...labeling }, manually_verified: true, attributes: { provenance: "manual" } });
            setLabeling(null);
            await loadOverlay();
          }, "Room label added")}>Add</Btn>
          <Btn onClick={() => setLabeling(null)}>Cancel</Btn>
        </div>
      )}

      {picked && (
        <div className="absolute right-2 top-12 z-20 w-64 rounded-md bg-black/80 p-2 text-[11px] text-[#e6e4dc]">
          <div className="mb-1 flex items-center justify-between">
            <span className="text-[12px] font-semibold text-white">{picked.label}</span>
            <button type="button" className="text-[#a9a79f] hover:text-white" onClick={() => setPicked(null)}>close</button>
          </div>
          <div>{picked.kind === "observation" ? `${picked.type} · ${picked.review_status}` : picked.type} · {picked.provenance}</div>
          <div>recorded {new Date(picked.created_at).toLocaleTimeString()} in {picked.frame_of_record}</div>
          {picked.via && <div className="text-[#a9a79f]">shown via {picked.via.join(" → ")}</div>}
          {picked.uncertainty_m != null && <div>position uncertainty ±{picked.uncertainty_m.toFixed(1)} m</div>}
          {picked.observation_id && (
            <Btn className="mt-1" onClick={() => setEvidence(picked.observation_id)}>Open evidence</Btn>
          )}
        </div>
      )}
      {evidence && <EvidenceModal obsId={evidence} onClose={() => setEvidence(null)} />}
      {!asset && (
        <div className="absolute inset-0 flex items-center justify-center p-6 text-center text-[12.5px] text-[#a9a79f]">
          No scene loaded. Import a PLY, OBJ or splat (with an optional transform.json), {simulated ? "export the simulator geometry, " : ""}or load the synthetic sample indoor scan.
        </div>
      )}
    </div>
  );
}
