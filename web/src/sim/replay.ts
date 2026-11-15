// What the lab looks like at any moment of a run: who is in, what they are doing, where each
// study stands. Everything is derived from the run's events, so scrubbing is exact.

import type { AgentRec, Ev, Note, Role, Run, Search, Study } from "../data/types";

export const ROLE_LABEL: Record<Role, string> = {
  director: "Lab director",
  library: "Research librarian",
  "team-a": "Scientist, Team A",
  "team-b": "Scientist, Team B",
  "team-c": "Scientist, Team C",
  referee: "Referee",
  solo: "Solo scientist",
  other: "Visitor",
};

export const ROLE_READS: Record<Role, string> = {
  director: "Reads every department's memory. Decides what crosses from one team to another.",
  library: "Reads and writes only its own study's library notes.",
  "team-a": "Reads only Team A's notes for this study, plus what the director passes down. Never Team B's.",
  "team-b": "Reads only Team B's notes for this study, plus what the director passes down. Never Team A's.",
  "team-c": "Called only if the referee cannot settle a disagreement. Reads only its own notes and the warning signs passed to it.",
  referee: "Sees two answers labelled X and Y without knowing which team wrote which. Must run a test, not vote.",
  solo: "Works alone with no lab, no memory and no library. A comparison arm for the lab.",
  other: "",
};

export const MODEL_NAME: Record<string, string> = {
  fable: "Fable 5.2",
  opus: "Opus 5.5",
  haiku: "Haiku 5.5",
  sonnet: "Sonnet 5.5",
};

const NAMES = [
  "Ada", "Bram", "Chidi", "Dana", "Elif", "Femi", "Gia", "Hana", "Ines", "Jun", "Kofi", "Lena", "Mateo",
  "Noor", "Oren", "Priya", "Quinn", "Rafa", "Sana", "Tomas", "Uma", "Vik", "Wen", "Xavi", "Yara", "Zane",
  "Amara", "Basil", "Cleo", "Dev", "Esme", "Farid", "Greta", "Hugo", "Ivy", "Jonas", "Kira", "Leo", "Mina",
  "Nils", "Olu", "Pia", "Rumi", "Taro", "Una", "Vera", "Wren", "Yusuf", "Zoe", "Aiko", "Bodhi", "Cyra",
  "Dmitri", "Eun", "Fiona", "Gael", "Hira", "Ilse", "Jaya", "Kai", "Luz", "Milo", "Nia", "Omar", "Paz",
  "Rhea", "Soren", "Tess", "Ugo", "Vida", "Wiley", "Ximena",
];

export const PHASE_TEXT: Record<string, string> = {
  library: "Researching how this kind of analysis should be done",
  briefing: "Choosing what Team A should know before it starts",
  analysis: "Analysing the data",
  revise: "Re-checking the method after the director's questions",
  challenge: "Re-checking an agreed answer the director is trying to break",
  questions: "Turning each team's warning signs into questions for the other",
  referee: "Designing a test that tells two answers apart",
  "third-team": "Called in as a third, blind team",
};

export type Status = {
  present: boolean;
  working: boolean;
  phase: string | null;
  sessionStart: number | null;
  sessions: number;
  done: boolean;
};

export type Moment = { t: number; kind: "brief" | "agree" | "disagree" | "referee" | "final" | "grade" | "challenge" | "questions"; study?: string; label: string };

export class Replay {
  run: Run;
  agents: Map<string, AgentRec>;
  names = new Map<string, string>();
  studies: Map<string, Study>;
  studyIndex = new Map<string, number>();
  byAgent = new Map<string, Ev[]>();
  byStudy = new Map<string, Ev[]>();
  end: number;
  moments: Moment[] = [];
  notesByAgent = new Map<string, Note[]>();
  passDownsTo = new Map<string, Note[]>();
  searchesBy = new Map<string, Search[]>();

