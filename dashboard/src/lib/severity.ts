import type { Severity } from "../types/contracts";

// Reserved status palette (always rendered with an icon + label, never color alone).
export const SEVERITY: Record<Severity, { label: string; color: string; ink: string; rank: number }> = {
  critical: { label: "CRITICAL", color: "var(--critical)", ink: "#ffffff", rank: 0 },
  local_proximity: { label: "PROXIMITY", color: "var(--serious)", ink: "#1a1a19", rank: 1 },
  warning: { label: "WARNING", color: "var(--warning)", ink: "#1a1a19", rank: 2 },
  sensor_fault: { label: "SENSOR", color: "var(--warning)", ink: "#1a1a19", rank: 3 },
  attention: { label: "ATTENTION", color: "var(--serious)", ink: "#1a1a19", rank: 4 },
  info: { label: "INFO", color: "var(--s1)", ink: "#ffffff", rank: 5 },
};

export const STATUS_COLOR: Record<string, string> = {
  OK: "var(--good)",
  STALE: "var(--warning)",
  DEGRADED: "var(--warning)",
  OFFLINE: "var(--critical)",
  NOT_CONFIGURED: "var(--ink-3)",
  PAUSED: "var(--ink-3)",
};
