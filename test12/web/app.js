"use strict";
// test12 page: start a run, watch the video (drone camera or a simulator view), draw the map from /api/test12/world
// and /api/test12/state, pick where the suspect starts, and read the drone's callouts to the police out loud.

const $ = (id) => document.getElementById(id);
const S = { world: null, run: null, layer: null, dpr: 1, surveyKey: null, surveyCanvas: null, polling: false,
  spawn: null, view: "drone" };
const PHASE_COLOR = { REPORTED: "#ffb547", TAKEOFF: "#ffb547", SEARCH: "#3987e5", FOLLOW: "#2fbf4a", SCAN: "#fab219",
  STOPPED: "#898781", TIME_LIMIT: "#f05a4a", IDLE: "#55554f" };
const VIEW_TEXT = {
  drone: ["Drone camera", "DJI Mini 2 (simulated) · 720p 30 fps · YOLO26n at 5 Hz"],
  chase: ["Third-person view", "simulator camera 3 m behind the drone (drawn to scale) · its camera in the corner"],
  top: ["Bird's-eye view", "simulator camera 95 m up, north up · the drone's camera in the corner"],
};
const KIND_FILL = { house: "#4c4c55", building: "#5f5f6d", station: "#36598f", garage: "#696964", truck: "#8b8b8b",
  kiosk: "#86705a", vehicle: "#3f3f46", pad: "#cfcfcf", hangar: "#74747c", fence: "#55555a" };
const ROOF = new Set(["carport", "awning", "walkway", "shelter"]);

const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const sgn = (v) => (v >= 0 ? "+" : "−") + Math.abs(v).toFixed(1);
const pad3 = (v) => String(Math.round(v)).padStart(3, "0");

// --- map geometry ----------------------------------------------------------------------------------------------
function tf() {
  const c = $("map");
  const [x0, x1, y0, y1] = S.world.bounds;
  const m = 10 * S.dpr;
  const s = Math.min((c.width - 2 * m) / (x1 - x0), (c.height - 2 * m) / (y1 - y0));
  const ox = (c.width - s * (x1 - x0)) / 2;
  const oy = (c.height - s * (y1 - y0)) / 2;
  return { s, X: (x) => ox + (x - x0) * s, Y: (y) => c.height - oy - (y - y0) * s,
    inv: (px, py) => [x0 + (px - ox) / s, y0 + (c.height - oy - py) / s] };
}

// the nearest point on a sidewalk (where the suspect can start), as the server will place it
function snapSidewalk(x, y) {
  let best = null;
  for (const [, axis, kind, a, b] of S.world.streets) {
    if (kind !== "street") continue;
    const o = S.world.lane_offset[axis];
    for (const d of [o, -o]) {
      const ax = a[0] + (axis === "ns" ? d : 0), ay = a[1] + (axis === "ew" ? d : 0);
      const bx = b[0] + (axis === "ns" ? d : 0), by = b[1] + (axis === "ew" ? d : 0);
      const L2 = (bx - ax) ** 2 + (by - ay) ** 2 || 1;
      const t = Math.max(0, Math.min(1, ((x - ax) * (bx - ax) + (y - ay) * (by - ay)) / L2));
      const px = ax + t * (bx - ax), py = ay + t * (by - ay);
      const dist = Math.hypot(px - x, py - y);
      if (!best || dist < best.d) best = { x: px, y: py, d: dist };
    }
  }
  return best;
}

function resize() {
  const c = $("map");
  S.dpr = Math.min(2, window.devicePixelRatio || 1);
  const w = Math.max(200, Math.round(c.clientWidth * S.dpr));
  const h = Math.max(150, Math.round(c.clientHeight * S.dpr));
  if (c.width !== w || c.height !== h) {
    c.width = w;
    c.height = h;
  }
  if (S.world) buildLayer();
  draw();
}