  constructor(run: Run) {
    this.run = run;
    this.agents = new Map(run.agents.map((a) => [a.id, a]));
    this.studies = new Map(run.studies.map((s) => [s.id, s]));
    run.studies.forEach((s, i) => this.studyIndex.set(s.id, i));

    // Names: the director is the director; everyone else gets a first name, stable per run.
    let n = 0;
    const order = [...run.agents].sort((a, b) => roleOrder(a.role) - roleOrder(b.role) || (a.study ?? "").localeCompare(b.study ?? "") || a.id.localeCompare(b.id));
    for (const a of order) this.names.set(a.id, a.role === "director" ? "The Director" : NAMES[n++ % NAMES.length]);

    for (const e of run.events) {
      if (e.a) push(this.byAgent, e.a, e);
      if (e.s) push(this.byStudy, e.s, e);
    }
    for (const note of Object.values(run.notes)) {
      if (note.kind === "note") push(this.notesByAgent, note.by, note);
      else if (note.to) push(this.passDownsTo, note.to, note);
    }
    for (const s of run.searches) push(this.searchesBy, s.a, s);

    // The replay ends shortly after the last agent leaves; grading, which waited for the solo
    // agents, is shown at the end.
    let last = 0;
    for (const a of run.agents) for (const s of a.sessions) last = Math.max(last, s.end ?? s.start);
    this.end = Math.min(run.duration, last + 20);
    if (!run.finished) this.end = run.duration;
    this.buildMoments();
  }

  name(id: string) {
    return this.names.get(id) ?? id;
  }

  /** Events of the grading step are pinned to the end of the replay. */
  timeOf(e: Ev) {
    return e.k === "grade.done" ? Math.min(e.t, this.end) : e.t;
  }

  status(id: string, t: number): Status {
    const a = this.agents.get(id);
    const out: Status = { present: false, working: false, phase: null, sessionStart: null, sessions: 0, done: false };
    if (!a) return out;
    for (const s of a.sessions) {
      if (s.start > t) break;
      out.present = true;
      out.sessions++;
      out.phase = s.phase;
      out.sessionStart = s.start;
      out.working = s.end === null || s.end > t;
    }
    out.done = out.present && !out.working;
    return out;
  }

  /** The latest event by this agent at or before t. */
  lastEvent(id: string, t: number, kinds?: string[]): Ev | null {
    const list = this.byAgent.get(id);
    if (!list) return null;
    for (let i = upper(list, t) - 1; i >= 0; i--) {
      if (!kinds || kinds.includes(list[i].k)) return list[i];
    }
    return null;
  }

  /** One line on what this agent is doing at t, in plain words. */
  activity(id: string, t: number): string {
    const st = this.status(id, t);
    const a = this.agents.get(id);
    if (!a) return "";
    if (!st.present) return "Not in yet";
    const e = this.lastEvent(id, t, ["memory.write", "memory.recall", "web.search", "web.contents", "memory.pass_down"]);
    const fresh = e && st.sessionStart !== null && e.t >= st.sessionStart && t - e.t < 90;
    if (st.working && fresh && e) {
      if (e.k === "memory.write") return `Writing a note: ${this.run.notes[e.f!]?.label ?? "a finding"}`;
      if (e.k === "memory.recall") return `Looking through memory for "${e.q}"`;
      if (e.k === "web.search") return `Searching the web: "${this.run.searches[e.n!]?.q ?? ""}"`;
      if (e.k === "web.contents") return `Reading ${this.run.searches[e.n!]?.urls?.length ?? "a few"} pages in full`;
      if (e.k === "memory.pass_down") return `Passing a note to ${this.deptName(e.to ?? "")}`;
    }
    if (st.working) {
      if (a.role === "solo") return `Working alone on attempt ${a.id.split("-").pop()}`;
      return PHASE_TEXT[st.phase ?? ""] ?? "Working";
    }
    const s = a.study ? this.studies.get(a.study) : null;
    const final = s ? this.finalEvent(s.id, t) : null;
    if (final) return `Done. The study ended ${statusWord(final.status)}`;
    return "Finished a session, waiting for the next step";
  }

  deptName(dept: string) {
    const m = dept.match(/^(gb\d\d|sc\d\d|bd\d\d)-(.*)$/);
    if (!m) return dept === "director" ? "the director" : dept;
    const team = { "team-a": "Team A", "team-b": "Team B", "team-c": "Team C", library: "the library", referee: "the referee" }[m[2]] ?? m[2];
    return `${team} (${m[1]})`;
  }

  notesBy(id: string, t: number) {
    return (this.notesByAgent.get(id) ?? []).filter((n) => n.t <= t);
  }

  passDowns(id: string, t: number) {
    return (this.passDownsTo.get(id) ?? []).filter((n) => n.t <= t);
  }

  passDownsFrom(t: number) {
    return Object.values(this.run.notes).filter((n) => n.kind === "pass_down" && n.t <= t);
  }

  searches(id: string, t: number) {
    return (this.searchesBy.get(id) ?? []).filter((s) => s.t <= t);
  }

