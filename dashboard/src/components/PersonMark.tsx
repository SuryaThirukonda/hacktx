import { useCallback, useEffect, useRef, useState } from "react";
import { api, withToken } from "../lib/api";

interface Mark {
  request_id?: string; description?: string; status: string; reason?: string;
  confidence?: number | null; engine?: string; auto_approved?: boolean; preview_available?: boolean;
  can_start_follow?: boolean;
  movement_blocker?: string | null;
  follow?: { status: string; text: string; track_id?: string | null };
  autonomy?: { mode: string; motion_profile: string; note?: string | null };
}

/** Shared responder/dashboard view: one mark, separate movement start, bounded push-to-talk. */
export function PersonMark({ incidentId, announce }: { incidentId: string; announce?: (text: string) => void }) {
  const base = `/api/incidents/${encodeURIComponent(incidentId)}/person-mark`;
  const [text, setText] = useState("");
  const [mark, setMark] = useState<Mark | null>(null);
  const [busy, setBusy] = useState(false);
  const [recording, setRecording] = useState(false);
  const [error, setError] = useState("");
  const [voiceStatus, setVoiceStatus] = useState("");
  const [chat, setChat] = useState<{role:"user"|"assistant"; text:string}[]>([]);
  const append = (role: "user"|"assistant", value: string) => setChat((items) => [...items.slice(-19), {role, text:value}]);
  const current = useRef(base); current.current = base;
  const pressed = useRef(false);
  const recorder = useRef<MediaRecorder | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const discard = useRef(false);
  const timer = useRef<number | undefined>(undefined);
  const generation = useRef(0);
  const announced = useRef("");
  const say = useRef(announce); say.current = announce;
  const micAvailable = window.isSecureContext && Boolean(navigator.mediaDevices?.getUserMedia) && typeof MediaRecorder !== "undefined";
  const refresh = useCallback(async () => {
    const gen = generation.current;
    try {
      const next = await api.get<Mark>(base);
      if (current.current !== base || gen !== generation.current) return;
      setMark(next);
      const signature = `${next.request_id}:${next.status}`;
      if (signature !== announced.current && ["approved", "review", "no_match", "error"].includes(next.status)) {
        announced.current = signature;
        say.current?.(`${next.status === "approved" ? "Person marked" : next.status === "review" ? "Please review the match" : "No approved match"}.
          ${next.confidence != null ? `Model confidence ${Math.floor(next.confidence * 1000) / 10} percent.` : ""}`);
        const confidence = next.confidence != null ? ` Model confidence ${(Math.floor(next.confidence*1000)/10).toFixed(1)}%.` : "";
        append("assistant", `${next.reason ?? next.status}${confidence}${next.status === "approved" ? " Press Start follow separately to move the aircraft." : ""}`);
      }
    } catch (e) { setError((e as Error).message); }
  }, [base]);
  const stopRecording = (cancel = false) => {
    pressed.current = false;
    discard.current ||= cancel;
    window.clearTimeout(timer.current);
    if (recorder.current?.state === "recording") recorder.current.stop();
    stream.current?.getTracks().forEach((t) => t.stop());
    setRecording(false);
  };
  useEffect(() => {
    setMark(null); setChat([]); setError(""); announced.current = ""; generation.current++;
    void refresh();
    const interval = window.setInterval(() => { void refresh(); }, 1000);
    const hide = () => { if (document.hidden) stopRecording(true); };
    const blur = () => stopRecording(true);
    document.addEventListener("visibilitychange", hide); window.addEventListener("blur", blur);
    return () => {
      window.clearInterval(interval); stopRecording(true);
      document.removeEventListener("visibilitychange", hide); window.removeEventListener("blur", blur);
    };
  }, [refresh]);
  const submit = async (description: string) => {
    if (!description.trim()) return;
    generation.current++; setBusy(true); setError("");
    append("user", description.trim());
    try {
      const command = description.toLowerCase().trim().replace(/[.!?]+$/, "");
      if (/^(stop|stop moving|stop movement|stop following|manual|pilot control)$/.test(command)) {
        await api.post("/api/autonomy/mode", {mode:"MANUAL"});
        append("assistant", "Movement stop requested. Backend is in MANUAL; RC control has priority. Follow will not restart automatically.");
        return;
      }
      if (/^(clear mark|cancel request|release target)$/.test(command)) {
        setMark(await api.post<Mark>(`${base}/release`)); append("assistant", "Mark cleared and pending matching cancelled."); return;
      }
      if (/\b(status|ready|why|not moving|isn.t moving|not turning)\b/.test(command)) {
        const status = await api.get<{autonomy:{mode:string};blockers:string[]}>("/api/autonomy/readiness");
        append("assistant", `Aircraft mode: ${status.autonomy.mode}. ${status.blockers.length ? status.blockers.join(". ") : "The control path is ready."}`); return;
      }
      if (/^(help|what can you do|start follow|start tracking)$/.test(command)) {
        append("assistant", "Describe one person's visible clothing, for example: track the person wearing a blue sweatshirt and black pants. Muse compares visible candidate crops; clothing rules are the offline fallback. You can also ask status, say stop, or clear mark. After a verified mark, use the separate Start follow button for aircraft movement."); return;
      }
      const result = await api.post<Mark>(base, { text: description.trim() });
      if (current.current === base) { setMark(result); setText(description); }
    } catch (e) { if (current.current === base) { setError((e as Error).message); append("assistant", `Request not completed: ${(e as Error).message}`); } }
    finally { if (current.current === base) setBusy(false); }
  };
  const startRecording = async () => {
    if (!micAvailable || busy || pressed.current) return;
    pressed.current = true; discard.current = false;
    setError(""); setVoiceStatus("Allow microphone access, then hold to speak.");
    try {
      const input = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (!pressed.current || current.current !== base) { input.getTracks().forEach((t) => t.stop()); return; }
      stream.current = input;
      const mime = ["audio/mp4", "audio/webm;codecs=opus", "audio/webm"].find((m) => MediaRecorder.isTypeSupported(m));
      if (!mime) throw new Error("This browser cannot record a supported audio format. Type your description.");
      const rec = new MediaRecorder(input, { mimeType: mime }); recorder.current = rec;
      const chunks: Blob[] = [];
      rec.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
      rec.onstop = async () => {
        input.getTracks().forEach((t) => t.stop());
        if (discard.current || current.current !== base) { setVoiceStatus("Recording cancelled."); return; }
        const audio = new Blob(chunks, { type: mime });
        if (!audio.size || audio.size > 4 * 1024 * 1024) { setError("Record a short command under 20 seconds."); return; }
        setBusy(true); setVoiceStatus("Transcribing…");
        try {
          const result = await api.postRaw<{ text: string }>(`${base}/transcribe?format=${mime.startsWith("audio/mp4") ? "mp4" : "webm"}`, audio);
          if (current.current !== base || discard.current) return;
          setVoiceStatus(`Heard: “${result.text}”`);
          await submit(result.text);
        } catch (e) { if (current.current === base) setError((e as Error).message); }
        finally { if (current.current === base) setBusy(false); }
      };
      rec.onerror = () => { setError("Recording failed. Type your description or try again."); stopRecording(true); };
      rec.start(); setRecording(true); setVoiceStatus("Listening · release to send");
      timer.current = window.setTimeout(() => stopRecording(), 20000);
    } catch (e) { stopRecording(true); setError((e as Error).message); }
  };
  const act = async (path: string, body: unknown = {}) => {
    setBusy(true); setError(""); generation.current++;
    try { await api.post(path, body); await refresh(); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  };
  const button = "rounded-lg border border-line bg-surface-2 px-3 py-2 font-semibold disabled:opacity-40";
  return <section className="mt-3 rounded-xl border border-line bg-surface p-3 text-[13px]" aria-label="Eye in the Sky assistant">
    <h2 className="font-bold">Chat with Eye in the Sky · one person of interest</h2>
    <p className="mt-1 text-ink-3">Describe visible clothing. Muse proposes a visible match. Simulation can select it automatically; live aircraft require human confirmation.</p>
    <div role="log" aria-label="Eye in the Sky conversation" className="mt-2 max-h-56 space-y-2 overflow-y-auto">
      {chat.map((message, index) => <p key={index} className={`rounded p-2 ${message.role === "user" ? "bg-surface-2" : "border border-line"}`}><b>{message.role === "user" ? "You" : "Eye in the Sky"}:</b> {message.text}</p>)}
    </div>
    <form className="mt-2 flex flex-col gap-2" onSubmit={(e) => { e.preventDefault(); void submit(text); }}>
      <input aria-label="Person clothing description" maxLength={500} value={text} onChange={(e) => setText(e.target.value)}
        placeholder="Hey, track the person in a blue sweatshirt and black pants" className="min-w-0 rounded border border-line bg-surface-2 p-2" />
      <div className="flex flex-wrap gap-2">
        <button type="submit" className={button} disabled={busy || recording || !text.trim()}>Send</button>
        <button type="button" className={button} disabled={!micAvailable || busy}
          style={{ touchAction: "none", userSelect: "none" }} onContextMenu={(e) => e.preventDefault()}
          onPointerDown={(e) => { e.preventDefault(); e.currentTarget.setPointerCapture(e.pointerId); void startRecording(); }}
          onPointerUp={() => stopRecording()} onPointerCancel={() => stopRecording(true)}
          onLostPointerCapture={() => { if (pressed.current) stopRecording(true); }}
          onKeyDown={(e) => { if ((e.key === " " || e.key === "Enter") && !e.repeat) { e.preventDefault(); void startRecording(); } }}
          onKeyUp={(e) => { if (e.key === " " || e.key === "Enter") { e.preventDefault(); stopRecording(); } }}>
          {recording ? "Listening… release to send" : "Hold to talk"}</button>
      </div>
    </form>
    {!micAvailable && <p className="mt-2 text-ink-3">Microphone access needs HTTPS on your phone (or localhost on the Mac). Text works here.</p>}
    {voiceStatus && <p className="mt-2 text-ink-2">{voiceStatus}</p>}
    {error && <p role="alert" className="mt-2 text-[var(--serious)]">{error}</p>}
    {mark && mark.status !== "idle" && <div className="mt-3 space-y-2" aria-live="polite">
      <p className="font-semibold">{mark.status === "approved" ? "Person marked" : mark.status.replaceAll("_", " ")}
        {mark.confidence != null ? ` · ${(Math.floor(mark.confidence * 1000) / 10).toFixed(1)}% model confidence` : " · confidence not provided"}
        {mark.auto_approved ? " · auto-approved" : ""}</p>
      <p>{mark.engine === "muse" ? "Muse Spark" : mark.engine?.includes("clothing_rules") ? "Clothing rules · fallback" : mark.engine} · {mark.reason}</p>
      {mark.preview_available && <figure><img className="max-h-40 rounded" alt="Proposed person's clothing at request time"
        src={withToken(`${base}/preview?request_id=${encodeURIComponent(mark.request_id!)}`)} /><figcaption className="text-[11px] text-ink-3">Request-time preview · not live video</figcaption></figure>}
      {mark.status === "approved" && <p>{mark.follow?.text} · {mark.autonomy?.motion_profile === "camera_only" ? "Yaw + camera tilt" : mark.autonomy?.motion_profile === "visual_follow" ? "Horizontal movement + camera pitch" : "Full-motion follow"} · {mark.autonomy?.mode}</p>}
      <div className="flex flex-wrap gap-2">
        {mark.status === "review" && mark.preview_available && <button className={button} disabled={busy} onClick={() => void act(`${base}/confirm`, { request_id: mark.request_id })}>Confirm this person</button>}
        {mark.status === "approved" && <button className={button} disabled={busy || !mark.can_start_follow}
          onClick={() => void act(`${base}/follow`, { request_id: mark.request_id })}>Start {mark.autonomy?.motion_profile === "camera_only" ? "camera" : "aircraft"} follow</button>}
        {["FOLLOW", "HOLD"].includes(mark.autonomy?.mode ?? "") && <button className={button} onClick={() => void act("/api/autonomy/mode", { mode: "MANUAL" })}>Stop movement · pilot control</button>}
        <button className={button} disabled={busy} onClick={() => void act(`${base}/release`)}>Clear mark / cancel request</button>
      </div>
      {mark.status === "approved" && !mark.can_start_follow && <p className="text-ink-3">{mark.movement_blocker} RC input always cancels movement.</p>}
    </div>}
    <p className="mt-2 text-[11px] text-ink-3">Confidence is the model's score, not a measured accuracy guarantee. Only one person is marked. Finer clothing details may require visual review. Clothing crops are sent to Meta when Muse is configured. Voice audio is sent to OpenAI for transcription.</p>
  </section>;
}