function buildLayer() {
  const c = $("map");
  const L = document.createElement("canvas");
  L.width = c.width;
  L.height = c.height;
  const g = L.getContext("2d");
  const T = tf();
  const W = S.world;
  const [x0, x1, y0, y1] = W.bounds;
  const px = (v) => v * T.s;
  g.fillStyle = "#18271c";
  g.fillRect(T.X(x0), T.Y(y1), px(x1 - x0), px(y1 - y0));
  const [p0, p1, q0, q1] = W.layout.park;
  g.fillStyle = "#13301a";
  g.fillRect(T.X(p0), T.Y(q1), px(p1 - p0), px(q1 - q0));
  // roads, then sidewalks on both sides
  g.lineCap = "butt";
  for (const [name, axis, kind, a, b] of W.streets) {
    g.strokeStyle = kind === "service" ? "#3a3a36" : "#3b3b39";
    g.lineWidth = px(kind === "service" ? 5 : W.road_width[axis]);
    g.beginPath(); g.moveTo(T.X(a[0]), T.Y(a[1])); g.lineTo(T.X(b[0]), T.Y(b[1])); g.stroke();
  }
  g.strokeStyle = "#5a5a54";
  g.lineWidth = Math.max(1, px(2.2));
  for (const [name, axis, kind, a, b] of W.streets) {
    if (kind !== "street") continue;
    const o = W.lane_offset[axis];
    for (const d of [o, -o]) {
      const dx = axis === "ns" ? d : 0, dy = axis === "ew" ? d : 0;
      g.beginPath(); g.moveTo(T.X(a[0] + dx), T.Y(a[1] + dy)); g.lineTo(T.X(b[0] + dx), T.Y(b[1] + dy)); g.stroke();
    }
  }
  // buildings and cover (roofs see-through: someone may be under them)
  const order = (k) => (ROOF.has(k) ? 2 : k === "post" ? 3 : 1);
  for (const [kind, bx0, bx1, by0, by1] of [...W.boxes].sort((p, q) => order(p[0]) - order(q[0]))) {
    if (kind === "post") continue;
    g.fillStyle = ROOF.has(kind) ? "rgba(160,160,172,0.55)" : (KIND_FILL[kind] || "#5a5a5a");
    g.fillRect(T.X(bx0), T.Y(by1), Math.max(1, px(bx1 - bx0)), Math.max(1, px(by1 - by0)));
  }
  g.fillStyle = "rgba(52,130,64,0.55)";
  for (const [x, y, r] of W.trees) {
    g.beginPath(); g.arc(T.X(x), T.Y(y), Math.max(1.5, px(r)), 0, 2 * Math.PI); g.fill();
  }
  // a grid every 50 m from the base: the coordinates every report uses
  const [bx, by] = W.pad;
  g.strokeStyle = "rgba(255,255,255,0.07)";
  g.lineWidth = 1;
  g.fillStyle = "rgba(255,255,255,0.38)";
  g.font = `${10 * S.dpr}px ui-sans-serif, system-ui, sans-serif`;
  for (let k = Math.ceil((x0 - bx) / 50); bx + k * 50 <= x1; k++) {
    const X = T.X(bx + k * 50);
    g.beginPath(); g.moveTo(X, T.Y(y0)); g.lineTo(X, T.Y(y1)); g.stroke();
    if (k) g.fillText(`${k > 0 ? "+" : "−"}${Math.abs(k * 50)} E`, X + 3, T.Y(y1) + 12 * S.dpr);
  }
  for (let k = Math.ceil((y0 - by) / 50); by + k * 50 <= y1; k++) {
    const Y = T.Y(by + k * 50);
    g.beginPath(); g.moveTo(T.X(x0), Y); g.lineTo(T.X(x1), Y); g.stroke();
    if (k) g.fillText(`${k > 0 ? "+" : "−"}${Math.abs(k * 50)} N`, T.X(x0) + 3, Y - 3);
  }
  // street names
  g.fillStyle = "rgba(235,235,225,0.55)";
  g.font = `${10.5 * S.dpr}px ui-sans-serif, system-ui, sans-serif`;
  const named = new Set();
  for (const [name, axis, kind, a, b] of W.streets) {
    if (kind !== "street" || named.has(name)) continue;
    const len = Math.hypot(b[0] - a[0], b[1] - a[1]);
    if (len < 80) continue;
    named.add(name);
    const mx = (a[0] + b[0]) / 2, my = (a[1] + b[1]) / 2;
    g.save();
    g.translate(T.X(mx), T.Y(my));
    if (axis === "ns") g.rotate(-Math.PI / 2);
    g.textAlign = "center";
    g.fillText(name, 0, -px(5.5) - 3);
    g.restore();
  }
  S.layer = L;
}

// --- dynamic layers --------------------------------------------------------------------------------------------
function surveyImage(sv) {
  const key = sv.data.length + ":" + sv.data.slice(0, 64) + sv.data.slice(-64) + S.run.t;
  if (key === S.surveyKey && S.surveyCanvas) return S.surveyCanvas;
  const bytes = atob(sv.data);
  const cv = S.surveyCanvas || document.createElement("canvas");
  cv.width = sv.nx;
  cv.height = sv.ny;
  const g = cv.getContext("2d");
  const im = g.createImageData(sv.nx, sv.ny);
  for (let i = 0; i < bytes.length; i++) {
    const a = bytes.charCodeAt(i);
    if (!a) continue;
    const alpha = a <= 91 ? 0.12 + 0.36 * (1 - (a - 1) / 90) : 0.1;
    im.data[4 * i] = 70; im.data[4 * i + 1] = 144; im.data[4 * i + 2] = 255; im.data[4 * i + 3] = Math.round(alpha * 255);
  }
  g.putImageData(im, 0, 0);
  S.surveyCanvas = cv;
  S.surveyKey = key;
  return cv;
}

