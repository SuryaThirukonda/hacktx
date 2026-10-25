import { AlertBanner } from "../components/AlertBanner";
import { AlertsTimeline } from "../components/AlertsTimeline";
import { DecisionPanel } from "../components/DecisionPanel";
import { RightColumn } from "../components/RightPanels";
import { TopBar } from "../components/TopBar";
import { TrackInspector } from "../components/TrackInspector";
import { DroneCard, MapCard, PhoneCard } from "../components/ViewCards";
import { useStore } from "../lib/store";

export function Toast() {
  const t = useStore((s) => s.toast);
  if (!t) return null;
  return (
    <div className="pointer-events-none fixed bottom-4 left-1/2 z-50 -translate-x-1/2 rounded-md border px-3 py-2 text-[13px] shadow-xl"
      style={{ background: "var(--surface)", borderColor: t.kind === "error" ? "var(--critical)" : "var(--border-strong)", color: t.kind === "error" ? "var(--critical)" : "var(--ink-1)" }}>
      {t.text}
    </div>
  );
}

export function Disconnected() {
  const conn = useStore((s) => s.stateConn);
  const st = useStore((s) => s.state);
  if (conn === "open" && st) return null;
  return (
    <div className="pointer-events-none fixed inset-x-0 top-12 z-40 flex justify-center">
      <div className="rounded-b-md bg-[var(--warning)] px-3 py-1 text-[12px] font-semibold text-[#1a1a19]">
        {conn === "open" ? "Connected: waiting for first state…" : "Backend not reachable: reconnecting… (start it with `make backend`)"}
      </div>
    </div>
  );
}

export function Console() {
  return (
    <div className="flex h-full flex-col">
      <TopBar />
      <AlertBanner />
      <Disconnected />
      <main className="grid min-h-0 flex-1 grid-cols-1 gap-2 overflow-y-auto p-2 lg:grid-cols-2 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,1.3fr)_312px] xl:grid-rows-[auto_minmax(0,1fr)] xl:overflow-hidden">
        <div><DroneCard /></div>
        <div><PhoneCard /></div>
        <div className="lg:col-span-2 xl:col-span-1"><MapCard /></div>
        <div className="h-[640px] min-h-0 lg:col-span-2 lg:h-[560px] xl:col-span-1 xl:row-span-2 xl:h-auto"><RightColumn /></div>
        <div className="h-[520px] min-h-0 lg:h-[440px] xl:h-auto"><TrackInspector /></div>
        <div className="h-[420px] min-h-0 lg:h-[440px] xl:h-auto"><AlertsTimeline /></div>
        <div className="h-[460px] min-h-0 lg:col-span-2 lg:h-[440px] xl:col-span-1 xl:h-auto"><DecisionPanel /></div>
      </main>
      <Toast />
    </div>
  );
}
