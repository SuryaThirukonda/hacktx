import { Box, LayoutDashboard, MonitorPlay, PlayCircle, Shield, Smartphone } from "lucide-react";
import { useEffect } from "react";
import { BottomPanel } from "../components/mission/BottomPanel";
import { CenterView } from "../components/mission/CenterView";
import { ProvBadge } from "../components/mission/common";
import { LeftRail } from "../components/mission/LeftRail";
import { RightRail } from "../components/mission/RightRail";
import { Btn, Dot } from "../components/ui";
import { actions, loadHome, useIncident } from "../lib/incident";
import { useStore } from "../lib/store";
import { Disconnected, Toast } from "./Console";

/** The incident-command dashboard: mission and world on the left, the drone view in the centre, the map, review
 *  queue and alerts on the right, the replayable timeline at the bottom. Every control performs a real action. */
export function Mission() {
  const incident = useIncident((s) => s.incident);
  const mode = useStore((s) => s.state?.mode);
  const live = mode === "LIVE";
  const conn = useIncident((s) => s.conn);
  const showToast = useStore((s) => s.showToast);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", localStorage.getItem("overwatch.theme") === "light" ? "light" : "dark");
    document.title = "Overwatch · incident command";
    loadHome().catch((e) => showToast(String((e as Error).message), "error"));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const startDemo = async () => {
    try {
      await actions.startIncident("Training area A", "search", "DEMO-1");
      showToast("Demo incident started (SIMULATED)");
    } catch (e) {
      showToast(String((e as Error).message), "error");
    }
  };

  if (!mode) return <div className="p-6 text-ink-2">Connecting to backend…<Toast /></div>;

  return (
    <div className="flex h-full min-h-0 flex-col bg-page text-ink-1">
      <header className="flex h-11 shrink-0 items-center gap-2 border-b border-line bg-surface px-3">
        <Shield size={16} className="text-[var(--s1)]" />
        <span className="text-[13px] font-extrabold tracking-[0.14em]">OVERWATCH</span>
        <span className="hidden text-[12px] text-ink-3 xl:inline">incident command · research and training prototype</span>
        <div className="mx-2 h-5 w-px bg-[var(--border-strong)]" />
        {incident ? (
          <span className="flex min-w-0 items-center gap-1.5 text-[12.5px]">
            <span className="truncate font-semibold">{incident.title}</span>
            <ProvBadge p={incident.provenance} />
            <span className="hidden font-mono text-[11px] text-ink-3 lg:inline">{incident.id}</span>
          </span>
        ) : (
          <span className="text-[12.5px] text-ink-3">No active incident</span>
        )}
        <div className="ml-auto flex items-center gap-1.5">
          {!live && <Btn tone="primary" onClick={startDemo} title="Start the guaranteed demo incident (training area A, simulated)">
            <PlayCircle size={14} /> <span className="hidden whitespace-nowrap sm:inline">Start demo incident</span>
          </Btn>}
          {live && !incident && <Btn tone="primary" onClick={() => void actions.startIncident("Live drone follow", "follow", null).catch((e: Error) => showToast(e.message, "error"))}>Start live session</Btn>}
          <span className="hidden items-center gap-1 text-[11px] text-ink-3 sm:flex" title="incident event stream">
            <Dot color={conn === "open" ? "var(--good)" : "var(--warning)"} /> {incident ? conn : "idle"}
          </span>
          <a href="/" className="inline-flex h-7 items-center gap-1 rounded-md px-2 text-[12px] text-ink-2 hover:bg-surface-3" title="Simulation demo">
            <MonitorPlay size={13} /> <span className="hidden lg:inline">Demo</span>
          </a>
          <a href="/responder" className="inline-flex h-7 items-center gap-1 rounded-md px-2 text-[12px] text-ink-2 hover:bg-surface-3" title="Responder app">
            <Smartphone size={13} /> <span className="hidden lg:inline">Responder</span>
          </a>
          <a href="/3d" className="inline-flex h-7 items-center gap-1 rounded-md px-2 text-[12px] text-ink-2 hover:bg-surface-3" title="Full-screen 3D view">
            <Box size={13} /> <span className="hidden lg:inline">3D</span>
          </a>
          <a href="/ops" className="inline-flex h-7 items-center gap-1 rounded-md px-2 text-[12px] text-ink-2 hover:bg-surface-3" title="Sensor and fusion console">
            <LayoutDashboard size={13} /> <span className="hidden lg:inline">Ops console</span>
          </a>
        </div>
      </header>
      <Disconnected />
      <main className={`grid min-h-0 flex-1 grid-cols-1 gap-3 overflow-y-auto p-3 xl:overflow-hidden ${live ? "xl:grid-cols-[minmax(0,1fr)_400px] xl:grid-rows-[minmax(0,1fr)_220px]" : "xl:grid-cols-[264px_minmax(0,1fr)_392px] xl:grid-rows-[minmax(0,1fr)_200px]"}`}>
        {!live && <div className="order-2 xl:order-1 xl:row-span-2 xl:min-h-0 xl:overflow-y-auto">
          <LeftRail />
        </div>}
        <div className="order-1 h-[62vw] max-h-[620px] min-h-[300px] xl:order-2 xl:h-auto xl:max-h-none xl:min-h-0">
          <CenterView />
        </div>
        <div className={`order-3 xl:min-h-0 xl:overflow-y-auto xl:row-span-2`}>
          <RightRail />
        </div>
        {<div className="order-4 h-[220px] xl:h-auto xl:min-h-0">
          <BottomPanel />
        </div>}
      </main>
      <Toast />
    </div>
  );
}