function marker(g, x, y, r, fill, stroke) {
  g.beginPath(); g.arc(x, y, r, 0, 2 * Math.PI);
  if (fill) { g.fillStyle = fill; g.fill(); }
  if (stroke) { g.strokeStyle = stroke; g.stroke(); }
}

function label(g, text, x, y, color) {
  g.font = `${11 * S.dpr}px ui-sans-serif, system-ui, sans-serif`;
  g.lineWidth = 3 * S.dpr;
  g.strokeStyle = "rgba(0,0,0,0.75)";
  g.strokeText(text, x, y);
  g.fillStyle = color;
  g.fillText(text, x, y);
}

function draw() {
  const c = $("map");
  const g = c.getContext("2d");
  g.clearRect(0, 0, c.width, c.height);
  if (!S.world) return;
  if (S.layer) g.drawImage(S.layer, 0, 0);
  const T = tf();
  const d = S.dpr;
  const r = S.run;
  const live = r && r.drone;
  if (live && $("t-survey").checked && r.survey) {
    const sv = r.survey;
    g.imageSmoothingEnabled = false;
    g.drawImage(surveyImage(sv), T.X(sv.x0), T.Y(sv.y0 + sv.ny * sv.cell), sv.nx * sv.cell * T.s, sv.ny * sv.cell * T.s);
  }
  if (live && $("t-belief").checked && r.belief) {
    for (const [x, y, hid] of r.belief) {
      if (hid) marker(g, T.X(x), T.Y(y), 2.6 * d, null, "rgba(255,154,60,0.75)");
      else marker(g, T.X(x), T.Y(y), 1.7 * d, "rgba(255,154,60,0.55)");
    }
  }
  // base and its axes
  const [bx, by] = S.world.pad;
  g.strokeStyle = "#ffffff";
  g.lineWidth = 1.5 * d;
  g.beginPath(); g.moveTo(T.X(bx), T.Y(by)); g.lineTo(T.X(bx + 18), T.Y(by)); g.moveTo(T.X(bx), T.Y(by)); g.lineTo(T.X(bx), T.Y(by + 18)); g.stroke();
  marker(g, T.X(bx), T.Y(by), 4 * d, "#ffffff");
  label(g, "BASE (0, 0)", T.X(bx) + 6 * d, T.Y(by) + 14 * d, "#ffffff");
  if (S.spawn && !(r && r.running)) {  // where the next run's suspect starts
    const [sx, sy] = S.spawn;
    marker(g, T.X(sx), T.Y(sy), 6 * d, "rgba(255,77,77,0.35)", "#ff4d4d");
    marker(g, T.X(sx), T.Y(sy), 2 * d, "#ff4d4d");
    label(g, "suspect starts here", T.X(sx) + 9 * d, T.Y(sy) - 7 * d, "#ff9a9a");
  }
  if (!live) return;
  // where the call said, and what the planner is going for
  const [cx, cy] = r.crime.xy;
  g.fillStyle = "#ffb547";
  g.beginPath(); g.moveTo(T.X(cx), T.Y(cy) - 6 * d); g.lineTo(T.X(cx) + 6 * d, T.Y(cy)); g.lineTo(T.X(cx), T.Y(cy) + 6 * d); g.lineTo(T.X(cx) - 6 * d, T.Y(cy)); g.fill();
  label(g, "911: last seen", T.X(cx) + 8 * d, T.Y(cy) - 6 * d, "#ffcf8a");
  const [dx, dy] = r.drone.xy;
  if (r.pick) {
    const [vx, vy] = r.pick.viewpoint, [tx, ty] = r.pick.target;
    g.setLineDash([5 * d, 4 * d]);
    g.strokeStyle = "rgba(255,230,120,0.8)";
    g.lineWidth = 1.2 * d;
    g.beginPath(); g.moveTo(T.X(dx), T.Y(dy)); g.lineTo(T.X(vx), T.Y(vy)); g.stroke();
    g.setLineDash([]);
    g.beginPath(); g.moveTo(T.X(tx) - 6 * d, T.Y(ty)); g.lineTo(T.X(tx) + 6 * d, T.Y(ty)); g.moveTo(T.X(tx), T.Y(ty) - 6 * d); g.lineTo(T.X(tx), T.Y(ty) + 6 * d); g.stroke();
  }
  // the drone's path and what its camera covers (out to YOLO's range)
  const tr = r.drone.trail;
  if (tr && tr.length > 1) {
    g.strokeStyle = "rgba(76,201,240,0.55)";
    g.lineWidth = 1.4 * d;
    g.beginPath(); g.moveTo(T.X(tr[0][0]), T.Y(tr[0][1]));
    for (const [x, y] of tr) g.lineTo(T.X(x), T.Y(y));
    g.stroke();
  }
  if (r.drone.footprint) {
    g.fillStyle = "rgba(76,201,240,0.10)";
    g.strokeStyle = "rgba(76,201,240,0.6)";
    g.lineWidth = 1 * d;
    g.beginPath();
    r.drone.footprint.forEach(([x, y], i) => (i ? g.lineTo(T.X(x), T.Y(y)) : g.moveTo(T.X(x), T.Y(y))));
    g.closePath(); g.fill(); g.stroke();
  }
  // the 360-degree scan over where they were last seen: the ring of ground the camera sweeps (10 to 46 m out), and
  // how far round the current circle is
  if (r.scan) {
    const [cx2, cy2] = r.drone.xy;
    g.fillStyle = "rgba(250,178,25,0.10)";
    g.beginPath(); g.arc(T.X(cx2), T.Y(cy2), 46 * T.s, 0, 2 * Math.PI); g.arc(T.X(cx2), T.Y(cy2), 10 * T.s, 0, 2 * Math.PI, true); g.fill();
    g.strokeStyle = "#fab219";
    g.lineWidth = 2.5 * d;
    g.beginPath(); g.arc(T.X(cx2), T.Y(cy2), 46 * T.s, -Math.PI / 2, -Math.PI / 2 + (2 * Math.PI * r.scan.turned) / 360); g.stroke();
    label(g, `360° scan · circle ${r.scan.circle}`, T.X(cx2) + 48 * T.s + 4 * d, T.Y(cy2), "#ffd479");
  }
  // police car and its route
  const pol = r.police;
  if (pol && pol.state !== "PARKED" && pol.route && pol.route.length) {
    g.setLineDash([6 * d, 4 * d]);
    g.strokeStyle = "rgba(91,140,255,0.85)";
    g.lineWidth = 1.6 * d;
    g.beginPath(); g.moveTo(T.X(pol.xy[0]), T.Y(pol.xy[1]));
    for (const [x, y] of pol.route) g.lineTo(T.X(x), T.Y(y));
    g.stroke();
    g.setLineDash([]);
  }
  if (pol) {
    g.save();
    g.translate(T.X(pol.xy[0]), T.Y(pol.xy[1]));
    g.rotate((pol.heading * Math.PI) / 180);
    const L = Math.max(7 * d, 4.9 * T.s), W = Math.max(4 * d, 1.9 * T.s);
    g.fillStyle = "#f2f2f2"; g.fillRect(-W / 2, -L / 2, W, L);
    g.fillStyle = "#1c1c1c"; g.fillRect(-W / 2, -L / 2, W, L * 0.28); g.fillRect(-W / 2, L * 0.22, W, L * 0.28);
    g.fillStyle = "#e53935"; g.fillRect(-W / 2, -L * 0.04, W / 2, L * 0.08);
    g.fillStyle = "#2f6bff"; g.fillRect(0, -L * 0.04, W / 2, L * 0.08);
    g.restore();
    if (pol.state !== "PARKED") label(g, "Police", T.X(pol.xy[0]) + 8 * d, T.Y(pol.xy[1]) + 4 * d, "#b9cbff");
    if (pol.ping) marker(g, T.X(pol.ping.xy[0]), T.Y(pol.ping.xy[1]), 9 * d, null, "rgba(156,184,255,0.9)");
  }
  // the latest callout: from the car (where its ping says it is) to the suspect
  const lastRep = (r.reports || [])[(r.reports || []).length - 1];
  if (pol && lastRep && lastRep.police && r.t - lastRep.t < 4) {
    const [qx, qy] = pol.ping ? pol.ping.xy : pol.xy;
    g.setLineDash([3 * d, 3 * d]);
    g.strokeStyle = "rgba(224,90,214,0.9)";
    g.lineWidth = 1.5 * d;
    g.beginPath(); g.moveTo(T.X(qx), T.Y(qy)); g.lineTo(T.X(lastRep.xy[0]), T.Y(lastRep.xy[1])); g.stroke();
    g.setLineDash([]);
    const rel = lastRep.police;
    label(g, `${rel.distance_m.toFixed(0)} m ${rel.compass} of the car`, (T.X(qx) + T.X(lastRep.xy[0])) / 2 + 6 * d,
      (T.Y(qy) + T.Y(lastRep.xy[1])) / 2, "#f3a6ee");
  }
  // reports to the police, the latest labelled in base coordinates
  const reps = r.reports || [];
  reps.forEach((p, i) => marker(g, T.X(p.xy[0]), T.Y(p.xy[1]), (i === reps.length - 1 ? 4.2 : 2.6) * d, "#e05ad6"));
  if (reps.length) {
    const p = reps[reps.length - 1];
    label(g, `(${sgn(p.base[0])}, ${sgn(p.base[1])})`, T.X(p.xy[0]) + 7 * d, T.Y(p.xy[1]) - 7 * d, "#f3a6ee");
  }
  if (r.target && r.target.fresh) marker(g, T.X(r.target.xy[0]), T.Y(r.target.xy[1]), 7 * d, null, "#7ee28f");
  if (r.last_seen) {
    const [lx, ly] = r.last_seen.xy;
    marker(g, T.X(lx), T.Y(ly), 7 * d, null, "#fab219");
    label(g, "last seen", T.X(lx) + 9 * d, T.Y(ly) + 4 * d, "#ffd479");
  }
  if ($("t-truth").checked && r.truth) {
    g.lineWidth = 2 * d;
    for (const [x, y] of r.truth.bystanders) marker(g, T.X(x), T.Y(y), 2.4 * d, "rgba(200,200,200,0.7)");
    const [sx, sy] = r.truth.suspect;
    marker(g, T.X(sx), T.Y(sy), 5.5 * d, null, "#ff4d4d");
    label(g, r.truth.suspect_state === "HIDING" ? "true (sim): hiding" : "true (sim)", T.X(sx) + 8 * d, T.Y(sy) + 13 * d, "#ff8a8a");
  }
  // the drone: a triangle pointing where it faces
  g.save();
  g.translate(T.X(dx), T.Y(dy));
  g.rotate((r.drone.heading * Math.PI) / 180);
  g.fillStyle = "#4cc9f0";
  g.strokeStyle = "#062a36";
  g.lineWidth = 1.5 * d;
  g.beginPath(); g.moveTo(0, -9 * d); g.lineTo(6 * d, 7 * d); g.lineTo(0, 4 * d); g.lineTo(-6 * d, 7 * d); g.closePath(); g.fill(); g.stroke();
  g.restore();
}

