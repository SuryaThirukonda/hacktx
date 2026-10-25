import { Check, Mic, MicOff, Send, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { useStore } from "../lib/store";
import type { VoiceCommand, VoicePrimitive } from "../types/contracts";
import { Section } from "./mission/common";
import { Badge, Btn } from "./ui";

// the browser's speech-to-text (Chrome and Safari prefix it); speech stays on this machine's browser
interface SpeechRec {
  lang: string;
  interimResults: boolean;
  continuous: boolean;
  start: () => void;
  stop: () => void;
  onresult: ((e: { results: ArrayLike<ArrayLike<{ transcript: string }> & { isFinal: boolean }> }) => void) | null;
  onend: (() => void) | null;
  onerror: ((e: { error: string }) => void) | null;
}
const SpeechCtor = (): (new () => SpeechRec) | null => {
  const w = window as unknown as { SpeechRecognition?: new () => SpeechRec; webkitSpeechRecognition?: new () => SpeechRec };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
};

const STATUS: Record<VoiceCommand["status"], [string, string]> = {
  received: ["var(--ink-3)", "received"],
  parsing: ["var(--ink-3)", "parsing"],
  asking: ["var(--warning)", "question"],
  awaiting_approval: ["var(--warning)", "needs approval"],
  done: ["var(--good)", "done"],
  failed: ["var(--critical)", "failed"],
  declined: ["var(--ink-3)", "declined"],
  rejected: ["var(--ink-3)", "rejected"],
  not_found: ["var(--ink-3)", "not found"],
  ignored: ["var(--ink-3)", "ignored"],
};

/** Operator commands by voice (the browser's speech-to-text) or typed: the transcript, the parsed primitive and
 *  its confidence, the clarifying question with its options, and approve or reject for anything that moves. */
export function VoicePanel({ className = "" }: { className?: string }) {
  const v = useStore((s) => s.state?.voice ?? null);
  const showToast = useStore((s) => s.showToast);
  const [text, setText] = useState("");
  const [listening, setListening] = useState(false);
  const [heard, setHeard] = useState("");
  const rec = useRef<SpeechRec | null>(null);
  const canSpeak = !!SpeechCtor();
  useEffect(() => () => rec.current?.stop(), []);
  if (!v) return null;

  const run = (f: () => Promise<unknown>) => f().catch((e) => showToast(String((e as Error).message), "error"));
  const send = (t: string, source: string) => {
    if (!t.trim()) return;
    run(() => api.post("/api/voice/command", { text: t.trim(), source }));
    setText("");
  };
  const listen = () => {
    const C = SpeechCtor();
    if (!C) return;
    if (listening) {
      rec.current?.stop();
      return;
    }
    const r = new C();
    r.lang = "en-US";
    r.interimResults = true;
    r.continuous = false;
    r.onresult = (e) => {
      const res = e.results[e.results.length - 1];
      const t = res[0].transcript;
      setHeard(t);
      if (res.isFinal) send(t, "dashboard speech");
    };
    r.onend = () => setListening(false);
    r.onerror = (e) => {
      setListening(false);
      showToast(`Speech: ${e.error}`, "error");
    };
    rec.current = r;
    setHeard("");
    setListening(true);
    r.start();
  };
  const last = [...v.commands].reverse();
  return (
    <Section title="Voice commands" right={<Badge color="var(--s4)">MANUAL</Badge>} className={className}>
      <div className="flex flex-col gap-2 px-2.5 py-2 text-[12px]">
        <form
          className="flex items-center gap-1"
          onSubmit={(e) => {
            e.preventDefault();
            send(text, "typed");
          }}
        >
          {canSpeak && (
            <Btn tone={listening ? "primary" : "default"} onClick={listen} title={listening ? "Stop listening" : "Speak a command"}>
              {listening ? <MicOff size={13} /> : <Mic size={13} />}
            </Btn>
          )}
          <input
            value={listening ? heard : text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Follow the person in the red shirt"
            className="h-7 min-w-0 flex-1 rounded-md border border-line bg-surface-2 px-2 text-[12px] text-ink-1"
            aria-label="Command"
          />
          <Btn tone="primary" disabled={!text.trim()} title="Send the command" onClick={() => send(text, "typed")}>
            <Send size={13} />
          </Btn>
        </form>
        <div className="flex flex-wrap gap-1">
          {["Stop", "Follow the person in the red shirt", "Go to the house with the carport", "Search sector two", "Look for red shirt, jeans"].map((s) => (
            <button key={s} type="button" onClick={() => send(s, "typed")} className="rounded border border-line px-1.5 py-0.5 text-[10.5px] text-ink-2 hover:bg-surface-2">
              {s}
            </button>
          ))}
        </div>
        {v.question && (
          <div className="rounded-md border border-[var(--warning)] px-2 py-1.5">
            <div className="text-[12px] font-semibold text-[var(--warning)]">{v.question.question}</div>
            {v.question.options.length > 0 && (
              <div className="mt-1 flex flex-col gap-1">
                {v.question.options.map((o, i) => (
                  <button
                    key={o.id}
                    type="button"
                    onClick={() => run(() => api.post("/api/voice/answer", { option: i }))}
                    className="rounded bg-surface-2 px-2 py-1 text-left text-[11.5px] text-ink-1 hover:bg-surface-3"
                  >
                    {i + 1}. {o.label}
                  </button>
                ))}
              </div>
            )}
            <div className="mt-1 text-[10.5px] text-ink-3">Nothing moves until this is answered.</div>
          </div>
        )}
        {v.pending && <Pending p={v.pending} run={run} />}
        {last.length > 0 && (
          <ul className="flex flex-col divide-y divide-[var(--border)]">
            {last.slice(0, 4).map((c) => <CmdRow key={c.id} c={c} />)}
          </ul>
        )}
        <label className="flex items-center gap-1.5 text-[10.5px] text-ink-3">
          <input
            type="checkbox"
            checked={v.require_approval_sim}
            onChange={(e) => run(() => api.post("/api/voice/settings", { require_approval_sim: e.target.checked }))}
          />
          ask for approval of search and go-to in the simulator too (always for a real aircraft; always for follow)
        </label>
        <p className="text-[10.5px] leading-snug text-ink-3">
          The words become one verb from a closed set ({v.engine === "rules" ? "keyword grammar, no API call" : "decision model, grammar as baseline"}); below {Math.round(100 * v.gate)}% confidence it asks. Stop and return are immediate. Places and people are matched to named things on the map, never guessed.
        </p>
      </div>
    </Section>
  );
}

function Pending({ p, run }: { p: VoicePrimitive; run: (f: () => Promise<unknown>) => void }) {
  return (
    <div className="rounded-md border border-[var(--s1)] px-2 py-1.5">
      <div className="text-[12px]">
        <span className="font-semibold">{p.primitive}</span> <span className="text-ink-2">{summary(p)}</span>
      </div>
      <div className="text-[10.5px] text-ink-3">"{p.transcript}" · {p.safety_constraints.join(" · ")}</div>
      <div className="mt-1 flex gap-1">
        <Btn tone="primary" onClick={() => run(() => api.post(`/api/voice/primitives/${p.id}/approve`, {}))}>
          <Check size={13} /> Approve
        </Btn>
        <Btn tone="ghost" onClick={() => run(() => api.post(`/api/voice/primitives/${p.id}/reject`, {}))}>
          <X size={13} /> Reject
        </Btn>
      </div>
    </div>
  );
}

function summary(p: VoicePrimitive): string {
  const a = p.parameters as Record<string, unknown>;
  return String(a.who ?? a.label ?? a.descriptor ?? "");
}

function CmdRow({ c }: { c: VoiceCommand }) {
  const [col, label] = STATUS[c.status] ?? STATUS.received;
  const prim = c.primitive;
  return (
    <li className="py-1.5 text-[11.5px] leading-snug">
      <div className="flex items-baseline gap-1.5">
        <span className="min-w-0 flex-1 truncate text-ink-1">"{c.transcript}"</span>
        <Badge color={col}>{label}</Badge>
      </div>
      <div className="text-[10.5px] text-ink-3">
        {c.intent ? `${c.intent}${c.confidence != null ? ` ${Math.round(100 * c.confidence)}%` : ""}${c.engine ? ` · ${c.engine}` : ""}` : "…"}
        {prim ? ` → ${prim.primitive} ${summary(prim)} · approval ${prim.approval_status.replace("_", " ")} · ${prim.execution_status.replace("_", " ")}` : ""}
      </div>
      {c.reply && <div className="text-[10.5px] text-ink-2">{c.reply}</div>}
    </li>
  );
}
