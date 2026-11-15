// Answers without a model, from the run log only. Used when the Python server is not running
// (a static deploy) or Claude is unavailable. It keeps the same memory rule as the lab: each
// agent answers only from what it could read at that moment.

import type { Replay } from "../sim/replay";
import { lowerFirst, MODEL_NAME, PHASE_TEXT, ROLE_LABEL, roleLabel, roundWord, statusWord } from "../sim/replay";

export type Source = { ref: number; kind: string; id: string; label: string; t: number };
export type Answer = { answer: string; sources: Source[]; mode: "claude" | "offline"; model?: string; note?: string };

type Doc = { kind: string; id: string; label: string; text: string; t: number };

const STOP = new Set("the a an and or of to in on for is are was were be been it its this that with as at by from what which who how why do does did you your i me my we our they them can could would should about into than then there their have has had not no yes so if any all".split(" "));

function words(s: string) {
  return s.toLowerCase().match(/[a-z0-9]+/g)?.filter((w) => w.length > 2 && !STOP.has(w)) ?? [];
}

/** What an agent can read at time t. Mirrors visible_docs() in web/server.py. */
export function visibleDocs(R: Replay, agent: string, t: number): Doc[] {
  const run = R.run;
  const docs: Doc[] = [];
  const all = agent === "guide" || agent === "director" || R.agents.get(agent)?.role === "head";
  const a = R.agents.get(agent);
  for (const n of Object.values(run.notes)) {
    if (n.t > t) continue;
    const mine = n.kind === "note" ? n.by === agent : n.to === agent;
    if (!all && !mine) continue;
    docs.push({ kind: n.kind, id: n.id, label: n.label ?? `Note from the director to ${R.deptName(n.to ?? "")}`, text: n.kind === "pass_down" ? `${n.text}\nWhy it was passed on: ${n.reason ?? ""}` : n.text, t: n.t });
  }
  for (const s of run.searches) {
    if (s.t > t) continue;
    if (agent !== "guide" && s.a !== agent) continue;
    docs.push({ kind: "search", id: `search-${s.t}-${s.a}`, label: `Web search: ${s.q}`, text: `${s.q}\n${s.results.map((r) => r.title).join("\n")}`, t: s.t });
  }
  for (const e of run.events) {
    if (e.t > t || e.k !== "relay.leak_check") continue;
    if (!all && (e.to !== agent || !e.passed)) continue;
    docs.push({ kind: "question", id: `q-${e.t}-${e.to}`, label: `Question to ${R.deptName(e.to ?? "")}`, text: e.q ?? "", t: e.t });
  }
  for (const s of run.studies) {
    if (a && !all && a.study !== s.id) continue;
    const evs = R.byStudy.get(s.id) ?? [];
    for (const e of evs) {
      if (e.t > t) continue;
      if (e.k === "compare" && (all || (a && (a.role === "team-a" || a.role === "team-b" || a.role === "team-c")))) {
        const f = Object.entries(e.fields ?? {});
        docs.push({ kind: "answer", id: `cmp-${s.id}-${e.round}`, label: `Comparison of the two answers (${roundWord(e.round)})`, text: `The answers ${e.agree ? "agree" : "disagree"}. Fields that match: ${f.filter(([, v]) => v).map(([k]) => k).join(", ") || "none"}. Fields that differ: ${f.filter(([, v]) => !v).map(([k]) => k).join(", ") || "none"}.`, t: e.t });
      }
      if (e.k === "candidate" && (all || (a && e.team && agent === `${s.id}-${e.team}`))) {
        const c = s.candidates.find((c) => c.team === e.team && c.round === e.round);
        if (c) docs.push({ kind: "answer", id: `cand-${s.id}-${e.team}-${e.round}`, label: `${all ? R.deptName(c.agent) : "My"} answer (${roundWord(e.round)})`, text: JSON.stringify(c.answer), t: e.t });
      }
      if (e.k === "referee.verdict" && s.referee && (all || agent === `${s.id}-referee`)) {
        docs.push({ kind: "study", id: `ref-${s.id}`, label: `The referee's test for ${s.id}`, text: `Difference: ${s.referee.difference ?? ""}\nTest: ${s.referee.test ?? ""}\nPick: ${s.referee.pick ?? ""}\nReason: ${s.referee.reason ?? ""}`, t: e.t });
      }
      if (e.k === "study.final" && all) {
        docs.push({ kind: "study", id: `final-${s.id}`, label: `${s.id} result`, text: `${s.title}: reported as ${statusWord(e.status)} (${roundWord(e.round)}).`, t: e.t });
      }
    }
    if (all && s.challenge) {
      const chal = (R.byStudy.get(s.id) ?? []).find((e) => e.k === "agent.finish" && e.a === "director" && e.phase === "challenge");
      if (chal && chal.t <= t) docs.push({ kind: "study", id: `chal-${s.id}`, label: `The director's challenge for ${s.id}`, text: `${s.challenge.verdict ?? ""}\nAssumptions both teams made: ${(s.challenge.shared_assumptions ?? []).join(" ")}`, t: chal.t });
    }
    if (agent === "guide" && s.grade && R.graded(t)) {
      const solo = s.grade.solo.filter(Boolean).length;
      docs.push({ kind: "study", id: `grade-${s.id}`, label: `${s.id} grade`, text: `${s.title}. The lab's answer ${s.grade.lab ? "passed" : "failed"} the benchmark's grader. Solo agents passed ${solo} of ${s.grade.solo.length} attempts; a vote over them ${s.grade.vote ? "passed" : "failed"}.`, t: R.end });
    }
  }
  return docs;
}