// --- panels ----------------------------------------------------------------------------------------------------
function renderStatus(r) {
  const phase = r.phase || "IDLE";
  if (phase === "IDLE") return;
  const pill = `<span class="pill" style="background:${PHASE_COLOR[phase] || "#555"};color:#0d0d0d">${esc(r.phase_label || phase)}</span>`;
  const bits = [`t <b>${(r.t || 0).toFixed(1)} s</b>`, `seed <b>${r.seed}</b>`];
  if (r.speed !== undefined && r.running) bits.push(`sim speed ${r.speed.toFixed(2)}×`);
  if (r.video && r.running) bits.push(`video ${r.video.fps.toFixed(0)}/${r.video.target_fps} fps`);
  if (r.detector && r.detector.yolo_ms_mean) bits.push(`YOLO ${r.detector.hz || 5} Hz, ${Math.round(r.detector.yolo_ms_mean)} ms a pass`);
  if (r.drone) bits.push(`altitude ${r.drone.alt.toFixed(0)} m, gimbal ${r.drone.gimbal.toFixed(0)}°`);
  $("status").innerHTML = pill + bits.join(" · ");
}

function renderSteps(r) {
  const order = ["REPORTED", "TAKEOFF", "SEARCH", "FOLLOW"];
  const ph = r.phase;
  const ended = ph === "STOPPED" || ph === "TIME_LIMIT";
  const lost = ph === "SCAN";
  const pol = r.police || {};
  const out = pol.state && pol.state !== "PARKED";
  let cur = { REPORTED: 0, TAKEOFF: 1, SEARCH: 2, FOLLOW: 3, SCAN: 3 }[ph];
  if (ended) cur = (r.reports || []).length ? 4 : 3;
  if (cur === undefined) cur = -1;
  for (const li of $("steps").children) {
    const p = li.dataset.p;
    li.className = "";
    if (p === "POLICE") {
      if (pol.on_scene || (ended && out)) li.className = "done";
      else if (out) li.className = "active";
      li.textContent = out && pol.eta_s != null ? `Police: ${Math.round(pol.eta_s)} s` : "Police en route";
      continue;
    }
    if (p === "SCENE") {
      if (pol.on_scene) li.className = ended ? "done" : "active";
      continue;
    }
    const i = order.indexOf(p);
    if (i < cur) li.className = "done";
    else if (i === cur) li.className = lost ? "lost" : "active";
    if (p === "FOLLOW") li.textContent = ph === "SCAN" ? "Lost: 360° scan" : "Follow";
  }
}

