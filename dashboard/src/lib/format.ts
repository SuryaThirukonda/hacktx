// Formatting helpers. Unknown values render as an em dash, never as 0.

export const DASH = "—";

export function num(v: number | null | undefined, digits = 1, unit = ""): string {
  if (v === null || v === undefined || Number.isNaN(v)) return DASH;
  return `${v.toFixed(digits)}${unit}`;
}

export function mps(v: number | null | undefined): string {
  return num(v, 1, " m/s");
}

export function mph(v: number | null | undefined): string {
  if (v === null || v === undefined) return DASH;
  return `${(v * 2.236936).toFixed(1)} mph`;
}

export function meters(v: number | null | undefined, digits = 1): string {
  return num(v, digits, " m");
}

export function secs(ms: number | null | undefined, digits = 1): string {
  if (ms === null || ms === undefined) return DASH;
  return `${(ms / 1000).toFixed(digits)} s`;
}

export function ms(v: number | null | undefined): string {
  if (v === null || v === undefined) return DASH;
  return v >= 100 ? `${v.toFixed(0)} ms` : `${v.toFixed(1)} ms`;
}

export function clockTime(tMs: number): string {
  const s = Math.max(0, tMs / 1000);
  const m = Math.floor(s / 60);
  return `${m}:${(s % 60).toFixed(1).padStart(4, "0")}`;
}

export function shortId(trackId: string | null | undefined): string {
  if (!trackId) return DASH;
  const parts = trackId.split(":");
  return parts.length >= 2 ? `#${parts[1]}` : trackId;
}

export function headingName(deg: number | null | undefined): string {
  if (deg === null || deg === undefined) return DASH;
  const names = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];
  return `${names[Math.round(((deg % 360) + 360) % 360 / 45) % 8]} ${deg.toFixed(0)}°`;
}

export function pct(v: number | null | undefined, digits = 0): string {
  if (v === null || v === undefined) return DASH;
  return `${(v * 100).toFixed(digits)}%`;
}
