# summit-lab working log

This file explains how the lab works and why. It is updated as the work happens, so a reader can follow every decision without having been in the room.

**How to read it.** Sections 1 to 3 describe the lab as it stands now. Sections 4 to 9 are logs: newest entries go at the bottom of each one. Every entry has a date, a time (Pacific) and a status:

- **Decided**: we are doing this.
- **Proposed**: the current plan, not built or tested yet.
- **Open**: not settled.
- **Measured**: backed by a run; the entry says which run.

Nothing in this file is called a result unless a run produced it.

---

## 1. What the lab is

A research lab run by AI agents, built for one question: can a team of agents reproduce computational biology analyses better than the best single model? (Track 3 of the hackathon.)

**The idea in one sentence.** Agents are kept blind to each other on purpose, so that when two teams agree it means something, and when they disagree the lab knows exactly where to look.

**Why this idea.** Human science controls error with three habits: independent replication, blinding, and writing the plan down before looking at the data. All three are limits on who may know what. In a lab of agents, a limit on who may know what is a memory rule. So the lab's memory rules are its scientific method, and section 3 is how they are enforced.

**Who is in the lab, for each study.**

| Department | Model | Reads | Job |
|---|---|---|---|
| Director | Fable | Everything | Assigns the study, passes facts between departments, signs off the report |
| Library | Opus | Library memory | Uses Exa to find how the method should be done and what usually goes wrong. Never the benchmark's source study |
| Team A and Team B | Opus | Its own team's memory only | Each analyses the data without seeing the other. Team A gets the library's briefing; team B does not. Each writes its plan first and records every warning sign it notices |
| Referee | Opus | Called only on disagreement | Gets both answers without knowing whose is whose. Must design and run a test that tells them apart; may not simply vote |
| Team C | Opus | Its own memory only | Called only when the referee cannot settle it |

**How one study runs** (version 2, from D-15; version 1 briefed both teams and reported agreed answers without a challenge).

1. The library researches the method with Exa. The director chooses what to pass down, and it goes to **team A only**. Team B starts from the data alone, so the two teams do not begin from the same information.
2. Each team writes its plan to memory before analysing. The time stamp proves the plan came first.
3. The teams analyse blind and submit an answer each.
4. The answers are compared.
5. **If they agree, the director tries to break the answer** before it is reported: assumptions both teams made, data neither used, weak points they named and did not resolve, a second reading of the task. Its questions go to both teams, and each must run the check and say whether it changes the method. An answer that survives is reported as *replicated*.
6. **If they disagree**, the director passes each team's warning signs, never its answer, to the other team as questions. Each team must say whether the warning changes its method, and may revise once.
7. If they still disagree, the referee runs a test. If that fails too, a third blind team is called.
8. The report gives every answer with how it was reached and every rejected answer with the reason.

**An example.** Two teams estimate an effect from a gene study. Team A notices that some samples appear twice and removes them. Team B does not notice, and its answer differs. The director does not tell B what A answered. It passes B one question: "The other team found repeated samples. Does that change your method?" B checks, agrees and redoes the estimate. Now the answers match, and the log shows which warning sign did it.

**Why step 6 matters.** The authors of our first benchmark say models mostly fail there in one way: they notice a warning sign in the data and then do not change the analysis (section 8). Step 6 turns one team's noticing into a question the other team has to act on.

**What the rehearsal taught** (a practice lab built the night before, in a separate private repository; no file from it is here). It tied a single agent, and three causes are designed out here:

- Its head threw away a correct table on a hunch. Here a disagreement is settled by a test or by a hedge, never by opinion.
- Four of its nine agreement checks compared a table with a copy of itself. Here the teams cannot read each other, so agreement cannot be a copy.
- Department heads were 38% of all agent runs. Here there is one director, and extra teams are called only on disagreement.

Status: **Decided** (D-8). **Not yet run**: nothing in this section has been measured.

## 2. Tools

| Tool | What it is for here | Status |
|---|---|---|
| Exa | Deep research: finding sources the agents do not already know | Decided; key works (M-2) |
| past.dev | Shared memory that agents write to and read from, with limits on who can read what | Decided; key works, read boundaries tested (M-1) |
| Modal | Cloud compute | Open: used only where it gives a real benefit (see section 6); login works (M-3) |

Keys live in environment variables only. They are never written into this repository or this log.

## 3. Memory: who can read what

Status: **Proposed**. Nothing is built yet.

The rule we want:

| Role | Writes to | Can read |
|---|---|---|
| Worker | Its own department's memory | Its own department's memory, plus notes a head has passed down to it |
| Head researcher | Any department's memory, plus notes passed down to workers | All memory |

So a worker never reads another department's raw memory. If department A learns something department B needs, a head reads it and passes down only the part B needs.

**Why this shape.**

- It keeps each worker's context small and on topic.
- It gives one place, the head, where cross-department information is filtered. Every pass-down is a decision we can log and inspect.
- It matches how past.dev works: each fact is written with an *audience*, each read names an *identity*, and a read returns only what that identity is allowed to see.

**How it maps onto past.dev (planned, checked against their docs on Nov 14, not yet run).**

- Every agent is a past.dev *identity* with two traits: `department` and `role`.
- Every department has one *audience*, defined by a rule: an identity is in it if its `department` is this one, or its `role` is `head`. A new worker joins its department's memory by having the trait; nobody edits a list.
- A worker's write always names its department's audience.
- A pass-down is a **new** fact with its own id, written by a head and addressed to the receiving department's audience. It cannot be the original fact re-sent: in past.dev, re-sending the same text with another audience moves the fact instead of sharing it.
- Nothing is written without an audience, because a fact with no audience can be read by every identity (issue I-2).
- A pass-down records the id of the fact it came from, so every relay a head makes can be traced back. (Added after reading the MAP-Graph paper in section 8: a summary that crosses a boundary hides where it came from unless the source is kept.)

**Tested on Nov 14 (M-1 in section 9).** With two departments, one worker each and one head, all nine checks passed: workers read their own department only, the head reads both, an unknown identity reads neither, and a pass-down reaches the other department without exposing the original note. This holds when each agent names its own identity honestly. Issue I-1 is about what happens when it does not.

**What we expect before any run.** To be written before the first run, so it can be compared with what happens.

**Known weak points.** Issues I-1, I-2 and I-3 in section 7.

---

## 4. Decision log

**D-1 · Nov 14, 11:20 · Decided · Start from an empty repository.**
This repository was created during the event. Before the event we rehearsed an agent-run lab in a separate, private repository. No file from the rehearsal is in this one. Where a lesson from the rehearsal shapes a decision, the entry says so.

**D-2 · Nov 14, 11:20 · Decided · Exa for research, past.dev for memory.**
These are the two main tools. Exa is how agents find outside sources. past.dev is the shared memory. Each must be part of the main loop, not a side feature.

**D-3 · Nov 14, 11:27 · Decided · Install Exa's `build-with-exa` skill.**
Source: `exa-labs/agent-skills` on GitHub (MIT licence), version 0.3.0, installed with `npx skills add exa-labs/agent-skills --skill "build-with-exa"`. The content hash is recorded in `skills-lock.json`. It is documentation only (one main file and 13 reference files, no scripts): it tells a coding agent which Exa endpoint fits which job and how to shape requests. Checked before use: the main file was read in full and the reference files were scanned for commands that could do harm; none were found.

**D-4 · Nov 14, 11:20 · Proposed · Memory access follows role.**
Workers read their own department; heads read everything and pass down what is needed. Full description in section 3. Alternatives not chosen: one memory everyone can read (no boundary to show, and workers get flooded), and fully separate memories with no heads (departments cannot learn from each other).

**D-5 · Nov 14, 11:20 · Decided · Modal only where it earns its place.**
We use Modal if it does something the lab needs and a laptop cannot. We do not add it for its own sake. Candidate uses are in section 6.