function renderCallout(r) {
  const cs = r.callouts || [];
  const c = cs[cs.length - 1];
  $("callout").hidden = !c;
  if (!c) return;
  const speaking = V.cur && V.cur.run === r.run_no && V.cur.n === c.n && V.busy;
  $("callout").innerHTML = `<div class="c-k">${speaking ? "🔊 Saying now" : "Latest callout"} · ${c.t.toFixed(1)} s</div>` +
    `<div class="c-x">“${esc(c.speech)}”</div>`;
  $("callout").className = "callout" + (c.kind === "lost" ? " lost" : "");
}

function renderReport(r) {
  renderCallout(r);
  const reps = r.reports || [];
  const p = reps[reps.length - 1];
  if (!p) return;
  $("rep-t").textContent = `${reps.length} report${reps.length > 1 ? "s" : ""} · latest at ${p.t.toFixed(1)} s`;
  const moving = p.moving ? `running ${p.moving} at ${p.speed_mps.toFixed(1)} m/s` : "not moving";
  const rel = p.police;
  $("report").innerHTML =
    (rel ? `<div class="rep-pol">From the police car: <b>${rel.distance_m.toFixed(0)} m ${rel.compass}</b> ` +
      `(its ${rel.clock} o'clock, bearing ${pad3(rel.bearing_deg)}°) · <span class="muted">by the car's ping ` +
      `${rel.ping_age_s.toFixed(1)} s earlier: base ${sgn(rel.car_base[0])} E, ${sgn(rel.car_base[1])} N</span></div>` : "") +
    `<div class="rep-main">Suspect at <span class="xy">base ${sgn(p.base[0])} m E, ${sgn(p.base[1])} m N</span></div>` +
    `<div class="rep-line">(base${sgn(p.base[0])}, base${sgn(p.base[1])}) · ${p.distance_m.toFixed(0)} m from the base, bearing ` +
    `${pad3(p.bearing_deg)}° (${p.compass}) · ${moving} · near ${esc(p.near)}</div>` +
    `<div class="rep-how"><b>How it is worked out:</b> the drone is at <b>base ${sgn(p.drone_base[0])} E, ${sgn(p.drone_base[1])} N</b> ` +
    `by dead reckoning (its velocity and compass added up since take-off, no GPS) <b>+</b> the camera offset ` +
    `<b>${sgn(p.offset[0])} E, ${sgn(p.offset[1])} N</b> (altitude, gimbal pitch, heading and where the person is in the image).</div>` +
    `<div class="rep-truth">Simulator truth: ${p.error_m.toFixed(1)} m from the suspect's true position.</div>`;
}

