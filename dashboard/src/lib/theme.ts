import { useEffect } from "react";
import { create } from "zustand";
export type Theme = "light" | "navy";
function saved(): Theme { try { return localStorage.getItem("overwatch.theme") === "light" ? "light" : "navy"; } catch { return "navy"; }
}
const useThemeStore = create<{ theme: Theme; toggle: () => void }>((set) => ({ theme: saved(), toggle: () => set((s) => ({ theme: s.theme === "light" ? "navy" : "light" })),
}));
/** One saved palette across the simulation launcher, incident command and operations console. */
export function useTheme(): [Theme, () => void] { const theme = useThemeStore((s) => s.theme), toggle = useThemeStore((s) => s.toggle); useEffect(() => { document.documentElement.dataset.theme = theme; try { localStorage.setItem("overwatch.theme", theme); } catch { /* private browsing */ } }, [theme]); return [theme, toggle];
}