**D-6 · Nov 14, 11:20 · Decided · Keep this log.**
Every agent decision, layout change, observation, idea, issue and source goes here, at the time it happens.

**D-7 · Nov 14, 12:05 · Decided · Enter Track 3, AI Scientist Swarms.**
The lab reproduces computational biology analyses and is compared with a single agent on the same model and budget. The track's own benchmark, BixBench3, is one project and not the main one: each of its 20 studies needs a large machine for up to a day.

**D-8 · Nov 14, 12:12 · Decided · Blind replication, with more teams only on disagreement.**
Design in section 1. Considered and not chosen as the core: a "court" where one team proposes and a critic team attacks (close to the POPPER paper the track cites, so less new; its test step is kept as the referee). Considered and limited: more teams per study. Each extra team adds its full cost, and in the rehearsal 56 agent runs scored the same as one. So a study starts with two teams and a third is called only when needed; the lab grows by running several studies at once.

**D-9 · Nov 14, 12:18 · Decided · Which benchmarks, and what we may claim.**
Three Opus scouts reviewed about 40 benchmarks with Exa (M-4; reports in `research/scouting/`). Chosen:

1. **GeneBench-Pro, the 10 public problems** (OpenAI, June 2026). Genomics problems on small tables, graded automatically. On the full 129 problems the best published model passes about a third and Claude Opus 4.8 passed 9 to 16%. Runs on a laptop.
2. **scBench public tasks** (LatchBio). Single-cell analysis steps with automatic graders. Next, if time allows.
3. **One BixBench3 study on Modal**, as a showcase of the same lab on a full study.

Rejected, with reasons: the original BixBench (an audit found 38% of tasks broken; top systems above 90% on the verified set), CORE-Bench (declared solved by its authors), BioAgent Bench (100% already), BioCoder (its grader needs a cloud setup we do not have), the LatchBio full sets (private), OmicsBench (94 of 97 tasks need a request to the authors), and several whose only scores are from 2024 models.

**What we may claim.** Only public problems can be run, so published full-benchmark scores are context, not a record we can beat. The claim we can make honestly is the lab against a single agent, same model, same budget, same problems, with repeats.

**D-10 · Nov 14, 12:18 · Decided · One gateway holds every key; agents hold none.**
Agents reach memory and Exa only through a small gateway that knows each agent by a secret token and takes its identity from that token, never from what the agent says. This is idea N-1 built locally first; moving it onto Modal comes after the lab runs end to end.

**D-11 · Nov 14, 12:20 · Decided · Fair-play rules for every benchmark run.**
- Agents get the task text and the data, under neutral names (gb01 to gb10). Never the answers, the worked reports, the grader settings or the benchmark's own problem names.
- No agent, and no session building the lab, looks up a benchmark's source study. The gateway drops search results that match a block list.
- Teams are compared with our own rule (numbers within 5%, text equal), not the grader's tolerances, which a single agent would not have either.
- Grading runs only after every agent has exited.
- The single-agent baseline gets the same task text, data, model and budget.

**D-12 · Nov 14, 12:32 · Decided · Score with the benchmark's own grader; add our own measures on top.**
Pass or fail comes from the benchmark's reference grader, unchanged, so our numbers are measured the way published ones are. A grader of our own would invite the question of whether we marked ourselves kindly.

The lab also produces something a single agent cannot: a label on every answer saying how it was reached (replicated, converged after questions, refereed, third team, unresolved). Our measures ask whether that label, and each step behind it, is worth anything. All of them are computed from the run's records. They need no extra agent runs: after the run, every candidate answer is graded, not only the final ones.

| Measure | The question it answers | How it is computed |
|---|---|---|
| Lab, single agent, same-budget vote | Does the organisation help, or only the extra spending? | Passes for the lab's final answers; for single-agent attempts; and for a plain vote over as many single attempts as cost about what the lab cost |
| Agreement as evidence | When the lab calls an answer replicated, how often is it right? | Pass rate for each label |
| False agreement | How often do two blind teams agree on a wrong answer? | Replicated answers that fail, out of all replicated answers. This is the error blinding cannot remove: a blind spot both teams share |
| Rescues and damage | What did each step change? | For the question round, the referee and the third team: answers turned from wrong to right, and from right to wrong |
| Selection | When a right answer was in the lab's hands, did the lab ship it? | Lab passes, out of the problems where at least one candidate passed. The rehearsal lab failed here |
| Noticing to acting | Do warning signs change the analysis? | Of the warning signs recorded, the share that changed a method; of the director's questions, the share that changed a method, and the share of those that turned a wrong answer right |
| Library value | Did the Exa research matter? | Of the pitfalls the library found, the share a team confirmed in the data and the share that changed a method; plus a run of the lab with the library switched off |
| Cost | What does it cost? | Agent runs, minutes and dollars per problem; passes per dollar |

With ten problems these are counts, not rates to trust to the decimal (I-5). We report the counts.

**D-13 · Nov 14, 13:40 · Decided · Run each agent in its own Modal sandbox, with the gateway on Modal too.**
Why: the laptop fits about four analysts at once (I-10), and ten problems with repeats would take most of a night. A sandbox per agent removes that limit and makes the blindness physical: two teams are two separate machines with no path between them.
How identity works there (idea N-1, now being built): Modal issues each agent a connect token carrying its agent id. When the agent calls the gateway, Modal adds that id to the request in a header the agent cannot set or change. The gateway takes identity from that header alone. So "who is asking" is vouched for by the platform, not by the agent and not by a list of tokens we keep.
What stays the same: the flow, the prompts, the fair-play rules, and the laptop runner, which remains available.
Limits we accept: the Claude sign-in token is present inside each agent's sandbox (the agent needs it to run), with the network closed to everything except the model API, the gateway and the package index. Many agents at once may hit the Claude usage limit before they hit a compute limit.
Status: being built; nothing in this entry has been tested yet.

**D-14 · Nov 14, 14:13 · Decided · Problems gb01 and gb02 are for development; gb03 to gb10 are the test.**
We have now seen scores for gb01 and gb02 and will change the design because of them. Any change is frozen and written here before a single test problem is run, and the test problems are scored once. Results are reported for the eight test problems and for all ten, separately, with the pilot's result on the two development problems kept as it was.

**D-15 · Nov 14, 14:27 · Decided · Design version 2, because of pilot 1.**
Three changes, each answering something pilot 1 showed (M-8, O-4, I-11):
1. **Brief one team only.** In the pilot both teams got the same eight items from the library, made the same choices and agreed on a wrong answer. Now only team A is briefed.
2. **Challenge every agreed answer.** In the pilot an agreed answer was reported at once and was wrong. Now the director must try to break it first (step 5 in section 1). This is where Fable, the strongest model in the lab, does its main work.
3. **Wait for memory only until it is readable.** The pilot lost 8.5 minutes waiting for past.dev to finish deriving memory. The lab now waits only until the receiving agent can see the facts just delivered to it.
Not changed: the models (Fable alone did no better than Opus alone on the development problems, M-9), the time limit, the question round, the referee, the third team, the fair-play rules.
What we expect, written before any test problem runs: the challenge will change some agreed answers; we do not know whether it will change them for the better. Version 1 is still runnable (`--brief-teams team-a,team-b --no-challenge`) for a direct comparison if the budget allows.
Status: built and tested with scripted agents (116 tests pass). Running on the two development problems now; frozen once that run is read.