function renderPolice(r) {
  const p = r.police;
  if (!p) return;
  let text = "Police car: parked at the station.";
  if (p.state === "EN_ROUTE") {
    const len = (p.route || []).reduce((acc, q, i, a) => acc + (i ? Math.hypot(q[0] - a[i - 1][0], q[1] - a[i - 1][1]) : Math.hypot(q[0] - p.xy[0], q[1] - p.xy[1])), 0);
    text = `Police car: <b>on its way</b> to the latest report, catching up in about <b>${Math.round(p.eta_s || 0)} s</b> (${len.toFixed(0)} m of streets left).`;
  } else if (p.state === "CLOSING") text = `Police car: <b>${p.on_scene ? "on scene" : "near"}</b>, closing on the latest callout.`;
  else if (p.state === "STOPPED") text = `Police car: <b>stopped</b> beside the last callout${p.on_scene ? " (on scene)" : ""}.`;
  if (p.dispatched_t != null) text += ` Dispatched at ${p.dispatched_t.toFixed(1)} s.`;
  if (p.ping) text += `<br><span class="muted">Its ping (every second): base ${sgn(p.ping.base[0])} E, ${sgn(p.ping.base[1])} N, ` +
    `heading ${pad3(p.ping.heading)}°, ${p.ping.speed.toFixed(1)} m/s · ${p.ping.age_s.toFixed(1)} s ago</span>`;
  $("police").innerHTML = text;
}

function renderEvents(r) {
  const ev = r.events || [];
  const box = $("events");
  const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 30;
  box.innerHTML = ev.map((e) => `<li class="k-${esc(e.kind)}"><span class="t">${e.t.toFixed(1)} s</span><span class="x">${esc(e.text)}</span></li>`).join("");
  $("ev-n").textContent = ev.length ? `${ev.length} events` : "";
  if (atBottom) box.scrollTop = box.scrollHeight;
}

