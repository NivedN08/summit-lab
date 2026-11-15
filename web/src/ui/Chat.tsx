import { useEffect, useRef, useState, type CSSProperties } from "react";
import { ask, type Turn } from "../ask/client";
import type { Source } from "../ask/offline";
import { ROLE_LABEL, ROLE_READS, type Replay } from "../sim/replay";
import type { Target } from "../world/engine";
import { DEPT } from "../world/palette";
import { Avatar } from "./Avatar";

export type Msg = { role: "user" | "bot"; text: string; sources?: Source[]; mode?: string; model?: string; note?: string; pending?: boolean };

type Props = {
  R: Replay;
  t: number;
  server: boolean;
  agent: string;
  setAgent: (id: string) => void;
  people: string[];
  chats: Record<string, Msg[]>;
  setChats: (f: (c: Record<string, Msg[]>) => Record<string, Msg[]>) => void;
  select: (t: Target) => void;
};

const SUGGEST: Record<string, string[]> = {
  guide: ["How does the lab work?", "Why are the two teams kept blind?", "Which studies passed?", "What did the referee decide in gb10?"],
  director: ["What are you working on?", "Which agreed answer worries you most?", "What did you ask the teams?"],
  library: ["What are you reading?", "What should the analysts watch out for?", "What are you working on?"],
  "team-a": ["What are you working on?", "What have you found so far?", "What did Team B find?"],
  "team-b": ["What are you working on?", "What have you found so far?", "What did Team A find?"],
  "team-c": ["Why were you called in?", "What have you found so far?"],
  referee: ["What was the disagreement?", "How did you decide?"],
  solo: ["What are you working on?", "Why do you work alone?"],
};