  questionsTo(id: string, t: number) {
    return this.run.events.filter((e) => e.k === "relay.leak_check" && e.to === id && e.passed && e.t <= t);
  }

  finalEvent(study: string, t: number) {
    return (this.byStudy.get(study) ?? []).find((e) => e.k === "study.final" && e.t <= t) ?? null;
  }

  graded(t: number) {
    return this.run.events.some((e) => e.k === "grade.done" && this.timeOf(e) <= t);
  }

  /** The stage a study is in at t, and the steps it has been through. */
  studySteps(id: string, t: number) {
    const evs = (this.byStudy.get(id) ?? []).filter((e) => e.t <= t);
    const steps: { t: number; title: string; detail?: string; tone?: "good" | "bad" | "neutral" }[] = [];
    const seen = new Set<string>();
    const once = (key: string, step: (typeof steps)[number]) => {
      if (seen.has(key)) return;
      seen.add(key);
      steps.push(step);
    };
    for (const e of evs) {
      if (e.k === "agent.start" && e.phase === "library") once("lib", { t: e.t, title: "The library starts researching" });
      if (e.k === "relay.briefing") once("brief", { t: e.t, title: "The director briefs Team A only", detail: `${e.items} notes passed down. Team B starts from the data alone.` });
      if (e.k === "agent.start" && e.phase === "analysis") once("ana", { t: e.t, title: "Two blind teams analyse the data" });
      if (e.k === "compare") once(`cmp-${e.round}`, {
        t: e.t,
        title: e.agree ? `Answers agree (${roundWord(e.round)})` : `Answers disagree (${roundWord(e.round)})`,
        detail: fieldsLine(e.fields),
        tone: e.agree ? "good" : "bad",
      });
      if (e.k === "agent.start" && e.a === "director" && e.phase === "challenge") once("chal", { t: e.t, title: "The director tries to break the agreed answer" });
      if (e.k === "agent.start" && e.a === "director" && e.phase === "questions") once("q", { t: e.t, title: "The director turns warning signs into questions" });
      if (e.k === "relay.leak_check") once(`leak-${e.to}-${e.round}`, {
        t: e.t,
        title: `${e.passed ? "Question sent" : "Question held back"} to ${this.deptName(e.to ?? "")}`,
        detail: e.passed ? undefined : "The leak check stopped it: it would have revealed the other team's answer.",
        tone: e.passed ? "neutral" : "bad",
      });
      if (e.k === "referee.assign") once("ref", { t: e.t, title: "A referee gets both answers, labelled X and Y" });
      if (e.k === "referee.verdict") once("verdict", {
        t: e.t,
        title: e.pick === "neither" ? "The referee's test picks neither answer" : `The referee's test picks answer ${e.pick}`,
        tone: e.pick === "neither" ? "bad" : "good",
      });
      if (e.k === "agent.start" && e.phase === "third-team") once("c", { t: e.t, title: "A third blind team is called in" });
      if (e.k === "study.final") once("final", { t: e.t, title: `Reported as ${statusWord(e.status)}`, tone: e.status === "unresolved" ? "bad" : "good" });
    }
    return steps;
  }

  private buildMoments() {
    for (const e of this.run.events) {
      if (e.k === "compare")
        this.moments.push({ t: e.t, kind: e.agree ? "agree" : "disagree", study: e.s ?? undefined, label: `${e.s}: answers ${e.agree ? "agree" : "disagree"}` });
      else if (e.k === "relay.briefing") this.moments.push({ t: e.t, kind: "brief", study: e.s ?? undefined, label: `${e.s}: Team A briefed` });
      else if (e.k === "referee.verdict") this.moments.push({ t: e.t, kind: "referee", study: e.s ?? undefined, label: `${e.s}: referee's test` });
      else if (e.k === "study.final") this.moments.push({ t: e.t, kind: "final", study: e.s ?? undefined, label: `${e.s}: ${statusWord(e.status)}` });
      else if (e.k === "grade.done") this.moments.push({ t: this.timeOf(e), kind: "grade", label: "Graded" });
      else if (e.k === "agent.start" && e.a === "director" && (e.phase === "challenge" || e.phase === "questions"))
        this.moments.push({ t: e.t, kind: e.phase === "challenge" ? "challenge" : "questions", study: e.s ?? undefined, label: `${e.s}: director ${e.phase === "challenge" ? "challenges" : "asks questions"}` });
    }
    this.moments.sort((a, b) => a.t - b.t);
  }