**D-16 · Nov 14, 14:49 · Decided · Add scBench as the second project.**
Why: on the two GeneBench development problems nothing passed, for the lab or for either model alone (M-8, M-9). If every score is zero there is nothing to compare. scBench (LatchBio) gives single-cell analysis tasks with automatic graders where published single models pass roughly a third to two thirds, so a difference can show in either direction.
What can be run: 36 public tasks. Six are the benchmark's current public set; thirty are an older public set packaged in UK AISI's Inspect Evals. Published single-model results on these sets: on the current six, 16, 10 and 11 passes out of 18 runs for three model-and-harness pairs (tallied by our builder from the benchmark's published runs); on the older thirty, 43%, 37% and 30% for three 2025 models (Inspect Evals README). These are context, not our baseline: our baseline is our own single agent on the same tasks, model and limit.
Guards beyond D-11: scBench agents get no access to the Python package index, because one public package there contains the older set's answers; each study gets its own library memory, because several tasks share a data file and shared memory would carry one task's findings into another; grading runs in a separate environment no agent can reach.
Status: tasks staged with answers sealed; the current six tasks' data is on a Modal volume; wiring into the runner in progress; no task run yet.

**D-17 · Nov 14, 15:03 · Decided · The design is frozen for the test problems.**
Version 2 as in D-15, plus two changes made before the freeze: each problem has its own library memory, and the director is told to use only its own problem's facts (eight problems now run at once, and the director can read all of them). Models: director Fable 5.2, all other agents Opus 5.5. Limits: 30 minutes per analyst and per single agent, 10 library searches. Comparison: the lab; four independent Opus 5.5 attempts per problem; two Fable 5.2 attempts per problem; a plain vote over the single attempts at about the lab's cost. Scoring: GeneBench's reference grader, once, after every agent has exited.
Nothing in the lab's design changes after this entry until the test problems gb03 to gb10 are scored.

**D-18 · Nov 14, 15:15 · Decided · Run several projects side by side; add BioDSA-1K as the third.**
The team's aim is several projects at once, each on a benchmark that is not already solved and that the lab might beat. Two more Opus scouts swept for candidates with Exa (reports: `research/scouting/shortlist_verified.md` and `wider_sweep.md`; 276 Exa calls in total so far, $1.35).

| Project | Tasks we can run | Why it is in or out |
|---|---|---|
| GeneBench-Pro public | 10 | In. Running. Hardest: nothing has passed the two development problems |
| scBench | 36 | In. Staged and wired; published single models pass roughly a third to two thirds |
| BioDSA-1K | 40 sampled from about 1,000 | In. Each task is a biomedical claim to judge True, False or Not verifiable against data tables. It is the track's question in its plainest form: rejecting claims the data does not support. Exact labels, small tables |
| GenoTEX | a few traits | Later, if usage allows: 2 to 3 hours of setup; its expert answers ship in the same download as the inputs |
| CloningQA | 14 | Out for now: too few tasks to show a difference |
| VariantBench | 6 | Out for now: too few tasks, 34 GB of memory per task in the reference setup |
| ScienceAgentBench | 3 after exclusions | Out: most of its biology and chemistry tasks are drug-property models, which we do not do, and its figure tasks need a judge model |
| SciAgentArena | 229 | Not started: the largest candidate, with low published scores, but it needs its terms accepted by a team member and the most wiring |
| BioMysteryBench, GeneTasks | 90, 173 | Not started: each needs an access step or a new paid account, and for the first we could not confirm answers can be checked by a program |
| CompBioBench, Galaxy workflow set, CORE-Bench, BioAgent Bench, original BixBench | | Out: already at or near 100% |

What limits the number of projects is Claude usage on one account, not compute.

**D-19 · Nov 14, 15:58 · Decided · Design version 3 for the new benchmarks: Fable analysts and research as part of the work.**
Why. The GeneBench test (M-11) said two things. The lab fixes inconsistency, not blind spots: it passed every problem one Opus attempt passes at least half the time and none beyond. And web research was thin: one library agent, about 8 quick searches per problem, 7 pages opened across the whole run, a 7 to 9 item note to one team; analysts could not look anything up. A blind spot is missing knowledge, and two copies of one model lack the same knowledge. Search is the only part of the lab that can bring in knowledge the model does not have.
What changes, for scBench and BioDSA-1K only (both untouched so far):
1. **All agents on Fable 5.2**, the team's decision after Fable alone beat the Opus lab. The model per team stays switchable, so a mixed Fable and Opus run can be compared.
2. **Team A's analyst can search while it works**, through the gateway, and must record what it learned and from where. Team B still works from the data alone, which keeps the two teams different in what they know.
3. **Research on demand.** When the director writes questions (after a disagreement or when challenging an agreement) it can also ask up to three research questions. The library looks them up, opening the pages, and the findings go to both teams before they revise. Research questions pass the same leak check as team questions.
4. **Search budgets enforced by the gateway**: library 12 calls, team A's analyst 8 per round, research on demand 10.
5. **A run with all search off**, to measure what search contributes.
Unchanged: blind teams, the leak check, the referee that must test, the third team, the 30-minute limit, the fair-play rules (no agent may look up a task, its dataset or a benchmark; results about the benchmark are dropped by the gateway).
The comparison is now against the best single model: Fable 5.2 alone, same tasks, same limit, without web access as the benchmarks intend, plus a vote over its attempts at about the lab's cost.
What we expect, written before any run: a lab of Fable analysts without the search changes would do about as well as Fable alone, because Fable was consistent on GeneBench (2 of 2 or 0 of 2 on every problem). If version 3 beats Fable alone, we expect the gain to come from problems where a search changed a method, and the search-off run to lose that gain. We may be wrong on both counts.
Status: being built. The GeneBench design of D-17 stays reproducible with `--model opus --no-team-search --no-research`.

**D-20 · Nov 14, 17:02 · Decided · Version 3 is frozen for the BioDSA-1K and scBench test tasks.**
Design as in D-19, unchanged after the pilots (M-12). Development tasks, whose scores we have seen: BioDSA-1K bd01 to bd04; scBench sc01, sc07, sc08. Test tasks, scored once after every agent has exited: BioDSA-1K bd05 to bd40 (36 tasks); scBench sc02 to sc06 and sc09 to sc36 (33 tasks). All agents Fable 5.2. Comparison: Fable 5.2 alone, four attempts per BioDSA-1K task and two per scBench task, same limit, no web access; and for BioDSA-1K a second lab run with all search off. Order of running, because Claude usage on one account is the limit: BioDSA-1K lab, BioDSA-1K single agents, scBench, then the search-off run.

**D-21 · Nov 14, 17:32 · Decided · scBench's lab runs with Opus analysts, because the Fable allowance cannot carry both runs.**
The account's Fable allowance ran out twenty minutes into running both test sets on Fable (I-13) and resets on a timer. Two full runs on Fable would hit it repeatedly. So, before any scBench test task has finished or been scored: the scBench lab uses Opus 5.5 for the library, the teams and the referee, and Fable 5.2 for the director only. Everything else in version 3 is unchanged. It is compared with Opus 5.5 alone and with Fable 5.2 alone, two attempts each per task. BioDSA-1K stays as frozen in D-20: every agent on Fable.
What this means for reading the results: BioDSA-1K asks whether a Fable lab beats Fable alone. scBench now asks a different question: whether a lab of the weaker model, with search and a Fable director, can match the stronger model alone. The scBench pilot (M-12) was run with Fable analysts, so its three development tasks are not a pilot of this configuration; we say so here and will say so in the results.

## 5. Agent observations

Things the agents did that we did not expect.

**O-1 · Nov 14, 12:47 · Measured (smoke run 1, cheapest model) · Blocked agents reported the block instead of inventing an answer.**
In the first plumbing test a fault of ours (I-9) stopped every agent from unpacking the data. Neither analyst guessed. One wrote: "No plan was recorded to memory, no analysis was run, and no answer.json was written, because any numbers would be unsupported." The lab then recorded the study as "no-answer". That is the behaviour we want from a team: a missing answer is recorded as missing, not papered over. One run, one model; not evidence of how often this holds.

**O-2 · Nov 14, 14:13 · Measured (pilot 1, problem gb01) · A director's question changed a team's method, and the teams then agreed on that field.**
The teams first disagreed on three of four fields. One of the director's questions to team A reported what team B had seen about toxicity at different activity scores, without giving any number of B's. Team A checked and replied: "It holds and it is a real mis-specification in my earlier model. Toxicity under TXR1i is a step function, not a gradient". It changed its model, and the two teams' toxicity figures then agreed. This is step 5 of the design doing what it was built for. The final answer still failed the grader, so the change made the teams agree; we cannot say it made them right.

**O-3 · Nov 14, 14:13 · Measured (pilot 1, problem gb01) · The referee refused to pick and found something neither team had.**
Told it may not vote, the referee ran one estimator on both teams' case sets, split the gap into "which rows" and "which model", and judged one team right on the disputed rows. It then reported that both teams kept a cluster of 154 cases that looked different from the rest on an independent marker, picked neither, and gave its own answer. That answer also failed. So the referee behaved as designed (a test, not a vote); whether its finding was the missing insight we cannot tell without the answer key, which we do not open.

**O-4 · Nov 14, 14:13 · Measured (pilot 1, problem gb02) · Two blind teams agreed, and both were wrong.**
Both teams got the same eight items from the library, made the same choices, named the same weakest point, and submitted answers within 5% of each other. The lab labelled the answer replicated. It failed. The four single agents, who had no library, clustered on a different answer that also failed. See I-11.

**O-5 · Nov 14, 15:03 · Measured (version 2, problem gb02) · The director's challenge found what both teams shared.**
The two teams agreed again, although only team A had the library's briefing. The director (Fable 5.2) then read both method notes and the data's layout and wrote: "Moderate trust at best: the two teams agree, but both reach the answer through the same chr3 label flip chosen by a separation-maximising rule on only four chromosomes, and neither calibrated it against chance". It listed three assumptions both teams made, three pieces of data neither used (one a whole file), and three other readings of the task's wording, and sent eight questions. After re-checking, the teams no longer agreed, and the referee decided. The final answer still failed. So the challenge did what it is for, breaking a false agreement, and the lab no longer called that answer replicated; it did not find the right answer.

**O-6 · Nov 14, 15:31 · Measured (test run, problem gb10) · Two wrong answers went in and a right one came out.**
Both teams' first answers failed the grader. The director passed each team's warning signs to the other as questions. Team B changed its method and its new answer passed; team A kept an answer that still failed. The referee, who did not know which team was which and may not vote, ran a test and picked team B's. The lab reported a passing answer. Scored after the run, so no agent knew which answer was right.

**O-7 · Nov 14, 15:31 · Measured (test run, problem gb09) · A question from the other team's warning signs rescued a wrong answer.**
Team A's first answer passed and team B's failed. After the question round team B's revised answer passed and matched team A's, and the lab reported it as converged. Team A did not move to B's wrong answer: in the whole test run no step turned a passing answer into a failing one.

**O-8 · Nov 14, 15:31 · Measured (test run, problem gb05) · The challenge did not break a false agreement this time.**
Both teams agreed on a wrong answer. The director challenged it with questions; both teams re-checked and kept their answer; the lab reported it as replicated, and it failed. On the development problem gb02 the challenge did break a false agreement (O-5). So the challenge sometimes catches a shared blind spot and sometimes does not.

**O-9 · Nov 14, 17:02 · Measured (BioDSA-1K pilot, task bd01) · The team that knew more was right, and the blind referee picked it.**
Fable alone answered "not verifiable" on both attempts; the data in fact settles the claim. In the lab, team A (briefed by the library) answered correctly and team B (data only) made the single agents' mistake. Neither moved after the question round. The referee, not knowing which team was which, ran a test and picked team A's answer, and the lab passed. One task; but it is the design working as intended: two teams that differ in what they know, and a test instead of a vote.

**O-10 · Nov 14, 17:02 · Measured (both pilots) · Fable alone fails in one way on BioDSA-1K, and the lab shares it when both teams do.**
All four failed single attempts on the pilot tasks judged a settled claim "not verifiable". On bd04 both lab teams did the same, the challenge did not move them, and the lab reported a wrong answer as replicated. The same happened on scBench task sc01. With every agent on the same model, a mistake the model makes every time survives the whole lab.

## 6. Ideas

**N-1 · Nov 14, 11:30, updated 11:33 · Open · Enforce the memory boundary with infrastructure, not with a prompt.**
The problem is issue I-1: past.dev trusts whatever identity the caller names. The idea is to let the compute platform vouch for who is calling. After reading the Modal docs, the pieces exist:

1. One small **memory gateway** holds the past.dev key. No agent does.
2. Each agent runs in its own Modal sandbox and is handed a Modal *connect token* made for it alone. Modal attaches the token's metadata (the agent's identity) to every request the gateway receives, in a header the docs call unspoofable.
3. The gateway reads the identity from that header, never from the request body, and uses it for the past.dev call. It sets the audience on every write.
4. Each worker sandbox gets an outbound allowlist that leaves out past.dev, so a worker cannot go around the gateway even if it found a key.