export function Chat({ R, t, server, agent, setAgent, people, chats, setChats, select }: Props) {
  const [text, setText] = useState("");
  const msgsRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const a = R.agents.get(agent);
  const role = agent === "guide" ? null : a?.role ?? null;
  const msgs = chats[agent] ?? [];
  const busy = msgs.some((m) => m.pending);

  useEffect(() => {
    msgsRef.current?.scrollTo({ top: msgsRef.current.scrollHeight, behavior: "smooth" });
  }, [msgs.length, msgs[msgs.length - 1]?.pending]);

  useEffect(() => {
    inputRef.current?.focus();
  }, [agent]);

  const send = async (q: string) => {
    const question = q.trim();
    if (!question || busy) return;
    setText("");
    const history: Turn[] = msgs.filter((m) => !m.pending).map((m) => ({ role: m.role === "user" ? "user" : "assistant", text: m.text }));
    setChats((c) => ({ ...c, [agent]: [...(c[agent] ?? []), { role: "user", text: question }, { role: "bot", text: "", pending: true }] }));
    const res = await ask(R, server, agent, t, question, history);
    setChats((c) => {
      const list = [...(c[agent] ?? [])];
      list[list.length - 1] = { role: "bot", text: res.answer, sources: res.sources, mode: res.mode, model: res.model, note: res.note };
      return { ...c, [agent]: list };
    });
  };

  const openSource = (s: Source) => {
    const note = R.run.notes[s.id];
    if (note) return select({ type: "agent", id: note.kind === "pass_down" ? note.to ?? note.by : note.by });
    const m = s.id.match(/(gb\d\d|sc\d\d|bd\d\d)/);
    if (s.kind === "search") {
      const who = s.id.split("-").slice(2).join("-");
      if (R.agents.has(who)) return select({ type: "agent", id: who });
    }
    if (m) select({ type: "study", id: m[1] });
  };

  const pretty = (label: string) => label.replace(/\b(gb\d\d|sc\d\d|bd\d\d)-(team-[abc]|library|referee)\b/g, (m) => R.deptName(m));
  const title = agent === "guide" ? "Lab guide" : R.name(agent);
  const sub =
    agent === "guide"
      ? "Answers from the whole run log, including the grades. Not an agent."
      : `${ROLE_LABEL[role!]}${a?.study ? ` on ${a.study}` : ""}. ${ROLE_READS[role!]}`;
  const bandStyle = { "--band": role ? DEPT[role] : "var(--gold)" } as CSSProperties;
  const suggestions = SUGGEST[agent === "guide" ? "guide" : role ?? "guide"] ?? [];

  return (
    <div className="chat" style={bandStyle}>
      <div className="chat-who">
        {a ? <Avatar id={agent} role={a.role} scale={2} /> : <GuideIcon />}
        <div style={{ minWidth: 0 }}>
          <b>{title}</b>
          <small>{sub}</small>
        </div>
      </div>
      <div style={{ padding: "8px 16px 0" }}>
        <label className="sr-only" htmlFor="persona">
          Who to ask
        </label>
        <select id="persona" className="run-pick" style={{ width: "100%", display: "block" }} value={agent} onChange={(e) => setAgent(e.target.value)}>
          <option value="guide">Lab guide (knows the whole run)</option>
          {people.map((id) => {
            const p = R.agents.get(id);
            if (!p) return null;
            return (
              <option key={id} value={id}>
                {R.name(id)}: {ROLE_LABEL[p.role]}
                {p.study ? `, ${p.study}` : ""}
              </option>
            );
          })}
        </select>
      </div>
      <div className="msgs" ref={msgsRef} aria-live="polite">
        {!msgs.length && (
          <p className="empty" style={{ fontSize: 14 }}>
            {agent === "guide"
              ? "Ask anything about the lab or its research. Answers cite the notes they come from."
              : `${R.name(agent)} answers only from what they could read at this moment in the run. Move the timeline and ask again to hear what they knew then.`}
          </p>
        )}
        {msgs.map((m, i) =>
          m.pending ? (
            <div key={i} className="msg bot typing" aria-label="Thinking">
              <span />
              <span />
              <span />
            </div>
          ) : (
            <div key={i} className={`msg ${m.role}`}>
              {m.text}
              {m.sources && m.sources.length > 0 && (
                <div className="src">
                  {m.sources.slice(0, 6).map((s) => (
                    <button key={s.ref} onClick={() => openSource(s)} title={pretty(s.label)}>
                      [{s.ref}] {pretty(s.label)}
                    </button>
                  ))}
                </div>
              )}
              {m.role === "bot" && (
                <span className="mode">{m.note ?? (m.mode === "claude" ? `Answered by Claude from the run log` : "Answered from the run log, without a model")}</span>
              )}
            </div>
          ),
        )}
      </div>
      {msgs.length < 2 && (
        <div className="suggest">
          {suggestions.map((s) => (
            <button key={s} onClick={() => send(s)} disabled={busy}>
              {s}
            </button>
          ))}
        </div>
      )}
      <form
        className="compose"
        onSubmit={(e) => {
          e.preventDefault();
          send(text);
        }}
      >
        <label className="sr-only" htmlFor="ask">
          Your question
        </label>
        <textarea
          id="ask"
          ref={inputRef}
          rows={1}
          value={text}
          placeholder={agent === "guide" ? "Ask about the research" : `Ask ${R.name(agent)}`}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send(text);
            }
          }}
          maxLength={2000}
        />
        <button className="btn gold" type="submit" disabled={busy || !text.trim()}>
          Send
        </button>
      </form>
    </div>
  );
}

function GuideIcon() {
  return (
    <svg width="28" height="42" viewBox="0 0 14 21" shapeRendering="crispEdges" aria-hidden="true">
      <rect x="2" y="3" width="10" height="10" fill="#ffc95c" />
      <rect x="3" y="4" width="8" height="8" fill="#161a33" />
      <rect x="6" y="6" width="2" height="2" fill="#ffc95c" />
      <rect x="6" y="9" width="2" height="2" fill="#ffc95c" />
      <rect x="5" y="13" width="4" height="4" fill="#ffc95c" />
    </svg>
  );
}
