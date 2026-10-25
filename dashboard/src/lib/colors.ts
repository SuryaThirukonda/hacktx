// Resolve design tokens for canvas drawing (canvas can't read CSS variables directly).
let cache: Record<string, string> = {};
let cacheAt = 0;

export function cssVar(name: string): string {
  const now = performance.now();
  if (now - cacheAt > 1000) {
    cache = {};
    cacheAt = now;
  }
  if (!(name in cache)) {
    cache[name] = getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#888";
  }
  return cache[name];
}

export const ROLE_VAR: Record<string, string> = { subject: "--s2", responder: "--s1" };

export function roleColor(role: string | null | undefined): string {
  return role ? cssVar(ROLE_VAR[role] ?? "--ink-2") : cssVar("--ink-2");
}

export function withAlpha(hex: string, a: number): string {
  if (!hex.startsWith("#") || hex.length < 7) return hex;
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return `rgba(${r},${g},${b},${a})`;
}