  /** Plain-language line for an event, for the lab log. Returns null for events too small to list. */
  describe(e: Ev): { who?: string; text: string; tone?: "good" | "bad" } | null {
    const n = (id?: string) => (id ? this.name(id) : "");
    switch (e.k) {
      case "agent.start": {
        const a = this.agents.get(e.a!);
        if (!a || a.role === "solo") return null;
        if (a.role === "director") return { who: e.a, text: `The director starts on ${e.s}: ${(PHASE_TEXT[e.phase ?? ""] ?? "").toLowerCase()}` };
        if (e.phase === "third-team") return { who: e.a, text: `${n(e.a)} is called in as Team C for ${e.s}` };
        if (a.role === "referee") return { who: e.a, text: `${n(e.a)}, the referee, takes ${e.s}` };
        if (a.sessions[0]?.start === e.t) return { who: e.a, text: `${n(e.a)} (${ROLE_LABEL[a.role].toLowerCase()}) starts on ${e.s}` };
        return { who: e.a, text: `${n(e.a)} starts again on ${e.s}: ${(PHASE_TEXT[e.phase ?? ""] ?? "").toLowerCase()}` };
      }
      case "web.search": {
        const s = this.run.searches[e.n!];
        return { who: e.a, text: `${n(e.a)} searches the web: "${s?.q ?? ""}"` };
      }
      case "web.contents":
        return { who: e.a, text: `${n(e.a)} opens pages to read in full` };
      case "memory.write": {
        const note = this.run.notes[e.f!];
        return { who: e.a, text: `${n(e.a)} writes "${note?.label ?? "a note"}"` };
      }
      case "relay.briefing":
        return { text: `The director passes ${e.items} library notes to Team A (${e.s}). Team B gets none` };
      case "relay.leak_check":
        return e.passed
          ? { who: "director", text: `The director asks ${this.deptName(e.to ?? "")} a question from the other team's warning signs` }
          : { who: "director", text: `A question to ${this.deptName(e.to ?? "")} is held back: it would leak an answer`, tone: "bad" };
      case "candidate":
        return { text: `${this.deptName(`${e.s}-${e.team}`)} hands in an answer (${roundWord(e.round)})` };
      case "compare":
        return { text: `${e.s}: the two answers ${e.agree ? "agree" : "disagree"} (${roundWord(e.round)})`, tone: e.agree ? "good" : "bad" };
      case "referee.verdict":
        return { text: `${e.s}: the referee's test ${e.pick === "neither" ? "supports neither answer" : `supports answer ${e.pick}`}`, tone: e.pick === "neither" ? "bad" : "good" };
      case "study.final":
        return { text: `${e.s} is reported as ${statusWord(e.status)}`, tone: e.status === "unresolved" ? "bad" : "good" };
      case "grade.done":
        return e.totals ? { text: `Graded after every agent left: the lab passed ${e.totals.lab_passed} of ${e.totals.lab_studies} studies` } : null;
      case "lab.halted":
        return { text: "The lab halted: the Claude account hit its usage limit", tone: "bad" };
    }
    return null;
  }
}

export function statusWord(s?: string | null) {
  return ({ replicated: "replicated", converged: "converged", refereed: "settled by the referee", unresolved: "unresolved", "no-answer": "no answer" } as Record<string, string>)[s ?? ""] ?? s ?? "";
}

export function roundWord(r?: string) {
  return ({ analysis: "first answers", revise: "after questions", challenge: "after the challenge", "third-team": "third team" } as Record<string, string>)[r ?? ""] ?? r ?? "";
}

function fieldsLine(fields?: Record<string, boolean>) {
  if (!fields) return undefined;
  const entries = Object.entries(fields);
  const same = entries.filter(([, v]) => v).length;
  return `${same} of ${entries.length} answer fields match`;
}

function roleOrder(r: Role) {
  return ["director", "library", "team-a", "team-b", "referee", "team-c", "solo", "other"].indexOf(r);
}

function push<K, V>(m: Map<K, V[]>, k: K, v: V) {
  const l = m.get(k);
  if (l) l.push(v);
  else m.set(k, [v]);
}

/** Index of the first event with t > time. */
function upper(list: Ev[], time: number) {
  let lo = 0;
  let hi = list.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (list[mid].t <= time) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

export function fmtClock(startIso: string, t: number) {
  const d = new Date(new Date(startIso).getTime() + t * 1000);
  return d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", second: "2-digit", timeZone: "America/Los_Angeles" });
}

export function fmtElapsed(t: number) {
  const m = Math.floor(t / 60);
  const s = Math.floor(t % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}
