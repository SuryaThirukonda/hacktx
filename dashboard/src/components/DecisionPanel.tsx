import { Brain, ChevronDown, ChevronRight } from "lucide-react";
import { useState } from "react";
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "../lib/api";
import { clockTime, ms } from "../lib/format";
import { useStore } from "../lib/store";
import type { DecisionRecord } from "../types/contracts";
import { Badge, Empty, Panel } from "./ui";

const TASK_LABEL: Record<string, string> = {
  D1_reacquire: "D1 reacquire",
  D2_identity: "D2 identity",
  D3_describe: "D3 describe",
  D4_motion: "D4 motion",
  D5_priority: "D5 notify",
};

const STATUS_COLOR: Record<string, string> = {
  success: "var(--good)",
  fallback: "var(--warning)",
  timeout: "var(--critical)",
  error: "var(--critical)",
  refusal: "var(--serious)",
  skipped: "var(--ink-3)",
};

function ProbBars({ rec }: { rec: DecisionRecord }) {
  const a = rec.answers.find((x) => x.type === "choice");
  if (!a) return null;
  const entries = Object.entries(a.probabilities).sort((x, y) => y[1] - x[1]).slice(0, 6);
  return (
    <div className="mt-1 flex flex-col gap-0.5">
      {entries.map(([k, p]) => (
        <div key={k} className="flex items-center gap-2 text-[10px]">
          <span className={`w-32 truncate ${k === a.choice ? "font-semibold text-ink-1" : "text-ink-3"}`}>{k}</span>
          <div className="h-2 flex-1 rounded-full bg-surface-3">
            <div className="h-2 rounded-full" style={{ width: `${Math.max(2, p * 100)}%`, background: k === a.choice ? "var(--s7)" : "var(--axis)" }} />
          </div>
          <span className="tnum w-9 text-right text-ink-2">{(p * 100).toFixed(0)}%</span>
        </div>
      ))}
    </div>
  );
}

function Row({ r }: { r: DecisionRecord }) {
  const [open, setOpen] = useState(false);
  const a = r.answers.find((x) => x.type === "choice");
  const baseline = r.facts?._rules_baseline as string | undefined;
  const differs = baseline !== undefined && r.selected !== null && baseline !== r.selected && r.engine !== "rules";
  return (
    <li className={`px-3 py-1.5 ${r.shadow ? "opacity-70" : ""}`}>
      <button className="flex w-full items-start gap-2 text-left" onClick={() => setOpen(!open)}>
        {open ? <ChevronDown size={13} className="mt-0.5 shrink-0 text-ink-3" /> : <ChevronRight size={13} className="mt-0.5 shrink-0 text-ink-3" />}
        <span className="tnum mt-0.5 w-11 shrink-0 text-[11px] text-ink-3">{clockTime(r.request_ts_ms)}</span>
        <span className="w-[74px] shrink-0 text-[11px] font-semibold text-ink-2">{TASK_LABEL[r.task]}</span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5 text-[12px]">
            <span className="font-semibold" style={{ color: "var(--s7)" }}>{r.engine}{r.mock ? " (mock)" : ""}</span>
            {r.shadow && <Badge color="var(--ink-3)">shadow</Badge>}
            <Badge color={STATUS_COLOR[r.status]}>{r.status}</Badge>
            <span className="text-ink-1">{r.selected ?? "—"}</span>
            {a?.confidence !== null && a?.confidence !== undefined && <span className="tnum text-ink-3">conf {a.confidence.toFixed(2)}</span>}
            <span className="tnum text-ink-3">{ms(r.latency_ms)}</span>
            {differs && <Badge color="var(--warning)" title="deterministic rules would have chosen differently">rules: {baseline}</Badge>}
          </div>
          <div className={`truncate text-[10px] ${r.acted ? "text-good-ink" : "text-ink-3"}`}>{r.acted ? "ACTED" : "not acted"} · {r.reason}</div>
        </div>
      </button>
      {open && (
        <div className="ml-6 mt-1">
          <ProbBars rec={r} />
          {r.answers.filter((x) => x.type !== "choice").map((x) => (
            <div key={x.name} className="tnum text-[10px] text-ink-2">
              {x.name}: {x.type === "predicate" ? `p=${x.probability?.toFixed(2)}` : x.type === "score" ? `score ${x.score?.toFixed(2)}` : x.type}
            </div>
          ))}
          <pre className="scroll-thin mt-1 max-h-40 overflow-auto rounded bg-surface-2 p-1.5 text-[10px] leading-snug text-ink-2">{JSON.stringify(r.facts, null, 1)}</pre>
        </div>
      )}
    </li>
  );
}

