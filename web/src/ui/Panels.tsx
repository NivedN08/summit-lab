import { useState, type CSSProperties, type ReactNode } from "react";
import type { Note, Role } from "../data/types";
import { fmtElapsed, MODEL_NAME, ROLE_READS, roleLabel, roundWord, statusWord, type Replay } from "../sim/replay";
import type { Target } from "../world/engine";
import { colorOf, headDept } from "../world/palette";
import { Avatar } from "./Avatar";

type Nav = {
  R: Replay;
  t: number;
  select: (t: Target) => void;
  talk: (agent: string) => void;
  filter: (study: string | null) => void;
};

const band = (role: Role, id = "") => ({ "--band": colorOf(role, id) }) as CSSProperties;

function Lock() {
  return (
    <svg width="12" height="13" viewBox="0 0 6 7" shapeRendering="crispEdges" aria-hidden="true">
      <path d="M1 3h4v4H1zM2 1h2v1H2zM1 2h1v1H1zM4 2h1v1H4z" fill="currentColor" />
      <path d="M3 4h1v2H3z" fill="#161a33" />
    </svg>
  );
}

function Expand({ text, lines = 3 }: { text: string; lines?: number }) {
  const [open, setOpen] = useState(false);
  const long = text.length > 180;
  return (
    <>
      <p className={`body ${!open && long ? "clamp" : ""}`} style={{ WebkitLineClamp: lines }}>
        {text}
      </p>
      {long && (
        <button className="more" onClick={() => setOpen(!open)} aria-expanded={open}>
          {open ? "Show less" : "Show all"}
        </button>
      )}
    </>
  );
}

function Section({ title, count, children }: { title: string; count?: number | string; children: ReactNode }) {
  return (
    <section className="section">
      <h3>
        {title}
        {count !== undefined && <span>{count}</span>}
      </h3>
      {children}
    </section>
  );
}

function NoteItem({ n, R }: { n: Note; R: Replay }) {
  return (
    <div className="item">
      <div className="label">{n.kind === "pass_down" ? (n.reason ? first(n.reason, 90) : n.by === "director" ? "From the director" : `From ${R.deptName(n.by)}`) : n.label}</div>
      <div className="meta">
        {fmtElapsed(n.t)} into the run{n.kind === "pass_down" ? `, passed by ${n.by === "director" ? "the director" : `${R.name(n.by)}, ${R.deptName(n.by)},`} to ${R.deptName(n.to ?? "")}` : ""}
      </div>
      <Expand text={n.text} />
    </div>
  );
}

function first(s: string, n: number) {
  return s.length > n ? s.slice(0, n - 1).replace(/\s+\S*$/, "") + "…" : s;
}

export function PersonButton({ id, R, t, select, note }: { id: string; R: Replay; t: number; select: Nav["select"]; note?: string }) {
  const a = R.agents.get(id);
  if (!a) return null;
  const st = R.status(id, t);
  return (
    <button className="person-btn" style={band(a.role, id)} onClick={() => select({ type: "agent", id })}>
      <Avatar id={id} role={a.role} scale={2} />
      <span>
        {R.name(id)}
        <small>{note ?? (st.working ? "Working" : st.present ? "Waiting" : "Not in yet")}</small>
      </span>
    </button>
  );
}

// ---- A person ---------------------------------------------------------------------------------

