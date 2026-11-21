// A short guided tour through one run, built from the run's own events so every number in a
// caption comes from the log.

import type { Ev } from "../data/types";
import type { Replay } from "../sim/replay";
import type { Target } from "../world/engine";

export type TourStep = { t: number; title: string; text: string; focus: Target | null; select?: Target; zoom?: number };

function clip(s: string, n: number) {
  const c = s.replace(/\s+/g, " ").trim();
  return c.length > n ? c.slice(0, n).replace(/\s+\S*$/, "") + "…" : c;
}

export function buildTour(R: Replay): TourStep[] {
  const ev = R.run.events;
  const find = (f: (e: Ev) => boolean) => ev.find(f);
  const steps: TourStep[] = [];
  const studies = R.run.studies.length;
  const lab = R.run.agents.filter((a) => a.role !== "solo").length;
  const solo = R.run.agents.filter((a) => a.role === "solo").length;

  const libStart = find((e) => e.k === "agent.start" && e.phase === "library");
  steps.push({
    t: (libStart?.t ?? 0) + 6,
    title: `${studies === 8 ? "Eight" : studies} studies start at once`,
    text: `${lab} lab agents work through ${studies} biology analyses side by side. Each study gets a librarian and two teams that never see each other's work.${solo ? ` In the room on the right, ${solo} solo agents tackle the same studies alone, for comparison.` : ""}`,
    focus: null,
  });

  const libs = R.run.agents.filter((a) => a.role === "library");
  const busiest = libs.sort((a, b) => R.searches(b.id, 1e9).length - R.searches(a.id, 1e9).length)[0];
  if (busiest) {
    const ss = R.searches(busiest.id, 1e9);
    const notes = R.notesBy(busiest.id, 1e9).length;
    const study = R.studies.get(busiest.study ?? "");
    steps.push({
      t: (ss[2]?.t ?? ss[0]?.t ?? 60) + 1,
      title: "The library reads up first",
      text: `${R.name(busiest.id)} runs ${ss.length} web searches on how to study ${study?.title.toLowerCase() ?? "the problem"} and what usually goes wrong, then writes ${notes} notes to memory. Click a librarian to see the articles.`,
      focus: { type: "agent", id: busiest.id },
      select: { type: "agent", id: busiest.id },
    });
  }

  const brief = find((e) => e.k === "relay.briefing");
  if (brief) {
    steps.push({
      t: brief.t - 3,
      title: "Only Team A is briefed",
      text: "The librarian carries its findings to the director, who passes the useful ones to Team A only. Team B starts from the data alone, so the two teams don't begin with the same ideas.",
      focus: { type: "agent", id: "director" },
    });
  }

  const dis = find((e) => e.k === "compare" && e.agree === false);
  if (dis) {
    const f = Object.values(dis.fields ?? {});
    steps.push({
      t: dis.t - 4,
      title: "Answers meet only at the hatch",
      text: `There is no door between Team A and Team B. Their answers meet at this hatch, where the lab compares them field by field. In ${dis.s}, the first answers differ on ${f.filter((x) => !x).length} of ${f.length} fields.`,
      focus: { type: "fixture", id: "hatch" },
    });
    const q = find((e) => e.k === "relay.leak_check" && e.passed === true && e.s === dis.s);
    if (q) {
      steps.push({
        t: q.t - 3,
        title: "Warning signs become questions",
        text: `The director never tells a team the other's answer. It turns what the other team noticed into a question: “${clip((q.q ?? "").replace(/^QUESTION FROM THE DIRECTOR:\s*/, ""), 170)}”`,
        focus: { type: "agent", id: q.to! },
        select: { type: "agent", id: q.to! },
      });
    }
  }

  const chal = find((e) => e.k === "agent.finish" && e.a === "director" && e.phase === "challenge");
  const chalStudy = chal?.s ? R.studies.get(chal.s) : null;
  if (chal && chalStudy?.challenge?.verdict) {
    steps.push({
      t: chal.t + 1,
      title: "Agreement has to survive a challenge",
      text: `When both teams agree, the director tries to break the answer before it is reported. On ${chalStudy.id}: “${clip(chalStudy.challenge.verdict, 180)}”`,
      focus: { type: "agent", id: "director" },
      select: { type: "study", id: chalStudy.id },
    });
  }

  const verdicts = ev.filter((e) => e.k === "referee.verdict" && e.pick !== "neither");
  const v = verdicts.find((e) => R.studies.get(e.s ?? "")?.grade?.lab) ?? verdicts[0];
  if (v?.s) {
    const s = R.studies.get(v.s)!;
    steps.push({
      t: v.t - 2,
      title: "A referee must test, not vote",
      text: `If the teams still disagree, a referee gets both answers as X and Y, not knowing whose is whose, and has to run a test. In ${s.id} it picked ${v.pick}: “${clip(s.referee?.reason ?? s.referee?.difference ?? "", 160)}”`,
      focus: { type: "agent", id: `${v.s}-referee` },
      select: { type: "agent", id: `${v.s}-referee` },
    });
  }

  const tot = R.run.totals;
  steps.push({
    t: R.end,
    title: "Graded after everyone left",
    text: tot
      ? `No agent ever sees a score. Once every agent had exited, the benchmark's own grader checked each answer: the lab passed ${tot.lab_passed} of ${tot.lab_studies} studies. Solo attempts passed ${tot.baseline_attempts_passed} of ${tot.baseline_attempts}, and a vote over them ${tot.vote_passed} of ${tot.vote_studies}. Click a study at the top to see how it went.`
      : "This run has not been graded yet. Answers are graded once, after every agent has left the lab.",
    focus: null,
  });
  return steps;
}