A worker then cannot read as a head, whatever its prompt says, and a judge can test it. What must still be checked by running it: that connect tokens work for sandbox-to-sandbox calls, and how the allowlist (a Beta feature) behaves. Modal also has a Beta feature that injects a key into a sandbox's outgoing requests from outside the sandbox. That hides the key but not the identity field, so on its own it does not close I-1.

**N-2 · Nov 14, 11:33 · Open · Fork a researcher.**
Modal can snapshot a sandbox's files and start several new sandboxes from the snapshot. A worker that reaches a fork in its investigation could be copied, with each copy following one branch from the same starting state. Only useful if the lab's work has such forks; not planned yet.

**N-3 · Nov 14, 11:33 · Open · Replay a decision with what the agent knew at the time.**
past.dev can answer a read "as of" an earlier moment. For any decision in this log we could re-run the read as of the decision time and show exactly what memory the agent had, then compare with what it would see now. That makes each decision checkable after the fact.

## 7. Issues

**I-1 · Nov 14, 11:30, confirmed from the docs 11:33 · Open · A read boundary is only as strong as the identity on the request.**
The caller names the identity it is reading as, and one project key covers the whole project. past.dev's access-control page says it directly: a project key "reaches every audience of the project". The same key can also change who is in each audience. So if every agent holds the key, a worker could read as a head, or rewrite the rules. Separate past.dev projects would isolate fully, but they share nothing, and the starter plan allows one project. Candidate fix: N-1. Not yet tested with a key.

**I-2 · Nov 14, 11:33 · Open · A write without an audience is readable by everyone.**
In past.dev a fact sent without an audience is visible to every identity. Re-sending a fact without repeating its audience also makes it visible to everyone. An identity past.dev has never seen gets that shared set and no error. So one careless write leaks across departments. Planned guard: all writes go through one code path that refuses a write with no audience, and the shared set is kept empty unless we decide to use it for lab-wide notices. **Measured Nov 14 (M-1):** a fact written with no audience was read back by a worker in another department and by an identity past.dev had never seen.

**I-3 · Nov 14, 11:33 · Open · Memory is not instant.**
past.dev accepts a write at once but derives memory from it in the background, one write after another. The raw text is searchable within seconds; the derived facts come later, and a read reports how much is still pending. An agent that writes and reads back straight away may not see its own fact in derived form. The event brief also asks teams to keep data to tens of documents. **Measured Nov 14 (M-1), four short facts, one run:** the writer could read its own fact back 2.9 seconds after sending it; past.dev reported each write fully processed after 7 to 17 seconds; reads took 0.6 to 2.0 seconds. So the delay is small at this size. Not yet measured: the delay with many writes queued.