export function PersonPanel({ id, ...nav }: Nav & { id: string }) {
  const { R, t } = nav;
  const a = R.agents.get(id)!;
  const st = R.status(id, t);
  const study = a.study ? R.studies.get(a.study) : null;
  const notes = R.notesBy(id, t).slice().reverse();
  const passed = R.passDowns(id, t).slice().reverse();
  const searches = R.searches(id, t).slice().reverse();
  const questions = R.questionsTo(id, t).slice().reverse();
  const sessions = a.sessions.filter((s) => s.start <= t);
  const graded = R.graded(t);
  return (
    <div style={band(a.role, id)}>
      <div className="who">
        <div className="portrait">
          <Avatar id={id} role={a.role} scale={5} />
        </div>
        <div>
          <h2>{R.name(id)}</h2>
          <p className="role">
            <span className="swatch" style={{ background: colorOf(a.role, id) }} />
            {roleLabel(id, a.role)}
          </p>
          <p className="sub">
            {MODEL_NAME[a.model ?? ""] ?? a.model ?? "Model unknown"}
            {a.role === "director" || a.role === "head" ? ", reads every department" : study ? `, study ${study.id}` : ""}
          </p>
        </div>
      </div>
      <p className="now-line">
        <small>{st.working ? "Now" : st.present ? "Waiting" : "Not in yet"}</small>
        {R.activity(id, t)}
      </p>
      {study && (
        <p className="sub" style={{ marginTop: 10 }}>
          Working on{" "}
          <button className="more" style={{ fontSize: 13 }} onClick={() => nav.select({ type: "study", id: study.id })}>
            {study.title}
          </button>
        </p>
      )}
      <p className="reads">
        <Lock />
        {ROLE_READS[a.role]}
      </p>
      <div className="actions">
        <button className="btn gold" onClick={() => nav.talk(id)}>
          Ask {R.name(id).replace("The ", "the ")} a question
        </button>
        {study && (
          <button className="btn" onClick={() => nav.filter(study.id)}>
            Follow {study.id}
          </button>
        )}
      </div>

      {a.role === "library" && (
        <Section title="Reading" count={`${searches.length} searches`}>
          {!searches.length && <p className="empty">No searches yet.</p>}
          {searches.slice(0, 8).map((s, i) => (
            <div className="item" key={i}>
              <div className="label">{s.endpoint === "contents" ? "Opened pages to read in full" : `“${s.q}”`}</div>
              <div className="meta">{fmtElapsed(s.t)} into the run</div>
              <ul className="results">
                {s.results.slice(0, 3).map((r, j) => (
                  <li key={j}>
                    <a href={r.url} target="_blank" rel="noreferrer noopener">
                      {r.title || r.url}
                      <span>{hostOf(r.url)}</span>
                    </a>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </Section>
      )}

      {(a.role === "team-a" || a.role === "team-b" || a.role === "team-c") && (
        <Section title="Passed down to it" count={passed.length + questions.length || undefined}>
          {!passed.length && !questions.length && (
            <p className="empty">{a.role === "team-b" ? "Nothing yet. Team B starts from the data alone; it hears from the director only if the teams disagree or the director challenges them." : "Nothing yet."}</p>
          )}
          {questions.map((q, i) => (
            <div className="item" key={`q${i}`}>
              <div className="label">A question from the other team's warning signs</div>
              <div className="meta">{fmtElapsed(q.t)} into the run, {roundWord(q.round)}</div>
              <Expand text={(q.q ?? "").replace(/^QUESTION FROM THE DIRECTOR:\s*/, "")} />
            </div>
          ))}
          {passed.map((n) => (
            <NoteItem key={n.id} n={n} R={R} />
          ))}
        </Section>
      )}

      {a.role === "director" && <DirectorSections {...nav} />}
      {a.role === "head" && <HeadSections id={id} {...nav} />}

      {a.role !== "director" && a.role !== "solo" && (
        <Section title={a.role === "head" ? "Notes for the other heads" : "Notebook"} count={notes.length || undefined}>
          {!notes.length && <p className="empty">No notes written yet.</p>}
          {notes.slice(0, 12).map((n) => (
            <NoteItem key={n.id} n={n} R={R} />
          ))}
        </Section>
      )}

      {study && (a.role === "team-a" || a.role === "team-b" || a.role === "team-c") && (
        <Section title="Answers handed in">
          {(() => {
            const handed = (R.byStudy.get(study.id) ?? []).filter((e) => e.k === "candidate" && e.t <= t && `${study.id}-${e.team}` === id);
            if (!handed.length) return <p className="empty">No answer yet.</p>;
            return handed.map((e, i) => {
              const c = study.candidates.find((c) => c.team === e.team && c.round === e.round);
              return (
                <div className="item" key={i}>
                  <div className="label">{roundWord(e.round)}</div>
                  {c?.answer && (
                    <dl className="kv">
                      {Object.entries(c.answer).map(([k, v]) => (
                        <Frag key={k} k={k} v={v} />
                      ))}
                    </dl>
                  )}
                </div>
              );
            });
          })()}
        </Section>
      )}

      {a.role === "referee" && study?.referee && (R.byStudy.get(study.id) ?? []).some((e) => e.k === "referee.verdict" && e.t <= t) && (
        <Section title="The referee's test">
          <div className="item">
            <div className="label">What differed</div>
            <Expand text={study.referee.difference ?? ""} />
          </div>
          <div className="item">
            <div className="label">The test it ran</div>
            <Expand text={study.referee.test ?? ""} />
          </div>
          {study.referee.reason && (
            <div className="item">
              <div className="label">Decision: {study.referee.pick === "neither" ? "neither answer" : `answer ${study.referee.pick}`}</div>
              <Expand text={study.referee.reason} />
            </div>
          )}
        </Section>
      )}

      {a.role === "solo" && study && (
        <Section title="Result">
          {graded && study.grade ? (
            <p className="empty">This attempt {study.grade.solo[(Number(id.split("-").pop()) || 1) - 1] ? "passed" : "failed"} the grader.</p>
          ) : (
            <p className="empty">Graded after every agent has left.</p>
          )}
        </Section>
      )}

      <Section title="Sessions" count={sessions.length || undefined}>
        {!sessions.length && <p className="empty">Not started yet.</p>}
        <dl className="kv">
          {sessions.map((s, i) => (
            <Frag
              key={i}
              k={`${a.role === "director" || a.role === "head" ? `${s.study} ` : ""}${s.phase?.startsWith("baseline") ? "attempt" : s.phase}`}
              v={s.end !== null && s.end <= t ? `${Math.round(s.seconds ?? 0)} s, $${(s.cost ?? 0).toFixed(2)}` : `running for ${Math.round(t - s.start)} s`}
            />
          ))}
        </dl>
      </Section>
    </div>
  );
}

function Frag({ k, v }: { k: string; v: unknown }) {
  return (
    <>
      <dt>{k.replace(/_/g, " ")}</dt>
      <dd>{typeof v === "number" ? String(v) : typeof v === "string" ? v : JSON.stringify(v)}</dd>
    </>
  );
}

function hostOf(url: string) {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

function HeadSections({ id, R, t }: Nav & { id: string }) {
  const dept = headDept(id) ?? "";
  const reviews = R.run.events.filter((e) => e.k === "head.review" && e.a === id && e.t <= t).reverse();
  return (
    <Section title="Reviews" count={reviews.length || undefined}>
      {!reviews.length && (
        <p className="empty">
          {dept === "library"
            ? "None yet. It reviews each study's library findings before the director's briefing."
            : "None yet. It reviews its scientist's first answer before the answer goes to the hatch."}
        </p>
      )}
      {reviews.map((e, i) => {
        const rv = e.s ? R.studies.get(e.s)?.heads?.[dept] : undefined;
        return (
          <div className="item" key={i}>
            <div className="label">
              {e.s}: {dept === "library" ? `${e.weak ?? 0} weak findings, ${e.missing ?? 0} gaps` : e.delivered ? `${e.delivered} of ${e.notes} notes delivered` : e.notes ? `${e.notes} notes, none delivered` : "no notes"}
            </div>
            <div className="meta">{fmtElapsed(e.t)} into the run{rv?.revised ? (rv.changed_answer ? ", the scientist changed its answer" : ", the scientist kept its answer") : ""}</div>
            {rv?.verdict && <Expand text={rv.verdict} />}
            {(rv?.notes ?? []).map((nt, j) => (
              <div key={j} className="quote" style={{ marginTop: 6, boxShadow: `inset 3px 0 0 ${nt.delivered ? "var(--good)" : "var(--bad)"}` }}>
                {nt.text}
                {!nt.delivered && <div className="meta">Held back: {nt.reasons.join("; ")}</div>}
              </div>
            ))}
          </div>
        );
      })}
    </Section>
  );
}

function DirectorSections({ R, t }: Nav) {
  const qs = R.run.events.filter((e) => e.k === "relay.leak_check" && e.t <= t).reverse();
  const pds = R.passDownsFrom(t).reverse();
  const challenges = R.run.studies.filter((s) => s.challenge && (R.byStudy.get(s.id) ?? []).some((e) => e.k === "agent.finish" && e.a === "director" && e.phase === "challenge" && e.t <= t));
  return (
    <>
      {challenges.length > 0 && (
        <Section title="Trying to break agreed answers" count={challenges.length}>
          {challenges.map((s) => (
            <div className="item" key={s.id}>
              <div className="label">{s.id}: {s.title}</div>
              <Expand text={s.challenge!.verdict ?? ""} lines={4} />
            </div>
          ))}
        </Section>
      )}
      <Section title="Questions sent to teams" count={qs.length || undefined}>
        {!qs.length && <p className="empty">None yet. Questions go out only after the teams' answers are compared.</p>}
        {qs.slice(0, 10).map((q, i) => (
          <div className="item" key={i}>
            <div className="label">
              To {R.deptName(q.to ?? "")} {q.passed ? "" : "(held back by the leak check)"}
            </div>
            <div className="meta">{fmtElapsed(q.t)} into the run, {roundWord(q.round)}</div>
            <Expand text={(q.q ?? "").replace(/^QUESTION FROM THE DIRECTOR:\s*/, "")} />
          </div>
        ))}
      </Section>
      <Section title="Notes passed down" count={pds.length || undefined}>
        {!pds.length && <p className="empty">None yet.</p>}
        {pds.slice(0, 8).map((n) => (
          <NoteItem key={n.id} n={n} R={R} />
        ))}
      </Section>
    </>
  );
}

// ---- A study ----------------------------------------------------------------------------------

export function StudyPanel({ id, ...nav }: Nav & { id: string }) {
  const { R, t } = nav;
  const s = R.studies.get(id)!;
  const steps = R.studySteps(id, t);
  const members = R.run.agents.filter((a) => (a.study === id && a.role !== "solo" || a.role === "head") && a.sessions.some((x) => x.start <= t && (a.role !== "head" || x.study === id)));
  const solos = R.run.agents.filter((a) => a.study === id && a.role === "solo");
  const final = R.finalEvent(id, t);
  const graded = R.graded(t) && s.grade;
  const verdict = (R.byStudy.get(id) ?? []).some((e) => e.k === "referee.verdict" && e.t <= t);
  const challenged = (R.byStudy.get(id) ?? []).some((e) => e.k === "agent.finish" && e.a === "director" && e.phase === "challenge" && e.t <= t);
  return (
    <div>
      <p className="sub">Study {s.id}</p>
      <h2 style={{ fontFamily: "var(--pixel)", fontSize: 28, lineHeight: 1.05, margin: "2px 0 8px" }}>{s.title}</h2>
      <p className="now-line" style={{ marginTop: 10 }}>
        <small>{final ? "Result" : "Where it stands"}</small>
        {final ? `Reported as ${statusWord(final.status)}, using ${R.deptName(final.final ?? "")}'s answer.` : steps.length ? steps[steps.length - 1].title : "Not started yet."}
      </p>
      <div className="actions">
        <button className="btn gold" onClick={() => nav.talk("guide")}>
          Ask about this study
        </button>
        <button className="btn" onClick={() => nav.filter(null)}>
          Show all studies
        </button>
      </div>

      {graded && s.grade && (
        <Section title="Graded after every agent left">
          <div className="score">
            <div>
              <b className={s.grade.lab ? "mark pass" : "mark fail"}>{s.grade.lab ? "Pass" : "Fail"}</b>
              <span>The lab</span>
            </div>
            <div>
              <b>
                {s.grade.solo.filter(Boolean).length} of {s.grade.solo.length}
              </b>
              <span>Solo attempts passed</span>
            </div>
            <div>
              <b className={s.grade.vote ? "mark pass" : "mark fail"}>{s.grade.vote ? "Pass" : "Fail"}</b>
              <span>Vote over solo agents</span>
            </div>
          </div>
        </Section>
      )}

      <Section title="What has happened">
        {!steps.length && <p className="empty">Nothing yet.</p>}
        <ol className="steps">
          {steps.map((st, i) => (
            <li key={i} className={st.tone ?? ""}>
              {st.title}
              <time>{fmtElapsed(st.t)}</time>
              {st.detail && <p>{st.detail}</p>}
            </li>
          ))}
        </ol>
      </Section>

      <Section title="Who is on it">
        <div className="people">
          {members.length ? members.map((a) => <PersonButton key={a.id} id={a.id} R={R} t={t} select={nav.select} note={roleLabel(a.id, a.role)} />) : <p className="empty">Nobody yet.</p>}
        </div>
        {solos.length > 0 && (
          <p className="sub" style={{ marginTop: 10 }}>
            Plus {solos.length} solo agents working alone on the same data, for comparison.
          </p>
        )}
      </Section>

      {challenged && s.challenge && (
        <Section title="The director's challenge">
          <div className="quote">{s.challenge.verdict}</div>
          {(s.challenge.shared_assumptions ?? []).slice(0, 3).map((x, i) => (
            <div className="item" key={i}>
              <div className="label">An assumption both teams made</div>
              <Expand text={x} />
            </div>
          ))}
          <p className="sub" style={{ marginTop: 8 }}>
            {s.challenge_changed_answer ? "The challenge changed the answer." : "Both teams re-checked and kept their answer."}
          </p>
        </Section>
      )}

      {verdict && s.referee && (
        <Section title="The referee's test">
          <div className="item">
            <div className="label">What differed</div>
            <Expand text={s.referee.difference ?? ""} />
          </div>
          {s.referee.reason && (
            <div className="item">
              <div className="label">Why it decided as it did</div>
              <Expand text={s.referee.reason} />
            </div>
          )}
        </Section>
      )}
    </div>
  );
}

// ---- A room -----------------------------------------------------------------------------------

const ROOM_TEXT: Record<string, { role?: Role; what: string }> = {
  director: { role: "director", what: "One director for the whole lab. It reads every department's memory and decides what may cross from one team to another: a briefing for Team A, questions built from each team's warning signs, and a challenge to every agreed answer." },
  library: { role: "library", what: "One librarian per study. Before anyone analyses the data, it searches the web for how the method should be done and what usually goes wrong, and writes what it finds to memory. It never looks up the benchmark's source study." },
  "team-a": { role: "team-a", what: "One analyst per study. Team A gets the library's briefing from the director, writes its plan to memory before it touches the data, then hands in an answer." },
  "team-b": { role: "team-b", what: "One analyst per study, working from the data alone. It never sees Team A's notes or answers. The wall between the rooms has no door." },
  "team-c": { role: "team-c", what: "Called only when the referee cannot settle a disagreement. A third blind team, given the warning signs but no answers." },
  referee: { role: "referee", what: "Called only when the teams still disagree after the question round. It gets both answers as X and Y, without knowing whose is whose, and must design and run a test that tells them apart. It may not vote." },
  solo: { role: "solo", what: "The comparison arm: the same model, the same data and the same time limit, but no lab. Four attempts per study, each alone, with no memory and no library." },
  lobby: { what: "Agents arrive here when their first session starts." },
};

export function RoomPanel({ id, ...nav }: Nav & { id: string }) {
  const { R, t } = nav;
  const info = ROOM_TEXT[id] ?? { what: "" };
  const people = info.role ? R.run.agents.filter((a) => a.role === info.role || (a.role === "head" && headDept(a.id) === info.role)) : [];
  const present = people.filter((a) => R.status(a.id, t).present);
  const working = people.filter((a) => R.status(a.id, t).working);
  return (
    <div style={info.role ? band(info.role) : undefined}>
      <h2 style={{ fontFamily: "var(--pixel)", fontSize: 30, margin: "0 0 6px" }}>{roomName(id)}</h2>
      <p className="now-line" style={{ marginTop: 6 }}>
        <small>Now</small>
        {info.role ? `${working.length} working, ${present.length - working.length} waiting, ${people.length - present.length} not in yet` : "Open"}
      </p>
      <p style={{ color: "var(--mist)", margin: "12px 0 0" }}>{info.what}</p>
      {info.role && (
        <p className="reads">
          <Lock />
          {ROLE_READS[info.role]}
        </p>
      )}
      {info.role && (
        <Section title="People" count={people.length}>
          <div className="people">
            {present.map((a) => (
              <PersonButton key={a.id} id={a.id} R={R} t={t} select={nav.select} note={a.role === "head" ? roleLabel(a.id, a.role) : a.study ?? ""} />
            ))}
            {!present.length && <p className="empty">Nobody has arrived yet.</p>}
          </div>
        </Section>
      )}
    </div>
  );
}

function roomName(id: string) {
  return ({ director: "Director's office", library: "Library", "team-a": "Team A", "team-b": "Team B", "team-c": "Team C", referee: "Referee", solo: "Solo agents", lobby: "Lobby" } as Record<string, string>)[id] ?? id;
}

// ---- Fixtures -----------------------------------------------------------------------------------

export function FixturePanel({ id, ...nav }: Nav & { id: string }) {
  const { R, t } = nav;
  if (id === "hatch") {
    const cmps = R.run.events.filter((e) => e.k === "compare" && e.t <= t).reverse();
    return (
      <div>
        <h2 style={{ fontFamily: "var(--pixel)", fontSize: 30, margin: "0 0 6px" }}>The hatch</h2>
        <p style={{ color: "var(--mist)", margin: "8px 0 0" }}>
          The only place Team A's and Team B's work meets. The lab's code, not an agent, compares the two answers field by field and tells each team only which fields agree.
        </p>
        <Section title="Comparisons so far" count={cmps.length || undefined}>
          {!cmps.length && <p className="empty">No answers have been compared yet.</p>}
          {cmps.map((e, i) => (
            <div className="item" key={i}>
              <div className="label" style={{ color: e.agree ? "var(--good)" : "var(--bad)" }}>
                {e.s}: {e.agree ? "agree" : "disagree"}
              </div>
              <div className="meta">
                {fmtElapsed(e.t)} into the run, {roundWord(e.round)}
              </div>
              <dl className="kv">
                {Object.entries(e.fields ?? {}).map(([k, v]) => (
                  <Frag key={k} k={k} v={v ? "match" : "differ"} />
                ))}
              </dl>
            </div>
          ))}
        </Section>
      </div>
    );
  }
  if (id === "exa") {
    const ss = R.run.searches.filter((s) => s.t <= t).reverse();
    return (
      <div style={band("library")}>
        <h2 style={{ fontFamily: "var(--pixel)", fontSize: 30, margin: "0 0 6px" }}>Web search</h2>
        <p style={{ color: "var(--mist)", margin: "8px 0 0" }}>
          The librarians search the web with Exa through the lab's gateway, which holds the key and drops any result about the benchmark itself. Each librarian has a budget of {R.run.config.max_searches ?? 10} searches.
        </p>
        <Section title="Searches so far" count={ss.length || undefined}>
          {!ss.length && <p className="empty">None yet.</p>}
          {ss.slice(0, 14).map((s, i) => (
            <div className="item" key={i}>
              <div className="label">{s.endpoint === "contents" ? "Opened pages in full" : `“${s.q}”`}</div>
              <div className="meta">
                {R.name(s.a)} for {s.s}, {fmtElapsed(s.t)}
              </div>
            </div>
          ))}
        </Section>
      </div>
    );
  }
  if (id === "vault") {
    const notes = Object.values(R.run.notes).filter((n) => n.t <= t);
    const by = (role: string) => notes.filter((n) => n.kind === "note" && n.by.endsWith(role)).length;
    const pd = notes.filter((n) => n.kind === "pass_down").length;
    return (
      <div>
        <h2 style={{ fontFamily: "var(--pixel)", fontSize: 30, margin: "0 0 6px" }}>Memory vault</h2>
        <p style={{ color: "var(--mist)", margin: "8px 0 0" }}>
          Shared memory on past.dev. Every note is written with an audience, and every read names who is reading, so a read returns only what that agent may see. One cabinet per study, one drawer per department. Every call goes through a gateway on Modal that knows each agent from a header the agent cannot forge.
        </p>
        <Section title="Written so far">
          <dl className="kv">
            <Frag k="Library notes" v={by("library")} />
            <Frag k="Team A notes" v={by("team-a")} />
            <Frag k="Team B notes" v={by("team-b")} />
            <Frag k="Team C notes" v={by("team-c")} />
            <Frag k="Referee notes" v={by("referee")} />
            <Frag k="Passed down by the director" v={pd} />
          </dl>
        </Section>
        <Section title="Who can read what">
          <dl className="kv">
            <Frag k="Director" v="Everything" />
            <Frag k="Librarian" v="Its own study's library drawer" />
            <Frag k="Team A" v="Its own drawer and what the director passes down" />
            <Frag k="Team B" v="Its own drawer and what the director passes down" />
            <Frag k="Referee" v="Two unlabelled answers" />
          </dl>
        </Section>
      </div>
    );
  }
  return (
    <div>
      <h2 style={{ fontFamily: "var(--pixel)", fontSize: 30, margin: "0 0 6px" }}>Elevator</h2>
      <p style={{ color: "var(--mist)" }}>Agents arrive here when their first session starts, and walk to their desk.</p>
    </div>
  );
}