function renderSummary(r) {
  const s = r.summary;
  $("summary-card").hidden = !s;
  if (!s) return;
  const pair = (a, b, unit) => (a != null ? `${a} / ${b} ${unit}` : "–");
  const rows = [
    ["Outcome", s.outcome === "STOPPED" ? "Stopped from the page" : s.outcome === "TIME_LIMIT" ? "Time limit" : (s.outcome || "–")],
    ["Run time", `${s.duration_s} s`],
    ["Suspect start", `${s.spawn_picked ? "picked on the map" : "random"} · ${s.head_start_s} s head start`],
    ["Found by YOLO (matched) at", s.matched_s != null ? `${s.matched_s} s` : "not found"],
    ["Following, since the match", s.following_pct != null ? `${s.following_pct} % of the time` : "–"],
    ["Drone to suspect while following (median / 90th pct)", pair(s.follow_dist_median_m, s.follow_dist_p90_m, "m")],
    ["Lost / found again by the 360° scan", `${s.losses} / ${s.reacquired}`],
    ["Out of sight before found again (median / max)", pair(s.refind_median_s, s.refind_max_s, "s")],
    ["Reports / callouts to police", `${s.reports} / ${s.callouts}`],
    ["Report error (mean / max)", pair(s.report_error_mean_m, s.report_error_max_m, "m")],
    ["Callout error from the car (mean / max)", pair(s.callout_error_mean_m, s.callout_error_max_m, "m")],
    ["Police: dispatch to on scene", s.police_dispatch_to_scene_s != null ? `${s.police_dispatch_to_scene_s} s` : "–"],
    ["Police to suspect once on scene (median)", s.police_gap_median_m != null ? `${s.police_gap_median_m} m` : "–"],
    ["Times the suspect hid", String(s.suspect_hides)],
    ["Sidewalk surveyed", `${s.sidewalk_surveyed_pct} %`],
    ["Search over just-seen ground", s.search_revisit_pct != null ? `${s.search_revisit_pct} %` : "–"],
    ["Distance flown", `${s.drone_distance_m} m`],
    ["Dead-reckoning error", `${s.dead_reckoning_error_m} m`],
    ["YOLO passes", `${s.yolo_calls ?? s.detector_passes} (${s.yolo_ms_mean ?? "–"} ms each)`],
  ];
  $("summary").innerHTML = rows.map(([k, v]) => `<div><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div></div>`).join("");
}

// --- voice: the callouts read out (ElevenLabs through the server, the browser's own voice otherwise) ---------------
const V = { on: true, engine: null, busy: false, cur: null, last: { run: null, n: 0 }, at: 0, blocked: false, el: new Audio() };

// phones (iOS Safari) only let a page make sound once it has played something inside a tap: play a silent clip then
const SILENT = "data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEARKwAAIhYAQACABAAZGF0YQAAAAA=";
function primeAudio() {
  if (V.busy) return;
  V.el.src = SILENT;
  V.el.play().catch(() => {});
  if ("speechSynthesis" in window) speechSynthesis.speak(new SpeechSynthesisUtterance(""));
}

function noteVoice() {
  $("voice-note").textContent = V.blocked ? "(click the page to allow sound)" : V.engine ? `(${V.engine})` : "";
}

function voiceDone() {
  V.busy = false;
  if (S.run) renderCallout(S.run);
}
V.el.addEventListener("ended", () => { if (V.busy) voiceDone(); });
V.el.addEventListener("error", () => { if (V.busy) V.cur ? browserSay(V.cur.speech) : voiceDone(); });

function browserSay(text) {
  if (!("speechSynthesis" in window)) return voiceDone();
  const u = new SpeechSynthesisUtterance(text);
  u.rate = 1.05;
  u.onend = u.onerror = voiceDone;
  speechSynthesis.speak(u);
}

function voiceTick(r) {
  if (!V.on || V.busy || V.blocked || !r.running || !(r.callouts || []).length) return;
  if (V.last.run !== r.run_no) V.last = { run: r.run_no, n: 0 };
  const fresh = r.callouts.filter((c) => c.n > V.last.n && r.t - c.t < 10);
  if (!fresh.length) return;
  const urgent = fresh.filter((c) => c.urgent);
  const c = urgent.length ? urgent[urgent.length - 1] : fresh[fresh.length - 1];
  if (!c.urgent && performance.now() - V.at < 8000) return;  // routine callouts: one every 8 s at most (each takes ~5 s)
  V.last.n = c.n;
  V.busy = true;
  V.at = performance.now();
  V.cur = { run: r.run_no, n: c.n, speech: c.speech };
  renderCallout(r);
  if (V.engine !== "ElevenLabs") return browserSay(c.speech);
  V.el.src = `/api/test12/voice/${r.run_no}/${c.n}.mp3`;
  V.el.play().catch((e) => {
    if (e.name === "NotAllowedError") { V.blocked = true; V.busy = false; noteVoice(); }
  });
}

