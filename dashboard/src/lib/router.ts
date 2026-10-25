// Minimal client-side routing for the ops console pages (/ops, /ops/drone, /ops/phone, /ops/map). Navigating with pushState keeps
// the state, event and video sockets connected; /responder and /3d are separate entry points with full loads.
import { useSyncExternalStore } from "react";

export type ViewKey = "drone" | "phone" | "map";
export const VIEW_PATHS: Record<ViewKey, string> = { drone: "/ops/drone", phone: "/ops/phone", map: "/ops/map" };
export const OPS_HOME = "/ops";

const listeners = new Set<() => void>();

function subscribe(cb: () => void) {
  listeners.add(cb);
  window.addEventListener("popstate", cb);
  return () => {
    listeners.delete(cb);
    window.removeEventListener("popstate", cb);
  };
}

export function navigate(path: string) {
  if (path === window.location.pathname) return;
  window.history.pushState(null, "", path + window.location.search);
  listeners.forEach((l) => l());
}

export function usePath(): string {
  return useSyncExternalStore(subscribe, () => window.location.pathname);
}

export function viewFromPath(path: string): ViewKey | null {
  const hit = (Object.entries(VIEW_PATHS) as [ViewKey, string][]).find(([, p]) => path.startsWith(p));
  return hit ? hit[0] : null;
}
