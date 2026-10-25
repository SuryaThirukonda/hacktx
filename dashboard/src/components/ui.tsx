import type { ReactNode } from "react";

export function Panel({ title, icon, right, children, className = "", bodyClass = "", id }: {
  title: ReactNode;
  icon?: ReactNode;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClass?: string;
  id?: string;
}) {
  return (
    <section id={id} className={`flex h-full min-h-0 min-w-0 flex-col rounded-lg border border-line bg-surface ${className}`}>
      <header className="flex h-9 shrink-0 items-center gap-2 border-b border-line px-3">
        {icon && <span className="text-ink-3">{icon}</span>}
        <h2 className="shrink-0 truncate text-[12px] font-semibold uppercase tracking-wide text-ink-2">{title}</h2>
        <div className="ml-auto flex min-w-0 shrink items-center gap-1.5 overflow-hidden whitespace-nowrap">{right}</div>
      </header>
      <div className={`relative min-h-0 flex-1 ${bodyClass}`}>{children}</div>
    </section>
  );
}

export function Badge({ children, color, solid = false, title, className = "" }: {
  children: ReactNode;
  color?: string;
  solid?: boolean;
  title?: string;
  className?: string;
}) {
  const c = color ?? "var(--ink-3)";
  return (
    <span
      title={title}
      className={`inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded px-1.5 py-[1px] text-[11px] font-semibold leading-4 ${className}`}
      style={solid ? { background: c, color: "#fff" } : { color: c, boxShadow: `inset 0 0 0 1px color-mix(in srgb, ${c} 55%, transparent)` }}
    >
      {children}
    </span>
  );
}

export function Dot({ color, pulse = false }: { color: string; pulse?: boolean }) {
  return (
    <span className="relative inline-flex h-2 w-2 shrink-0">
      {pulse && <span className="absolute inline-flex h-full w-full animate-ping rounded-full opacity-60" style={{ background: color }} />}
      <span className="relative inline-flex h-2 w-2 rounded-full" style={{ background: color }} />
    </span>
  );
}

export function Stat({ label, value, sub, accent, big = false }: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  accent?: string;
  big?: boolean;
}) {
  return (
    <div className="min-w-0">
      <div className="truncate text-[11px] uppercase tracking-wide text-ink-3">{label}</div>
      <div className={`tnum truncate font-semibold ${big ? "text-2xl" : "text-[15px]"}`} style={accent ? { color: accent } : undefined}>
        {value}
      </div>
      {sub && <div className="truncate text-[11px] text-ink-3">{sub}</div>}
    </div>
  );
}

export function Btn({ children, onClick, tone = "default", disabled, title, className = "", active = false }: {
  children: ReactNode;
  onClick?: () => void;
  tone?: "default" | "primary" | "danger" | "ghost";
  disabled?: boolean;
  title?: string;
  className?: string;
  active?: boolean;
}) {
  const tones: Record<string, string> = {
    default: "border-line-strong bg-surface-2 hover:bg-surface-3 text-ink-1",
    primary: "border-transparent bg-[var(--s1)] text-white hover:brightness-110",
    danger: "border-transparent bg-critical text-white hover:brightness-110",
    ghost: "border-transparent bg-transparent hover:bg-surface-3 text-ink-2",
  };
  return (
    <button
      type="button"
      title={title}
      disabled={disabled}
      onClick={onClick}
      className={`inline-flex h-7 items-center justify-center gap-1.5 rounded-md border px-2.5 text-[12px] font-medium transition disabled:cursor-not-allowed disabled:opacity-40 ${tones[tone]} ${active ? "ring-2 ring-[var(--s1)]" : ""} ${className}`}
    >
      {children}
    </button>
  );
}

export function Toggle({ checked, onChange, label, title }: { checked: boolean; onChange: (v: boolean) => void; label: string; title?: string }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      title={title}
      onClick={() => onChange(!checked)}
      className="flex w-full cursor-pointer select-none items-center gap-2 rounded text-left text-[12px] text-ink-2 hover:text-ink-1"
    >
      <span className={`relative inline-flex h-4 w-7 shrink-0 rounded-full transition ${checked ? "bg-[var(--s1)]" : "bg-surface-3"}`}>
        <span className={`absolute top-0.5 h-3 w-3 rounded-full bg-white transition ${checked ? "left-3.5" : "left-0.5"}`} />
      </span>
      <span className="truncate">{label}</span>
    </button>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="flex h-full items-center justify-center p-4 text-center text-[12px] text-ink-3">{children}</div>;
}

export function KV({ k, v, mono = false }: { k: string; v: ReactNode; mono?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-[3px] text-[12px]">
      <span className="shrink-0 text-ink-3">{k}</span>
      <span className={`tnum truncate text-right text-ink-1 ${mono ? "font-mono text-[11px]" : ""}`}>{v}</span>
    </div>
  );
}
