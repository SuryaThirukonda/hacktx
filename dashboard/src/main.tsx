import { StrictMode, Suspense, lazy } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import { usePath, viewFromPath } from "./lib/router";
import { connectStreams } from "./lib/store";
import { Console, Disconnected, Toast } from "./pages/Console";
import { Demo } from "./pages/Demo";
import { Drive } from "./pages/Drive";
import { Mission } from "./pages/Mission";
import { Responder } from "./pages/Responder";
import { ResponderSensors } from "./pages/ResponderSensors";
import { ViewPage } from "./pages/ViewPage";

const Sim3D = lazy(() => import("./pages/Sim3D").then((m) => ({ default: m.Sim3D }))); // three.js only loads here

// /            simulation demo (pick a scenario, watch the drone)
// /dashboard   full mission dashboard (incident command)
// /drive       phone controller for a simulated person (the chase target), through the Cloudflare tunnel
// /ops/...     sensor and fusion console (overview, drone, phone, map pages)
// /responder   responder PWA (incident alerts);  /responder/sensors  phone-sensor proximity view;  /3d  3D view
const entry = window.location.pathname;
const page = entry.startsWith("/responder/sensors") ? "sensors" : entry.startsWith("/responder") ? "responder" : entry.startsWith("/3d") ? "3d" : entry.startsWith("/ops") ? "ops" : entry.startsWith("/dashboard") ? "mission" : entry.startsWith("/drive") ? "drive" : "demo";
if (page === "ops" || page === "mission" || page === "demo") connectStreams();

/** Ops console routes share one set of live sockets. */
function OpsApp() {
  const view = viewFromPath(usePath());
  return view ? (
    <ViewPage view={view} overlays={<><Disconnected /><Toast /></>} />
  ) : (
    <Console />
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    {page === "sensors" ? (
      <ResponderSensors />
    ) : page === "responder" ? (
      <Responder />
    ) : page === "3d" ? (
      <Suspense fallback={null}>
        <Sim3D />
      </Suspense>
    ) : page === "ops" ? (
      <OpsApp />
    ) : page === "mission" ? (
      <Mission />
    ) : page === "drive" ? (
      <Drive />
    ) : (
      <Demo />
    )}
  </StrictMode>,
);