function bm25(docs: Doc[], query: string, k = 6) {
  const q = words(query);
  if (!q.length) return [];
  const N = docs.length;
  const toks = docs.map((d) => words(`${d.label} ${d.label} ${d.text}`));
  const avg = toks.reduce((s, x) => s + x.length, 0) / Math.max(1, N);
  const df = new Map<string, number>();
  for (const ts of toks) for (const w of new Set(ts)) df.set(w, (df.get(w) ?? 0) + 1);
  const scored = toks.map((ts, i) => {
    let score = 0;
    for (const w of q) {
      const f = ts.filter((x) => x === w || (w.length > 4 && x.startsWith(w.slice(0, -1)))).length;
      if (!f) continue;
      const idf = Math.log(1 + (N - (df.get(w) ?? 0) + 0.5) / ((df.get(w) ?? 0) + 0.5));
      score += (idf * f * 2.2) / (f + 1.2 * (0.25 + 0.75 * (ts.length / avg)));
    }
    return { i, score };
  });
  return scored.filter((s) => s.score > 0).sort((a, b) => b.score - a.score).slice(0, k).map((s) => docs[s.i]);
}

function first(text: string, n = 220) {
  const s = text.replace(/\s+/g, " ").trim();
  if (s.length <= n) return s;
  const cut = s.slice(0, n);
  return cut.slice(0, Math.max(cut.lastIndexOf(". "), cut.lastIndexOf(", "), 120)) + "…";
}