export function DecisionPanel() {
  const ds = useStore((s) => s.state?.decisions);
  const decisions = useStore((s) => s.decisions);
  const [showShadow, setShowShadow] = useState(true);
  const engines = (ds?.engines ?? []).filter((e) => e.calls > 0).map((e) => ({ name: e.name + (e.mock ? "*" : ""), p50: e.p50_ms, p95: e.p95_ms }));
  const list = [...decisions].reverse().filter((d) => showShadow || !d.shadow).slice(0, 80);
  return (
    <Panel
      title="AI decisions"
      icon={<Brain size={14} />}
      right={
        ds && (
          <>
            {ds.ai_paused ? (
              <Badge color="var(--critical)" solid title={ds.pause_reason ?? ""}>AI PAUSED · RULES ACTIVE</Badge>
            ) : (
              <Badge color="var(--s7)">{ds.active === "mock" ? "MOCK (no API key)" : ds.active}</Badge>
            )}
            <select
              value={ds.primary}
              onChange={(e) => api.post("/api/decisions/engine", { primary: e.target.value }).catch(() => undefined)}
              className="h-6 rounded border border-line-strong bg-surface-2 px-1 text-[11px] text-ink-2"
              title="Primary decision engine"
            >
              {["openai", "typesafe", "strands", "mock", "rules"].map((n) => (
                <option key={n} value={n}>{n}</option>
              ))}
            </select>
          </>
        )
      }
      bodyClass="flex flex-col"
    >
      {!ds ? (
        <Empty>Waiting…</Empty>
      ) : (
        <>
          <div className="flex shrink-0 items-stretch gap-3 border-b border-line px-3 py-2">
            <div className="flex shrink-0 flex-col justify-center gap-0.5 text-[11px]">
              <span className="text-ink-3">calls <b className="tnum text-ink-1">{ds.calls_total}</b></span>
              <span className="text-ink-3">acted <b className="tnum text-ink-1">{ds.acted_total}</b></span>
              <span className="text-ink-3" title="decisions where the model's choice differed from the deterministic rules baseline">
                vs rules <b className="tnum text-ink-1">{ds.disagreements}</b> diff
              </span>
              <label className="flex items-center gap-1 text-ink-3">
                <input type="checkbox" checked={showShadow} onChange={(e) => setShowShadow(e.target.checked)} /> shadow
              </label>
            </div>
            <div className="h-[92px] min-w-0 flex-1">
              {engines.length ? (
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={engines} margin={{ top: 4, right: 4, bottom: 0, left: -8 }} barGap={2}>
                    <CartesianGrid stroke="var(--grid)" vertical={false} />
                    <XAxis dataKey="name" tick={{ fontSize: 10, fill: "var(--ink-3)" }} stroke="var(--axis)" />
                    <YAxis tick={{ fontSize: 10, fill: "var(--ink-3)" }} stroke="var(--axis)" width={40} tickFormatter={(v: number) => `${v}`} />
                    <Tooltip contentStyle={{ background: "var(--surface)", border: "1px solid var(--border-strong)", fontSize: 11, color: "var(--ink-1)" }} cursor={{ fill: "var(--surface-3)" }} />
                    <Legend wrapperStyle={{ fontSize: 10 }} iconSize={8} formatter={(v: string) => `${v} (ms)`} />
                    <Bar dataKey="p50" name="p50 latency" fill="var(--s7)" radius={[4, 4, 0, 0]} isAnimationActive={false} />
                    <Bar dataKey="p95" name="p95 latency" fill="var(--s1)" radius={[4, 4, 0, 0]} isAnimationActive={false} />
                  </BarChart>
                </ResponsiveContainer>
              ) : (
                <Empty>No decision calls yet</Empty>
              )}
            </div>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto scroll-thin">
            {list.length === 0 ? <Empty>No decisions yet.</Empty> : <ul className="divide-y divide-[var(--border)]">{list.map((r) => <Row key={r.decision_id} r={r} />)}</ul>}
          </div>
          <div className="shrink-0 border-t border-line px-3 py-1 text-[10px] text-ink-3">
            Models choose among bounded options only; critical alerts, geofence and speed limits are deterministic. * = mock engine.
          </div>
        </>
      )}
    </Panel>
  );
}