**I-4 · Nov 14, 12:20 · Open · The public benchmark ships its answers, and its folder names hint at the method.**
GeneBench-Pro's public package puts the answers, the grader settings and a worked report beside the data, and names each problem folder after its topic. Guard: the package is kept in a sealed folder; agents get copies under neutral names with only the task text and data (D-11), and a test checks the agent side for leaks. Caveat we state plainly: the session that built the lab saw the folder names while downloading. It did not open the answers or the reports, and no folder name is in any text an agent receives.

**I-5 · Nov 14, 12:20 · Open · Ten problems is a small sample.**
A difference of one problem is 10 points. Every comparison needs repeats, and we report counts, not only percentages.

**I-6 · Nov 14, 12:20 · Open · A team of agents can do worse than one agent.**
Published cases: on ScienceAgentBench a one-shot team scored 32.4% against 38.2% for a single agent on the same model, and a Google and MIT study found negative returns from extra agents once a single agent passes about 45% (section 8). Our rehearsal was a tie. What we do about it: pick benchmarks where single models score low, and make the team add checking instead of hand-offs. We report the result whichever way it goes.

**I-7 · Nov 14, 12:20, updated 12:32 · Open · No real answer has been graded yet.**
The adapter is written and all five of its tests pass, including one that feeds the benchmark's reference grader a placeholder answer and sees it fail. No answer from an agent has been graded yet.

**I-8 · Nov 14, 12:31, fixed 12:47 · Fixed · The director could not run on Fable 5.2.**
The installed Claude command-line tool (2.1.285) only reaches Fable 5.1; Fable 5.2 needs version 2.1.290 or newer. The tool was upgraded to 2.1.311 and a test call confirmed Fable 5.2 answers. The director runs on Fable 5.2 and the workers on Opus 5.5. Reports name the exact model each agent used.

**I-9 · Nov 14, 12:47 · Fixed · Agents could not run shell loops, so they could not open the data.**
Found by the first plumbing test (`runs/smoke1`, one problem, cheapest model): all four agents stopped within 40 seconds, with no memory writes, no searches and no answer. Cause: in the agents' permission mode the command-line tool refuses any shell command it cannot analyse ahead of time (a loop, a `cd`, a variable), and the first thing an analyst does is loop over the compressed data files. Fix: shell commands are allowed to run, and the operating-system sandbox, not the command check, is what confines them. A new live check (`test_live_loops_and_cd_run_but_stay_confined`) shows loops now run inside the agent's folder and a loop writing outside it is still refused. All six live sandbox checks pass on the upgraded tool (version 2.1.311).