// --- polling and controls ----------------------------------------------------------------------------------------
async function poll() {
  try {
    const r = await (await fetch("/api/test12/state", { cache: "no-store" })).json();
    if (r.phase && r.phase !== "IDLE") {
      S.run = r;
      renderStatus(r);
      renderSteps(r);
      renderReport(r);
      renderPolice(r);
      renderEvents(r);
      renderSummary(r);
      voiceTick(r);
      if (r.error) $("status").innerHTML += ` · <span style="color:#f05a4a">${esc(r.error)}</span>`;
    }
    $("start").disabled = false;
    $("stop").disabled = !r.running;
    draw();
    setTimeout(poll, r.running ? 250 : 1000);
  } catch (e) {
    $("status").textContent = "Server not reachable; retrying…";
    setTimeout(poll, 2000);
  }
}

async function start() {
  $("start").disabled = true;
  V.blocked = false;  // this click lets the page play sound
  primeAudio();
  noteVoice();
  const v = $("seed").value.trim();
  const body = v ? { seed: Number(v) } : {};
  if (S.spawn) body.spawn = S.spawn;
  $("status").textContent = "Starting… (the first run loads the map and YOLO)";
  $("summary-card").hidden = true;
  $("report").innerHTML = '<p class="muted">No position yet. The drone reports once its camera has found the person in the red shirt.</p>';
  $("rep-t").textContent = "";
  $("callout").hidden = true;
  $("police").textContent = "Police car: parked at the station.";
  try {
    const r = await (await fetch("/api/test12/start", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })).json();
    $("seed").placeholder = `random (last: ${r.seed})`;
  } catch (e) {
    $("status").textContent = "Could not start: " + e;
  }
}

function showView(view) {
  S.view = view;
  for (const b of document.querySelectorAll(".views button")) b.classList.toggle("on", b.dataset.view === view);
  $("cam-title").textContent = VIEW_TEXT[view][0];
  $("cam-note").textContent = VIEW_TEXT[view][1];
}

function showSpawn() {
  const sp = S.spawn;
  $("spawn-text").textContent = sp ? `base ${sgn(sp[0] - S.world.pad[0])} E, ${sgn(sp[1] - S.world.pad[1])} N` : "random";
  $("spawn-clear").hidden = !sp;
  draw();
}

$("start").addEventListener("click", start);
$("stop").addEventListener("click", () => fetch("/api/test12/stop", { method: "POST" }));
for (const id of ["t-belief", "t-survey", "t-truth"]) $(id).addEventListener("change", draw);
for (const b of document.querySelectorAll(".views button")) {
  b.addEventListener("click", async () => {
    showView(b.dataset.view);
    try {
      const r = await (await fetch("/api/test12/view", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ view: b.dataset.view }) })).json();
      showView(r.view);
    } catch (e) { /* the next poll shows what the server has */ }
  });
}
$("map").addEventListener("click", (ev) => {  // where the suspect starts in the next run
  if (!S.world) return;
  const c = $("map");
  const k = c.width / c.clientWidth;
  const [x, y] = tf().inv(ev.offsetX * k, ev.offsetY * k);
  const p = snapSidewalk(x, y);
  if (!p) return;
  S.spawn = [Math.round(p.x * 10) / 10, Math.round(p.y * 10) / 10];
  showSpawn();
});
$("spawn-clear").addEventListener("click", () => { S.spawn = null; showSpawn(); });
$("voice").addEventListener("change", (e) => {
  V.on = e.target.checked;
  if (!V.on) {
    V.el.pause();
    if ("speechSynthesis" in window) speechSynthesis.cancel();
    V.busy = false;
  }
});
document.addEventListener("click", () => { if (V.blocked) { V.blocked = false; primeAudio(); noteVoice(); } });
window.addEventListener("resize", resize);
$("video").addEventListener("error", () => setTimeout(() => { $("video").src = "/api/test12/stream.mjpg?" + Date.now(); }, 2000));
fetch("/api/test12/world").then((r) => r.json()).then((w) => { S.world = w; resize(); });
fetch("/api/test12/view").then((r) => r.json()).then((v) => showView(v.view)).catch(() => {});
fetch("/api/test12/voice").then((r) => r.json()).then((v) => { V.engine = v.elevenlabs ? "ElevenLabs" : "browser voice"; noteVoice(); })
  .catch(() => { V.engine = "browser voice"; noteVoice(); });
poll();
