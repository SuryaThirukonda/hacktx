// Full page for one of the three main views, opened from the overview cards.
import { ArrowLeft, Box, Camera, Map as MapIcon, Plane } from "lucide-react";
import type { ReactNode } from "react";
import { AlertBanner } from "../components/AlertBanner";
import { AlertsTimeline } from "../components/AlertsTimeline";
import { DecisionPanel } from "../components/DecisionPanel";
import { DroneFeed, PhoneFeed } from "../components/Feeds";
import { MapPanel } from "../components/MapPanel";
import { TopBar } from "../components/TopBar";
import { TrackInspector } from "../components/TrackInspector";
import { navigate, OPS_HOME, VIEW_PATHS, type ViewKey } from "../lib/router";

const TABS: { key: ViewKey; label: string; icon: ReactNode }[] = [
  { key: "drone", label: "Drone · aerial", icon: <Plane size={14} /> },
  { key: "phone", label: "Responder · phone + LiDAR", icon: <Camera size={14} /> },
  { key: "map", label: "Fused local scene", icon: <MapIcon size={14} /> },
];

export function ViewPage({ view, overlays }: { view: ViewKey; overlays: ReactNode }) {
  return (
    <div className="flex h-full flex-col">
      <TopBar />
      <AlertBanner />
      {overlays}
      <nav className="flex flex-wrap items-center gap-1 border-b border-line bg-surface px-2 py-1.5" aria-label="Views">
        <button type="button" onClick={() => navigate(OPS_HOME)} className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-[12px] font-semibold text-ink-2 hover:bg-surface-3">
          <ArrowLeft size={14} /> Overview
        </button>
        <span className="mx-1 h-4 w-px bg-[var(--border-strong)]" />
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            onClick={() => navigate(VIEW_PATHS[t.key])}
            aria-current={t.key === view ? "page" : undefined}
            className={`inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-[12px] font-semibold ${t.key === view ? "bg-surface-3 text-ink-1" : "text-ink-2 hover:bg-surface-2"}`}
          >
            {t.icon} {t.label}
          </button>
        ))}
        <a href="/3d" className="ml-auto inline-flex items-center gap-1 rounded-md px-2 py-1 text-[12px] font-semibold text-ink-2 hover:bg-surface-3">
          <Box size={14} /> 3D view
        </a>
      </nav>
      <main className="grid min-h-0 flex-1 grid-cols-1 gap-2 overflow-y-auto p-2 xl:grid-cols-[minmax(0,1fr)_380px] xl:overflow-hidden">
        <div className="h-[70vw] shrink-0 xl:h-auto xl:min-h-0">
          {view === "drone" ? <DroneFeed /> : view === "phone" ? <PhoneFeed /> : <MapPanel />}
        </div>
        <div className="flex flex-col gap-2 xl:grid xl:min-h-0 xl:grid-rows-[minmax(0,1fr)_minmax(0,1fr)]">
          <div className="h-[460px] shrink-0 xl:h-auto xl:min-h-0">{view === "phone" ? <AlertsTimeline /> : <TrackInspector />}</div>
          <div className="h-[420px] shrink-0 xl:h-auto xl:min-h-0">{view === "drone" ? <DecisionPanel /> : view === "phone" ? <TrackInspector /> : <AlertsTimeline />}</div>
        </div>
      </main>
    </div>
  );
}