**I-10 · Nov 14, 13:36 · Fixed · Analysts had no useful time limit and filled the machine.**
Found by the second plumbing test (`runs/smoke2`): 40 minutes in, both analysts were still running large bootstrap jobs at about two full cores each, some left in the background, and the limit was an hour. We stopped the run. Changes, applied to lab analysts and to the single-agent baseline alike:
- A 30-minute limit, stated in the instructions, with the rule that only what is in `answer.json` when time runs out counts. Agents are told to write a first answer early and improve it.
- If an agent is stopped by the limit and has left a valid `answer.json`, the lab uses it and records that it was salvaged. A timed-out run is not repeated.
- Each script should stay under about 3 minutes; numeric libraries are capped at two threads per process; agents are told to use at most 2 cores.
- When an agent ends, any job it left running in its folder is stopped.
What this costs: an answer that needed more than 30 minutes is cut short, for both arms equally. Running more than about four analysts at once needs more cores than the laptop has; that is the case for moving analysts to Modal (idea N-1's sandboxes).

**I-11 · Nov 14, 14:13 · Open · Blind teams are not independent if they are the same model with the same briefing.**
Pilot 1, problem gb02: the lab's first "replicated" answer was wrong (O-4). Blinding stops one team copying the other. It does not stop both from making the same mistake for the same reason. Two causes are visible: the teams are the same model, and the director gave both the same briefing. What we will try, and test on problems we have not scored yet (D-14): give the library's briefing to one team only, so the two teams start from different information; and make agreement earn its label, by having the director challenge an agreed answer before it is reported.

**I-12 · Nov 14, 14:33 · Open · The Claude account ran out of usage in the middle of a run.**
The first full lab run on Modal (`runs/dev-v2`, the two development problems on version 2) got through the library step and then every agent returned the same message: "You've hit your session limit · resets 12am (UTC)". The lab treated each as a failed agent and wrote both problems down as "no-answer". That record is wrong: the agents never ran. The run is discarded and is not a result.
Fixed: both runners now recognise the limit message, the lab logs `lab.halted`, starts no further agent, and leaves the study without a final answer so it is re-run later (test: `test_a_usage_limit_stops_the_run_and_records_no_final_answer`).
Correction, 14:46: we first wrote that everything ran on one account. It did not. A check showed the laptop's own sign-in (a Max plan) still answers, while the token saved on Modal returns the limit message. So the token on Modal belongs to a second, smaller account (a Pro plan), and that is the one that ran out, after four Fable attempts and two library runs. The laptop runner is unaffected and the version 2 run was restarted there. Still open: replace the token on Modal with one from the larger account, and keep the smaller one as a spare.
What the discarded run did show: with the gateway on Modal, the library made 14 Exa searches, read 4 pages and wrote 17 facts, every request carrying an identity that Modal attached (the gateway log records the identity source as the X-Verified-User-Data header). So the gateway on Modal, Exa through it, and past.dev through it all work.

**I-13 · Nov 14, 17:27 · Open · The account's Fable limit stopped both test runs once.**
At 5:23pm, twenty minutes into the BioDSA-1K and scBench test runs (26 agents at once, all on Fable 5.2), agents began returning "You've reached your Fable limit. Switch to another model to continue." The halt added after I-12 worked: both runs logged `lab.halted`, started no more agents, and recorded no task as finished, so nothing false was written down and nothing was scored. By 5:26pm Fable answered again on the laptop and on Modal; we do not know whether the limit is on usage in a time window or on how many Fable sessions run at once. The BioDSA-1K run was resumed with 8 agents at once instead of 16. Lost: the first twenty minutes of work on both runs (about $31 as the command-line tool estimates it), because an unfinished task is re-run from the start. If the limit returns, the runs stop again and are resumed again; the design and the task lists do not change.

## 8. Research behind decisions

All sources below were read on Nov 14. This section is what documentation and papers say. What we measured ourselves is in section 9.

| Topic | Source | Used for |
|---|---|---|
| Audience on write, identity on read; rule and fixed audiences; traits | past.dev Concepts, https://past.dev/docs/memory-api/concepts | Section 3, D-4 |
| What a leaked key reaches; projects versus audiences; pass-downs must be new facts | past.dev Access control, https://past.dev/docs/memory-api/access-control | I-1, I-2, section 3 |
| Background processing, pending reads, "as of" reads, credits and rate limits | past.dev How requests behave, https://past.dev/docs/memory-api/how-it-works | I-3, N-3 |
| Exact request fields; request log and trace routes | past.dev Endpoint reference, https://past.dev/docs/memory-api/api-reference | Section 3 |
| Connect tokens and the verified identity header; outbound allowlists | Modal, Sandbox networking and security, https://modal.com/docs/guide/sandbox-networking | N-1 |
| Injecting a key from outside the sandbox (Beta) | Modal, Sandbox secret injection, https://modal.com/docs/guide/sandbox-secret-injection | N-1 |
| Snapshot a sandbox and start several from it | Modal, Sandbox snapshots, https://modal.com/docs/guide/sandbox-snapshots | N-2 |
| Which Exa endpoint for which job | `build-with-exa` skill, `.agents/skills/build-with-exa/SKILL.md` | D-3 |
| A summary can hide a restricted or untrusted source, so access limits must follow a fact through everything derived from it | Wang et al., "MAP-Graph: Provenance-Aware Shared Memory for Multi-Agent Workflows", Aug 2026, https://arxiv.org/abs/2608.10509 (found with Exa; abstract read, not the full paper) | Pass-downs record their source, section 3 |
| Four ways shared agent memory fails: leaks, stale facts spreading, contradictions that persist, lost provenance. Their live test found a scope rule that held on one API path and not on another | Margalit et al., "Governed Shared Memory for Multi-Agent LLM Systems", Jun 2026, https://arxiv.org/abs/2606.24535 (found with Exa; abstract read, not the full paper) | Why we test boundaries with real calls instead of trusting the docs (M-1) |

| Models "notice the relevant local diagnostic clue" but treat it as data cleaning instead of changing the method; best published model passes about a third | OpenAI, GeneBench-Pro (June 2026), https://openai.com/index/introducing-genebench-pro/ and bioRxiv 2026.06.29.735386 (read by a scout through Exa; summarised in `research/scouting/`) | Step 5 in section 1, D-9 |
| A one-shot team did worse than one agent on the same model (32.4% against 38.2%); a team that learned across rounds did better (43.1%) at about 34 times the cost | Mimosa on ScienceAgentBench, Mar 2026, https://arxiv.org/abs/2603.28986 (via scout) | I-6 |
| Extra agents give negative returns once one agent already scores above about 45% | "Towards a Science of Scaling Agent Systems", Google Research and MIT, https://arxiv.org/abs/2512.08296 (via scout) | I-6, choice of benchmarks in D-9 |
| Removing the code reviewer from a six-agent team cut its score from 60.5% to 25.0% | GenoMAS on GenoTEX, ACL Findings 2026 (via scout) | Why the lab's extra agents are checkers |
| About 40 benchmarks compared on size, grading, compute, published scores and failure causes | Our scouting reports: `research/scouting/study_scale_benchmarks.md`, `short_task_benchmarks.md`, `latch_benchmarks.md`; every source link is in them and every search is in `records/exa_calls.jsonl` | D-9 |

**past.dev facts we are building on** (from the pages above):

- A read returns the shared set plus the memory of every audience the reader's identity is in, and nothing else.
- A derived memory is never readable by more identities than the facts it rests on.
- Each derived memory has a kind. A statement the source gives as a guess is marked `hypothetical`, an unadopted option `proposed`, a withdrawn one `retracted`. Useful for a lab, where a hypothesis must not be read as a finding.
- A later value for the same property supersedes the earlier one, and the read says when.
- Every call gets a request id and appears in a request log, so each read an agent makes can be cited as evidence.
- Costs and limits on the starter plan: one credit per 350 bytes written, one credit per read, 500 requests a minute, up to 8,000 tokens of evidence per read, one project.

## 9. Measurements

Each entry gives the command, what came back, and what it does not show.

**M-1 · Nov 14, 11:56 · Measured · past.dev read boundaries hold for honest identities.**
Command: `python3 checks/memory_boundary.py`. Record: `checks/results/memory_boundary_1114115657.json`, which lists the past.dev request id of every read.
The script builds two departments with one worker each and one head, using the rule from section 3, writes one fact per department and one fact with no audience, then reads as each agent.

| Check | Expected | Got |
|---|---|---|
| Worker reads its own department's fact | yes | yes |
| Worker reads the other department's fact | no | no |
| Head reads department A's fact | yes | yes |
| Head reads department B's fact | yes | yes |
| Unknown identity reads department A's fact | no | no |
| Fact with no audience is read by the other department's worker | yes | yes |
| Fact with no audience is read by an unknown identity | yes | yes |
| After a head's pass-down, the other department's worker reads the value | yes | yes |
| ... and still does not get department A's original note | no | no |

9 of 9 as expected. Timings are under I-3.
What it does not show: one run, four short facts, and every agent named its own identity honestly. It says nothing about an agent that names another identity (I-1). The check data was deleted afterwards.
Also seen: with this little memory, a read returned everything the reader was allowed to see, whatever the question. The worker in department B asked about department A's value and got six results, none containing it. So an agent must check what a result says; getting results back does not mean the question was answered.

**M-2 · Nov 14, 11:57 · Measured · Exa key works.**
One search with highlights, three results: HTTP 200 in 2.1 seconds, cost reported by Exa $0.007. A follow-up call fetching the text of two pages cost $0.002. The search returned the two papers now listed in section 8.

**M-3 · Nov 14, 11:58 · Measured · Modal login works.**
Started one sandbox, ran one command in it and shut it down: 3.7 seconds end to end with Modal client 1.6.4. This only shows the account works. Idea N-1 is not tested yet.

**M-4 · Nov 14, 12:17 · Measured · Benchmark scouting with Exa.**
Three Opus scouts, each told to use only the lab's Exa tool. Together with the first test search: 94 Exa calls costing $0.36 in total, all logged in `records/exa_calls.jsonl` with who asked and why. Output: three reports covering about 40 benchmarks. What this does not show: the scouts read papers and leaderboard pages, they did not run any benchmark, so every number in the reports is a published number, not ours.

**M-5 · Nov 14, 12:31 · Measured · The memory layer keeps its limits on past.dev, through our own code.**
Command: `LAB_LIVE=1 python3 -m pytest tests/test_memory.py -k live` (47 seconds; run by the builder agent, not repeated by the director). Two departments, one worker each, one head. Each worker reached only its own department, the head reached both, and after a pass-down the other worker saw the new fact and not the original. The test data was deleted afterwards. Offline, `python3 -m pytest tests/ -q -k "not grader"` gives 59 passed, 6 skipped (the skipped ones are the live tests).

**M-6 · Nov 14, 12:31 · Measured · An agent cannot leave its folder, read a secret, or reach the open web.**
Command: `LAB_LIVE=1 python3 -m pytest tests/test_backend.py -k live` (57 seconds, about $0.11, on the cheapest model; run by the builder agent). Five checks, all held:

| Check | Result |
|---|---|
| Agent writes its result file and reports a session id | yes |
| Agent told to write a file outside its folder (three ways) | refused, file not created |
| Agent told to print a decoy secret outside its readable paths (file tool and shell) | refused |
| Agent told to fetch a web page | blocked; a local address was reachable |
| Agent leaves no result file | resumed exactly once, then wrote it |

What it does not show: these ran in a temporary folder, not under the repository where real runs live, and on Haiku, not Opus. The first real run repeats the check in place. This is what makes "blind" mean something: a team has no path to the other team's folder, the keys or the web.

**Note on grading (I-7).** The benchmark's reference grader was executed once, inside a builder agent's run of the whole test suite, on a placeholder answer that failed as it should. No real answer has been graded.

**M-7 · Nov 14, 13:36 · Measured · The chain works end to end up to the blind analysis (plumbing test, cheapest model).**
Command: `python3 -m lab.run lab --benchmark genebench --studies gb01 --run-dir runs/smoke2 --memory past --backend claude --model haiku --director-model haiku --max-searches 4`. Stopped by hand after 45 minutes (I-10), so the comparison, question round and referee steps have so far run only in tests with scripted agents.

| Step | What the log shows |
|---|---|
| Library | 4 Exa searches through the gateway, 8 pitfalls written to memory, 80 seconds |
| Director | 3 reads of the library's memory, 7 items chosen to pass down, 48 seconds |
| Delivery | 14 pass-downs written by the lab under the director's name (7 to each team) |
| Wait for memory | About 2 minutes 10 seconds for past.dev to finish processing 22 new facts before the teams started (issue I-3, now measured at this size) |
| Teams A and B | Each read its own memory once through the gateway; team A wrote 6 facts, team B wrote 10 (plans and warning signs) |

Every memory and search call in this table went through the gateway under the agent's token. The answers from this run mean nothing: it used the cheapest model and was stopped early.

**M-8 · Nov 14, 14:13 · Measured · Pilot 1: nobody passed either problem.**
Commands: `python3 -m lab.run lab --benchmark genebench --studies gb01,gb02 --run-dir runs/pilot1 --memory past --backend claude --parallel 4 --max-searches 10`, then `baseline ... --attempts 4 --model opus`, then `grade`, `measures`, `report`. Director on Fable 5.2, everyone else on Opus 5.5, 30-minute limit per agent, on the laptop. Scored by GeneBench's reference grader after every agent had exited. Records: `results/pilot1/`.

| | Problem gb01 | Problem gb02 |
|---|---|---|
| Lab's label | unresolved (teams disagreed twice, referee picked neither, third team agreed with no one) | replicated |
| Lab's final answer | fail (1 of 4 fields right) | fail (0 of 4) |
| Single agent, 4 attempts | 0 of 4 passed (each 1 of 4 fields right) | 0 of 4 passed |
| Plain vote over the attempts | fail | fail |
| Any candidate answer passing | none of 6 | none of 2 |

| Measure | Result |
|---|---|
| Lab against single agent | 0 of 2 problems against 0 of 8 attempts: no difference |
| False agreement | 1 of 1 replicated answers was wrong |
| Rescues and damage | none either way: no step turned a failing answer into a passing one, or the reverse |
| Selection | no passing answer existed, so nothing to select |
| Noticing to acting | 25 warning signs recorded; 8 questions written, none blocked by the leak check, 8 delivered; 1 of 9 replies changed a method |
| Library | 18 pitfalls, each with a source. Teams reported 32 of 39 pitfall checks as "found in the data" and 31 as "changed the method"; that is too high to believe and we treat this self-report as unreliable |
| Cost | Lab: 13 agent runs, 34 minutes of agent time, $8.16. Single agents: 8 runs, 41 minutes, $10.51. Dollars are the command-line tool's own estimate |
| Waiting on memory | About 5 minutes before the teams started and 3.5 minutes before the question round, for past.dev to finish processing pass-downs |

What this shows: the whole loop runs with real agents, including the disagreement path, the leak check, the referee and the third team; and the lab cost about as much as four single attempts. What it does not show: any advantage for the lab. Two problems are far too few to say there is none (I-5), and on the full benchmark more than half the problems are failed by every published model.

**M-9 · Nov 14, 14:21 · Measured · First real run on Modal; Fable alone also fails both development problems.**
Command: `python3 -m lab.run baseline --benchmark genebench --studies gb01,gb02 --attempts 2 --model fable --run-dir runs/dev-fable --backend modal --parallel 4`, then `grade`. Four agents, each in its own Modal sandbox, started in the same second and finished in 4.5 to 5.5 minutes. The transcripts name the model as claude-fable-5-2. Records: `results/dev-fable/`.

| | Problem gb01 | Problem gb02 |
|---|---|---|
| Fable 5.2 alone, 2 attempts | 0 of 2 passed (partial scores 0.38 and 0.25) | 0 of 2 passed |
| Opus 5.5 alone, 4 attempts (M-8) | 0 of 4 passed (0.25 each) | 0 of 4 passed |
| Lab, version 1 (M-8) | fail (0.25) | fail (0) |

Two things this adds. First, on gb02 every single agent on both models gave almost the same four numbers, and all were wrong: the blind spot is shared across models, not only across teams (I-11). The lab's two teams reached a different answer from the single agents, also wrong. Second, the Modal runner works end to end for single agents with a real model; the full lab flow on Modal is still untested.
What it does not show: anything about the eight test problems. Partial scores are the grader's own number and are reported only as a finer view; pass or fail is the result.

**M-10 · Nov 14, 15:03 · Measured · Version 2 on the two development problems: still no pass, but no false "replicated".**
Command: `python3 -m lab.run lab --benchmark genebench --studies gb01,gb02 --run-dir runs/dev-v2-local --memory past --backend claude --parallel 4 --max-searches 10`, then `grade` and `measures` (single-agent attempts reused from pilot 1). On the laptop. Records: `results/dev-v2/`.

| | Problem gb01 | Problem gb02 |
|---|---|---|
| First comparison | teams disagreed | teams agreed |
| What followed | 8 questions, still disagreed, referee picked | director's challenge, 8 questions, teams then disagreed, referee picked |
| Lab's label | refereed | refereed |
| Lab's final answer | fail (1 of 4 fields) | fail (0 of 4) |
| Version 1 on the same problem (M-8) | unresolved, fail | replicated, fail |

Other measures: 16 questions written, none blocked by the leak check; 6 of 14 replies said the question changed the method; no step turned a failing answer into a passing one or the reverse; 16 agent runs, 38 minutes of agent time, $8.79; no multi-minute waits on memory.
What it shows: the new steps run with real agents, and the one false "replicated" of version 1 is gone. What it does not show: any gain in correct answers. These two problems have now been failed by the lab twice and by both models alone.

**M-11 · Nov 14, 15:31 · Measured · The test: lab 3 of 8, the same as a vote over four Opus attempts; Fable alone did better.**
Commands, all on Modal, each agent in its own sandbox: `python3 -m lab.run lab --benchmark genebench --studies gb03,...,gb10 --run-dir runs/test-v2 --memory past --backend modal --parallel 12 --max-searches 10`; `baseline ... --attempts 4 --model opus --run-dir runs/test-v2`; `baseline ... --attempts 2 --model fable --run-dir runs/test-v2-fable`; then `grade` and `measures`, run once, after every agent had exited. Design frozen beforehand (D-17). Records: `results/test-v2/`, `results/test-v2-fable/`.

| Problem | Lab's label | Lab | Opus alone (4 attempts) | Vote over the 4 | Fable alone (2 attempts) |
|---|---|---|---|---|---|
| gb03 | unresolved | fail | 0 of 4 | fail | 0 of 2 |
| gb04 | replicated | **pass** | 4 of 4 | pass | 2 of 2 |
| gb05 | replicated | fail | 0 of 4 | fail | 0 of 2 |
| gb06 | refereed | fail | 0 of 4 | fail | 0 of 2 |
| gb07 | refereed | fail | 0 of 4 | fail | 0 of 2 |
| gb08 | refereed | fail | 1 of 4 | fail | 2 of 2 |
| gb09 | converged | **pass** | 2 of 4 | pass | 2 of 2 |
| gb10 | refereed | **pass** | 3 of 4 | pass | 2 of 2 |
| **Total** | | **3 of 8** | **10 of 32 attempts (31%)**; first attempts alone 2 of 8 | **3 of 8** | **8 of 16 attempts (50%)**; 4 of 8 problems |

| Measure | Result on the eight test problems |
|---|---|
| Lab against one Opus attempt | 3 of 8 against an expected 2.5 of 8 (31%); the first attempts alone passed 2 of 8 |
| Lab against the same-budget vote | 3 of 8 against 3 of 8: no difference. The lab cost about six single attempts per problem and only four exist, so the vote had the smaller budget |
| Lab against the best single model | Fable 5.2 alone passed 4 of 8 problems on both attempts. The lab, whose analysts are Opus 5.5, passed 3. On this test the best single model beat the lab |
| Agreement as evidence | replicated: 1 of 2 passed; converged: 1 of 1; refereed: 1 of 4; unresolved: 0 of 1 |
| False agreement | 1 of 2 replicated answers was wrong (gb05, O-8) |
| Rescues and damage | Question round: 2 team answers went from failing to passing, 0 the other way. Referee: 1 rescue, 0 damage. Challenge: 0 and 0. Third team: 0 and 0 |
| Selection | On all 3 problems where any lab answer passed, the lab reported a passing one. (The rehearsal lab failed exactly here) |
| Noticing to acting | 79 warning signs; 51 questions written, none blocked by the leak check; 20 of 66 replies changed a method |
| Cost | Lab: 62 agent runs, 137 minutes of agent time, $27.06, about 17 minutes of wall time for all eight. Opus alone: 32 runs, 90 minutes, $18.10. Fable alone: 16 runs, 87 minutes, $7.94. Dollars are the command-line tool's estimate |
| Memory and search | 245 facts written and 144 pass-downs delivered through the gateway on Modal; 318 reads; 56 Exa searches and 7 page reads by the eight library agents; every request carried an identity attached by Modal |
| Faults | No agent failed or timed out. 30 of 607 gateway requests were refused as malformed |

**What this shows.**
- The lab made Opus reliable where Opus was unreliable. It passed every problem that a single Opus attempt passes at least half the time (gb04, gb09, gb10), including one where both teams started wrong (O-6).
- It did not extend Opus's reach. On gb08, where Opus alone passed once in four tries, the lab failed; Fable alone passed twice in two.
- A plain vote over four attempts did as well as the lab on this test, for less.
- Every step that changed an answer changed it for the better or not at all: three rescues, no damage.

**What it does not show.** Eight problems. A difference of one problem is 12.5 points, so "3 of 8 against 3 of 8" and "3 against 4" are not differences we can call real. One run per arm. The lab's analysts were Opus; a lab of Fable analysts was not tested, and after seeing these scores it could not be tested on these problems without bias.

**With the two development problems** (version 2 failed both, M-10; Opus alone 0 of 8 attempts; Fable alone 0 of 4): lab 3 of 10, Opus alone 10 of 40 attempts (25%), Fable alone 8 of 20 (40%). Published context on the full 129 problems, with other harnesses: best model about a third; Claude Opus 4.8 between 9% and 16%.

**M-12 · Nov 14, 17:02 · Measured · Pilots of version 3 on the two new benchmarks (development tasks, all agents on Fable 5.2, on Modal).**
Commands: `python3 -m lab.run lab --benchmark biodsa --studies bd01,bd02,bd03,bd04 --run-dir runs/biodsa-pilot --memory past --backend modal --parallel 8`, then `baseline ... --attempts 2`, `grade`, `measures`; the same for `--benchmark scbench --studies sc01,sc07,sc08`. Records: `results/biodsa-pilot/`, `results/sc-pilot/`.

| | BioDSA-1K, 4 tasks | scBench, 3 tasks |
|---|---|---|
| Lab | 3 of 4 | 2 of 3 |
| Fable alone | 4 of 8 attempts; 2 of 4 tasks | 4 of 6 attempts; 2 of 3 tasks |
| Where they differ | bd01: lab right, single wrong twice (O-9) | nowhere: every agent passed sc07 and sc08 and failed sc01 |
| False agreement | 1 of 3 replicated answers wrong (bd04) | 1 of 3 (sc01) |
| Web search | library 48 calls, research on demand 39, team A 1; 29 pages opened | library 36, research on demand 29, team A 0 |
| Research on demand | 11 questions, none blocked, 20 findings delivered | 9 questions, 2 blocked by the leak check, 14 findings delivered |
| Lab cost | 33 runs, 94 minutes of agent time, $10.80 ($2.70 a task) | 25 runs, 131 minutes, $9.75 ($3.25 a task) |
| One single attempt | 55 seconds, about $0.10 | 4 minutes, about $0.11 |
| Faults | none | one sandbox lost by Modal, retried successfully |

What this shows: version 3 runs end to end with real agents, including search by verified identity, budgets, and research on demand; web search is now a real part of the work (on GeneBench the library opened 7 pages in a whole run). What it does not show: seven tasks cannot say whether the lab beats Fable alone. The lab costs about 27 single attempts per BioDSA-1K task; a vote over repeats would not help a model that gives the same answer every time, but the cost gap is real.

**M-13 · Nov 14, 19:15 · Measured · scBench test: the lab with Opus analysts scores 23 of 33, the same as Fable alone and no better than Opus alone.**
Commands: `python3 -m lab.run lab --benchmark scbench --studies sc02,...,sc36 (all but sc01, sc07, sc08) --run-dir runs/sc-test --memory past --backend modal --parallel 10 --model opus` (director on Fable, D-21), `baseline ... --attempts 2` (Fable), `baseline ... --attempts 2 --model opus --run-dir runs/sc-test-opus-single`, then `grade` and `measures` after every agent had exited. Scored by scBench's own grader. Records: `results/sc-test/`, `results/sc-test-opus-single/`. The Opus-alone rows were added at 19:35, when that run was graded.

| | 33 test tasks |
|---|---|
| Lab (Opus analysts, Fable director) | 23 of 33 |
| Fable alone | 44 of 66 attempts (67%); 23 of 33 tasks on the first attempt; 23 of 33 by vote |
| Opus alone (the like-for-like comparison) | 47 of 66 attempts (71%); 24 of 33 tasks on the first attempt; 23 of 33 by vote |
| Lab against Opus alone, task by task | lab right where Opus failed twice: sc29. Lab wrong where Opus passed twice: sc14. Opus passed once in two: sc05 (lab right), sc11 and sc18 (lab wrong). The other 28 tasks: same result |
| Lab right where Fable alone was wrong twice | sc29 (settled by a third team) |
| Lab wrong where Fable alone was right twice | sc14 (reported as unresolved) |
| By label (tasks, passed) | replicated 21, 16; converged 3, 1; refereed 6, 4; third-team 1, 1; unresolved 2, 1 |
| False agreement | 5 of 21 replicated answers failed; the two first answers agreed in 23 tasks and were both wrong in 6 |
| Challenge of agreed answers | 23 challenged, 21 drew questions, 1 changed the agreed answer; rescued 0, damaged 0 |
| Question round | rescued 4, damaged 2 |
| Referee | rescued 3, damaged 1 |
| Third team | rescued 2, damaged 0 |
| Selection | some lab answer passed in 25 tasks; the lab shipped a passing one in 23 (missed sc11, sc14) |
| Web search | library 389 calls, research on demand 262, team A 0; 2 refused by the budget |
| Research on demand | 88 questions, 12 blocked by the leak check, 150 findings delivered; the revision round after it rescued 3, damaged 1 |
| Cost | lab 300 runs, 814 minutes of agent time, $101.33 ($3.07 a task); Fable alone 66 runs, 179 minutes, $9.23 ($0.14 an attempt); Opus alone 66 runs, 122 minutes, $8.45 ($0.13 an attempt) |

What this shows. On scBench the lab added nothing to its own analysts: 23 of 33, against 24 of 33 for one Opus attempt and 23 of 33 for a vote over two. Opus alone also did slightly better than Fable alone here (71% of attempts against 67%), so Opus is not the weaker model on this benchmark. Seven tasks failed in every arm, single or lab, Opus or Fable, with or without web research (sc03, sc04, sc16, sc21, sc25, sc33, sc36): both models share those blind spots, and the lab's research did not remove them. Inside the lab, the steps after the first answers did net good: 9 rescues against 3 damages (question round 4 and 2, referee 3 and 1, third team 2 and 0; the revision round's 3 and 1 are already inside the question round's count). Agreement is evidence but not proof: 16 of 21 replicated answers passed (76%), against 7 of 12 for every other label (58%).

What it does not show. It is not a win: the same score at eleven to twelve times the cost. The rescues did not lift the lab above a single attempt because the lab's first answers started lower: team A (briefed by the library) passed 19 of 33 on its first answer and team B (data only) 21 of 33, against 24 of 33 for one Opus attempt alone. So the library's briefing did not improve first answers here; the later steps then won back what was lost. Differences of two to five tasks in 33 are within what one run per arm can produce by chance. The challenge step, which costs a director run and a round of replies on every agreed answer, changed nothing in the score here.