export function answerOffline(R: Replay, agent: string, t: number, question: string): Answer {
  const q = question.toLowerCase();
  const docs = visibleDocs(R, agent, t);
  const a = R.agents.get(agent);
  const name = agent === "guide" ? "the guide" : R.name(agent);
  const sources: Source[] = [];
  const cite = (d: Doc) => {
    let ref = sources.findIndex((s) => s.id === d.id) + 1;
    if (!ref) {
      sources.push({ ref: sources.length + 1, kind: d.kind, id: d.id, label: d.label, t: d.t });
      ref = sources.length;
    }
    return `[${ref}]`;
  };
  const out = (answer: string): Answer => ({ answer, sources, mode: "offline" });
  const asks = (...k: string[]) => k.some((w) => q.includes(w));

  if (agent === "guide") {
    if (asks("pass", "grade", "score", "result", "right", "correct", "win", "beat", "better")) {
      if (!R.graded(t))
        return out("Nothing has been graded yet. The lab's answers are graded once, after every agent has left, so no agent can see a score while it works. Play to the end of the run to see the results.");
      const tot = R.run.totals;
      const lines = R.run.studies.filter((s) => s.grade).map((s) => `${s.id} ${s.grade!.lab ? "passed" : "failed"}`);
      const d = docs.find((x) => x.kind === "study" && x.id.startsWith("grade-"));
      return out(`Graded after every agent had left: the lab passed ${tot?.lab_passed} of ${tot?.lab_studies} studies (${lines.join(", ")}). For comparison, solo agents on the same model passed ${tot?.baseline_attempts_passed} of ${tot?.baseline_attempts} attempts, and a plain vote over them passed ${tot?.vote_passed} of ${tot?.vote_studies}. ${d ? cite(d) : ""}`.trim());
    }
    if (asks("how does", "how do", "work", "explain", "what is this", "what is summit", "blind", "why are")) {
      return out("Each study gets a librarian who looks up how the method should be done, and two blind teams. Only Team A gets the librarian's briefing; Team B starts from the data alone, so the two do not start from the same information. Their answers meet only at the hatch in the wall between them. If they agree, the director tries to break the answer before it is reported. If they disagree, the director passes each team's warning signs, never its answer, to the other team as questions. If they still disagree, a referee must run a test to tell the answers apart, and a third team is called if that fails.");
    }
  }

  if (a && (a.role === "team-a" || a.role === "team-b") && asks("other team", "team a", "team b", "their answer", "they find", "they found")) {
    const other = a.role === "team-a" ? "Team B" : "Team A";
    if (!(q.includes(a.role === "team-a" ? "team a" : "team b") && !q.includes(other.toLowerCase()))) {
      const cmp = docs.filter((d) => d.id.startsWith("cmp-")).pop();
      return out(`I can't see ${other}'s notes or answers. The lab keeps the two teams blind to each other on purpose, so that when we agree it means something. All I'm told is whether our answers match, field by field${cmp ? `: ${first(cmp.text, 160)} ${cite(cmp)}` : ", and no comparison has happened yet."}`);
    }
  }

  if (a && a.role === "head" && asks("tell your scientist", "pass it on", "pass that on", "why can't", "why cant", "why not tell")) {
    return out("I can read both teams' memory, but nothing from the other team may reach my scientist. The two teams work blind so that when they agree it means something. Only the director decides what crosses between teams, and only as questions that pass the leak check. My notes to my own scientist go through the same check, and the lab refuses any note that cites the other team's notes.");
  }

  if (agent !== "guide" && asks("grade", "pass", "score", "correct", "right answer")) {
    return out("I don't know whether my answer is right. Grading happens once, after every agent has left the lab, and nobody inside sees the answer key.");
  }

  if (a && asks("doing", "working on", "up to", "busy", "now", "status", "job", "role")) {
    const st = R.status(agent, t);
    const study = a.study ? R.studies.get(a.study) : null;
    const lastNote = docs.filter((d) => d.kind === "note").sort((x, y) => y.t - x.t)[0];
    const parts = [];
    const phrase: Record<string, string> = { director: "the lab director", head: roleLabel(agent, "head").replace("Head", "the head"), library: "the research librarian", "team-a": "a scientist on Team A", "team-b": "a scientist on Team B", "team-c": "a scientist on Team C", referee: "the referee", solo: "a solo scientist" };
    parts.push(`I'm ${R.name(agent)}, ${phrase[a.role] ?? ROLE_LABEL[a.role]}${study ? ` for ${study.id} (${study.title.toLowerCase()})` : ""}, running on ${MODEL_NAME[a.model ?? ""] ?? a.model}.`);
    if (!st.present) parts.push("I haven't started yet.");
    else if (st.working) parts.push(`Right now: ${lowerFirst(a.role === "solo" ? "working alone on this attempt" : PHASE_TEXT[st.phase ?? ""] ?? "working")}.`);
    else parts.push(`My session is over for now. ${R.activity(agent, t)}.`);
    if (lastNote) parts.push(`My latest note is "${lastNote.label}". ${cite(lastNote)}`);
    return out(parts.join(" "));
  }

  if (a && a.role === "library" && asks("read", "search", "source", "paper", "article", "look")) {
    const ss = docs.filter((d) => d.kind === "search").slice(-4);
    if (!ss.length) return out("I haven't searched anything yet.");
    return out(`I've run ${docs.filter((d) => d.kind === "search").length} web searches so far. The latest: ${ss.map((s) => `"${s.label.replace("Web search: ", "")}" ${cite(s)}`).join("; ")}.`);
  }

  const hits = bm25(docs, question, 3);
  if (!hits.length) {
    const recent = docs.filter((d) => d.kind === "note" || d.kind === "pass_down").sort((x, y) => y.t - x.t).slice(0, 2);
    if (!recent.length) return out(agent === "guide" ? "Nothing in the lab's records matches that yet. Try asking about a study, like gb10, or how the teams are kept blind." : `I don't have anything in my notes about that yet. ${a ? "I can only read my own department's memory." : ""}`.trim());
    return out(`Nothing in ${agent === "guide" ? "the records" : "my notes"} matches that closely. The most recent: ${recent.map((d) => `"${d.label}" ${cite(d)}`).join(" and ")}.`);
  }
  const voice = agent === "guide" ? "From the lab's records:" : "From what I can read:";
  return out(`${voice} ${hits.map((d) => `${d.label}. ${first(d.text)} ${cite(d)}`).join(" ")}`.replace(/\s+/g, " ") + (name ? "" : ""));
}
