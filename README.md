# summit-lab

Entry for Track 3, AI Scientist Swarms, at the SF AI Research Hackathon (Nov 14-15, 2026).

## Summary

summit-lab is a research lab run by AI agents that reproduces computational biology analyses. For each task, a **library** agent researches the method with Exa. A **director** reads the library's findings and passes chosen items to **team A only**. Two analysis teams, A and B, then work blind to each other. Their past.dev memory is separated, so neither can read the other's plan, notes or answer. When the teams agree, the director tries to break the agreed answer before it is reported. When they disagree, the director turns each team's warning signs into questions for the other team, and never passes on an answer. A blind **referee** settles a remaining disagreement by designing and running a test. It may not vote. A third team is called only if the referee cannot settle it. The honest result so far: **the lab does not clearly beat the best single model. It lost to it on one benchmark, tied it on another and beat it by one task on a third.** On the GeneBench-Pro test the lab passed 3 of 8 problems, the same as a plain vote over four Opus attempts, and Fable alone passed 4 of 8. On the scBench test, a lab of Opus analysts with a Fable director passed 23 of 33 tasks, the same as Fable alone and one fewer than a single Opus attempt (24 of 33), at about eleven times the cost. On the BioDSA-1K test, a lab of Fable agents passed 27 of 36 tasks and Fable alone 26 of 36: the lab's first win over its own model, by one task, too small to call real. **On the pre-registered held-out test (M-19), 96 fresh tasks (80 BioDSA-1K, 16 planted faults), the lab passed 89, a vote over Opus attempts at the same cost 87, and Fable alone 91 at about a twelfth of the lab's cost.** The lab's edge over the vote, 2 tasks to 0, is not significant (p = 0.25). On BioDSA the lab, the vote and Opus alone failed exactly the same 6 tasks.

## Results

All numbers come from section 9 of [explanation.md](explanation.md). Pass or fail comes from each benchmark's grader, run once, after every agent had exited. Models: Fable 5.2 and Opus 5.5. Dollars are the Claude command-line tool's own estimate.

| Run | Tasks | Lab | Fable 5.2 alone | Opus 5.5 alone | Cost |
|---|---|---|---|---|---|
| GeneBench-Pro public, test, gb03-gb10 (M-11). Opus analysts, Fable director | 8 | **3 of 8** | 4 of 8 problems (8 of 16 attempts) | 10 of 32 attempts (31%); vote over 4 attempts: 3 of 8 | Lab $27.06; Opus alone $18.10; Fable alone $7.94 |
| scBench, test, 33 tasks (M-13). Opus analysts, Fable director (D-21) | 33 | **23 of 33** | 23 of 33 on the first attempt; 44 of 66 attempts (67%); vote: 23 of 33 | 24 of 33 on the first attempt; 47 of 66 attempts (71%); vote: 23 of 33 | Lab $101.33 ($3.07 a task); Fable alone $9.23 ($0.14 an attempt); Opus alone $8.45 ($0.13 an attempt) |
| BioDSA-1K, test, bd05-bd40 (M-14). All agents Fable (D-20) | 36 | **27 of 36** | 26 of 36 on the first attempt; 104 of 144 attempts (72%); vote: 26 of 36 | not planned | Lab $118.00 ($3.28 a task); Fable alone $15.87 |
| **Held-out, pre-registered (M-19).** Fresh BioDSA-1K draw bh01-bh80 with the label convention note, plus 16 planted-fault tasks. Opus in every lab role | 96 | **89 of 96** (74 + 15) | 91 of 96 (76 + 15), 2 attempts, vote | vote over 4 attempts: 87 of 96 (74 + 13); same-cost vote, 15 or 13 attempts: 87 of 96 | Lab $200.64 ($2.09 a task); same-cost vote $157.91; Opus alone $40.07; Fable alone $17.31 |
| BioDSA-1K pilot, bd01-bd04 (M-12). All agents Fable | 4 | 3 of 4 | 2 of 4 tasks (4 of 8 attempts) | not run | Lab $10.80 ($2.70 a task); one attempt about $0.10 |
| scBench pilot, sc01, sc07, sc08 (M-12). All agents Fable | 3 | 2 of 3 | 2 of 3 tasks (4 of 6 attempts) | not run | Lab $9.75 ($3.25 a task); one attempt about $0.11 |

On the two GeneBench development problems (gb01, gb02) nothing passed: not the lab in either version, not Opus alone, not Fable alone (M-8, M-9, M-10).

**What the lab's steps did.** A rescue turns a failing answer into a passing one. Damage does the reverse.

| Step | GeneBench-Pro test (M-11) | scBench test (M-13) |
|---|---|---|
| Question round (warning signs passed as questions) | 2 rescued, 0 damaged | 4 rescued, 2 damaged |
| Director's challenge of agreed answers | 0 rescued, 0 damaged | 23 challenged, 1 agreed answer changed; 0 rescued, 0 damaged |
| Referee | 1 rescued, 0 damaged | 3 rescued, 1 damaged |
| Third team | 0 rescued, 0 damaged | 2 rescued, 0 damaged |
| False agreement (replicated answers that failed) | 1 of 2 | 5 of 21 |
| Selection (shipped a passing answer when one existed) | 3 of 3 problems | 23 of 25 tasks (missed sc11, sc14) |
| Agent runs | 62 runs, 137 minutes | 300 runs, 814 minutes |

How to read this:

- **GeneBench-Pro.** The lab made Opus reliable where Opus was unreliable. It passed every problem a single Opus attempt passes at least half the time, and none beyond. Fable alone beat it, and a plain vote over four Opus attempts matched it for less money.
- **scBench.** The lab added nothing to its own analysts: 23 of 33, against 24 of 33 for one Opus attempt. The lab's first answers started lower (team A 19 of 33, team B 21 of 33), and the steps after them won that back: 9 rescues against 3 damages. Seven tasks failed in every arm, lab or single, Opus or Fable. Agreement is evidence but not proof: 16 of 21 replicated answers passed (76%), against 7 of 12 for every other label (58%).
- **BioDSA-1K.** Fable alone gives the same answer on every attempt (each task passed 4 of 4 or 0 of 4), so repeats and votes cannot fix anything. The lab's one gain came from the director's challenge of an agreed answer: on bd36 it pointed both teams at two clinical columns they had used only for counting, and both changed to the right answer (O-11). Eight of the nine remaining failures are that same mistake, a settled claim judged "Not verifiable", and the challenge did not catch them.
- **Both pilots.** 1 of 3 replicated answers was wrong in each. When every agent is the same model, a mistake that model makes every time survives the whole lab (O-10).

## How it works

One study, step by step (design version 3, D-19; `lab/protocol.py`):

1. **Library.** It searches with Exa for how the method should be done and what usually goes wrong. It writes pitfalls, each with a source, to the study's own library memory.
2. **Briefing.** The director picks items. The lab passes them down to team A only, so the two teams start from different information.
3. **Blind analysis.** Teams A and B each write a plan to memory first. The timestamp shows the plan came before the analysis. Each team records the warning signs it notices and submits an answer. Team A may search while it works; team B works from the data alone. Numbers within 5% and equal text count as agreement.
4. **Agree: challenge.** The director looks for assumptions both teams made, data neither used and other readings of the task. It sends questions to both teams, and each team must run the check.
5. **Disagree: questions.** The director turns each team's warning signs into questions for the other team. A leak check drops any question that would reveal the other team's answer. The director may also ask up to three research questions, which the library answers from the web for both teams. Each team may revise once.
6. **Referee.** It gets the two answers as X and Y, without knowing whose is whose, and must design and run a test that tells them apart.
7. **Team C.** A third blind team runs only if the referee picks neither answer.

```
 library (Exa) --> director picks items --> team A only
                                              |
 task + data --> team A (briefed, may search) +--> compare
 task + data --> team B (data only) ----------+       |
                                                       |
     agree --> director challenges --> still agree --> replicated
       |                                 |
       |                             now disagree
       v                                 v
   disagree --> warning signs become questions (+ research) --> agree --> converged
                      | still disagree
                      v
         referee: blind X/Y, must run a test --> picks one --> refereed
                      | picks neither
                      v
         team C (blind) --> matches A or B --> third-team
                        \-> otherwise -----> unresolved
```

Outcome labels:

| Label | Meaning |
|---|---|
| replicated | Both blind teams agreed, and still agreed after the director's challenge |
| converged | The teams disagreed, then agreed after the question round |
| refereed | The referee's test picked one answer |
| third-team | Team C agreed with team A or team B |
| unresolved | Nothing settled it; the report keeps every answer and the reason each was rejected |
| single-source | Only one team produced a valid answer |
| no-answer | Neither team produced a valid answer |

## past.dev challenge

### Agents

Every agent is a past.dev identity with two traits: its department and its role. Each study has its own departments (`<study>-library`, `<study>-team-a`, `<study>-team-b`, and so on), so one study's findings never reach another study's teams.

- **Writers.** The library writes pitfalls with their sources. Each team writes its plan before it analyses, then its warning signs and method notes. The lab writes every pass-down under the director's name: briefing items, questions and research findings.
- **Readers.** Each team reads its own memory through the gateway. The director reads everything and was told to use only its own study's facts (D-17).

### Layers

| Who | Can read |
|---|---|
| Worker (library, team A, team B, team C) | Only its own department's audience, plus pass-downs addressed to it |
| Director | Every audience (a rule audience includes any identity whose role is head or director) |
| An identity past.dev has never seen | No department's memory |

- **Pass-downs go through the lab, not through agents.** The gateway refuses a pass-down from any agent. The lab writes each one as a new fact, addressed to the receiving team's audience, after the leak check. Each pass-down records the id of the fact it came from, so every relay can be traced. It must be a new fact: in past.dev, re-sending a fact with another audience moves it instead of sharing it. All writes go through one function in `lab/memory_past.py` that refuses a write without one of the run's audiences (issue I-2).
- **Boundary check.** `checks/memory_boundary.py` builds two departments, one worker each and one head, and reads as each of them. Result in `checks/results/memory_boundary_1114115657.json` (M-1).

### Results

- **Boundary check (M-1): 9 of 9 checks as expected.** Workers read only their own department, the head reads both, an unknown identity reads neither, and a pass-down reaches the other department without exposing the original note. The same limits held through the lab's own code against the live service (M-5).
- **Timing (I-3, M-1).** With four short facts, a writer could read its own fact back after 2.9 seconds. past.dev reported each write fully processed after 7 to 17 seconds, and reads took 0.6 to 2.0 seconds.
- **At run size.** Pilot 1 waited about 5 minutes before the teams started and 3.5 minutes before the question round (M-8). Version 2 waits only until the receiving agent can see its new facts (D-15). The next run, on version 2, had no multi-minute waits (M-10).
- **GeneBench-Pro test (M-11).** 245 facts written, 144 pass-downs delivered, 318 reads, all through the gateway on Modal. Every request carried an identity attached by Modal. 30 of 607 gateway requests were refused as malformed.
- **Leak check.** It blocked no question on GeneBench (51 written). On scBench it blocked 2 of 9 research questions in the pilot and 12 of 88 in the test (M-11, M-12, M-13).

### Caveats

- **I-1: a read boundary is only as strong as the identity on the request.** past.dev trusts the identity the caller names, and one project key reaches every audience. Our answer is that no agent holds the key. Only the gateway does, and on Modal it takes identity from a header Modal sets. The log keeps I-1 open, because no test has had an agent name another identity directly.
- **I-2: a write without an audience is readable by everyone.** M-1 confirmed it: a fact with no audience was read by the other department's worker and by an unknown identity. The single write path above is the guard.
- **I-3: memory is not instant.** Derived memory is built in the background. The delay with many writes queued has not been measured. The event brief asks for tens of documents; one test run wrote 245 facts.
- **Results need checking.** With little memory, a read returned everything the reader was allowed to see, whatever the question. A worker asked about another department's value and got six results, none containing it (M-1).

## Where Exa is used

Agents never hold the Exa key. Every call goes through the gateway (`lab/gateway.py`), which uses `lab/tools/exa.py`.

- **Library, first pass.** Search and page contents for each study's method and pitfalls. Budget: 12 calls (GeneBench used 10).
- **Research on demand.** In a question round, the director may ask up to three research questions. Each passes the leak check, the library answers it from the web, and the findings go to both teams. Budget: 10 calls.
- **Team A's search.** Team A's analyst may search while it works and must record what it learned and from where. Budget: 8 calls per round. Team B never searches.
- **Budgets are enforced by the gateway.** A call over the budget is refused and logged. A dollar cap (`EXA_BUDGET_USD`) stops all calls.
- **Blocked terms.** Search results whose link or title contains a blocked term are dropped. A contents request for a blocked link is refused. Each benchmark supplies its own terms: its names, its real problem or task names, and for BioDSA-1K each task's source study and publication.
- **Call log.** Every call is a line in `records/exa_calls.jsonl`: which agent asked, why, what it asked, what came back and the cost. On Modal, the gateway's log is copied back while it runs.

Measured call counts:

| Run | Library | Research on demand | Team A | Pages opened |
|---|---|---|---|---|
| Benchmark scouting, three scouts (M-4) | 94 calls in total, $0.36 | | | |
| GeneBench-Pro test (M-11) | 56 searches | not in that design | not in that design | 7 |
| BioDSA-1K pilot (M-12) | 48 | 39 | 1 | 29 |
| scBench pilot (M-12) | 36 | 29 | 0 | |
| scBench test (M-13) | 389 | 262 (2 refused by the budget) | 0 | |

Two honest notes. Team A almost never used its own search. And teams' self-reports about the library's pitfalls were too positive to believe (32 of 39 reported as "found in the data", M-8). The planned run with all search off (D-20) is not in yet, so we cannot yet say what search added.

## How Modal is used

- **One sandbox per agent** (`lab/backend_modal.py`). Each agent is a headless Claude Code session in its own Modal Sandbox with fixed CPU and memory limits, and tools limited to Bash, Read, Write, Edit, Glob and Grep. The image holds public benchmark data only: no sealed data, no `.env`, no Exa tool. Two teams are two separate machines with no path between them.
- **The gateway sandbox** (`lab/modal_gateway.py`, `lab/gateway_main.py`). One long-lived sandbox runs the gateway. It is the only place that holds the past.dev and Exa keys.
- **Identity from Modal.** The lab issues each agent a Modal connect token with its agent id as metadata. Modal adds that id to every request in the `X-Verified-User-Data` header, which the agent cannot set. The gateway takes identity from that header alone.
- **Outbound allowlist.** Each agent sandbox can reach only Anthropic's API, the gateway, and the Python package index. The package index is removed for scBench, because one public package there contains answers to an older task set (D-16).
- **Volumes.** scBench and BioDSA-1K data live on the volumes `summit-scbench-data` and `summit-biodsa-data`. Each sandbox mounts only its own task's folder, read-only.
- **Secrets, by name only.** `claude-token` (`CLAUDE_CODE_OAUTH_TOKEN`) for agent sandboxes; `summit-lab-keys` (`PAST_API_KEY`, `EXA_API_KEY`) for the gateway only.
- **Measured.** Four single agents started in the same second and finished in 4.5 to 5.5 minutes (M-9). The eight GeneBench test problems took about 17 minutes of wall time with every agent on Modal (M-11). One sandbox lost by Modal was retried successfully (M-12).
- **Limits.** The Claude sign-in token is inside each agent's sandbox, because the agent needs it to run (D-13). The outbound allowlist is a Beta feature (N-1).

## Fair-play rules (D-11)

- **Neutral ids.** Agents get the task text and the data under neutral names (gb01, sc01, bd01). They never get answers, worked reports, grader settings or the benchmark's own problem names. Downloads stay in `data/<benchmark>/sealed/`, which no agent can read.
- **Sealed answers.** Only the grading step opens the sealed side.
- **No source-study lookups.** No agent, and no session building the lab, looks up a benchmark's source study. The gateway drops results that match the blocked terms.
- **Grade only after all agents exit.** `grade` refuses to run while any agent has started and not finished.
- **Dev/test split, design frozen first.** Development tasks, whose scores we saw: gb01-gb02, sc01, sc07, sc08, bd01-bd04. The design was frozen and written down before the test tasks ran (D-14, D-17, D-20), and test tasks are scored once. The one later change, Opus analysts for the scBench lab, was logged before any scBench test task had finished or been scored (D-21).
- **Official graders.** GeneBench-Pro and scBench are scored by their own reference graders, unchanged (D-12). scBench grading runs in a separate environment no agent can reach. BioDSA-1K ships no grader code, so its score is an exact match on the True, False or "Not verifiable" label. Its second score, evidence alignment, needs a judge model, and we do not use it.
- **Same terms for the single agent.** Same task text, data, time limit (30 minutes) and no web access.
- **Stated plainly (I-4).** The session that built the lab saw GeneBench's folder names while downloading. It did not open the answers or reports, and no folder name appears in any text an agent receives.

## Reproduce

Needs Python 3, the Claude Code command-line tool (2.1.290 or newer for Fable 5.2), and, for the Modal backend, a signed-in `modal` client with the two Modal Secrets above. Environment variables, by name: `PAST_API_KEY`, `EXA_API_KEY`, optional `EXA_BUDGET_USD`, and `LAB_LIVE=1` for live tests. Keys can sit in a local `.env`, which git ignores. Benchmark downloads go in `data/<benchmark>/sealed/` first. Run from the repository root.

```bash
# Stage data: build the public side under neutral ids; put data on Modal volumes
python3 -m lab.adapters.genebench stage
python3 -m lab.adapters.scbench stage
python3 -m lab.adapters.scbench grader-env
modal run lab/adapters/scbench_data.py --sets all
python3 -m lab.adapters.biodsa stage
python3 -m lab.adapters.biodsa upload
python3 -m lab.backend_modal build          # build the agent image once

# Free check of the whole loop with scripted agents
python3 -m lab.run lab --benchmark genebench --studies gb01 --run-dir runs/mock --backend mock

# Run the lab (GeneBench-Pro test, design of D-17; M-11)
python3 -m lab.run lab --benchmark genebench --studies gb03,gb04,gb05,gb06,gb07,gb08,gb09,gb10 \
  --run-dir runs/test-v2 --memory past --backend modal --parallel 12 --max-searches 10 \
  --model opus --no-team-search --no-research

# Run the lab (scBench test, D-21; M-13). The director defaults to Fable
SC_TEST=sc02,sc03,sc04,sc05,sc06,sc09,sc10,sc11,sc12,sc13,sc14,sc15,sc16,sc17,sc18,sc19,sc20,sc21,sc22,sc23,sc24,sc25,sc26,sc27,sc28,sc29,sc30,sc31,sc32,sc33,sc34,sc35,sc36
python3 -m lab.run lab --benchmark scbench --studies $SC_TEST --run-dir runs/sc-test \
  --memory past --backend modal --parallel 10 --model opus

# Run the lab (BioDSA-1K pilot; M-12). Add --no-search for the search-off run
python3 -m lab.run lab --benchmark biodsa --studies bd01,bd02,bd03,bd04 --run-dir runs/biodsa-pilot --memory past --backend modal --parallel 8

# Single-agent baselines, same tasks and limit
python3 -m lab.run baseline --benchmark genebench --studies gb03,gb04,gb05,gb06,gb07,gb08,gb09,gb10 \
  --attempts 4 --model opus --run-dir runs/test-v2 --backend modal
python3 -m lab.run baseline --benchmark scbench --studies $SC_TEST --attempts 2 --model fable \
  --run-dir runs/sc-test --backend modal

# Grade (only after every agent has exited), measure, report
python3 -m lab.run grade --run-dir runs/test-v2
python3 -m lab.run measures --run-dir runs/test-v2
python3 -m lab.run report --run-dir runs/test-v2

# Tests and checks
python3 -m pytest tests/ -q -k "not grader"                     # offline
LAB_LIVE=1 python3 -m pytest tests/test_memory.py -k live       # live past.dev
LAB_LIVE=1 python3 -m pytest tests/test_backend.py -k live      # live agent sandbox
python3 checks/memory_boundary.py                               # past.dev read boundaries (M-1)
```

Run folders under `runs/` stay local. The graded records of each run are in `results/` (`results.json`, `measures.json`, `events.jsonl` and one `study.json` per task).

## Limits and what we would do next

- **Small samples, one run per arm.** 8 GeneBench problems, 33 scBench tasks, 7 pilot tasks. A one-task difference is 12.5 points on GeneBench.
- **No clear win.** GeneBench: Fable alone beat the lab. scBench: the same score as Fable alone and as Opus alone, at about eleven times the cost. BioDSA-1K: one task better than Fable alone, at about seven times the cost. A lab of Fable analysts was never tested on GeneBench, and it cannot now be tested there without bias.
- **The briefing did not help first answers on scBench.** The team briefed by the library passed 19 of 33 on its first answer; the team with data only passed 21; one Opus attempt alone passed 24.
- **Same model, same blind spot.** Blinding stops copying. It does not stop two copies of one model from making the same mistake (I-11, O-10). The director's challenge broke a false agreement once (O-5) and failed to break others (O-8). On scBench it cost a director run and a round of replies on every agreed answer, and changed nothing in the score.
- **Search's value is not measured yet.** The search-off run (D-20) has not run, and team A barely searched.
- **Usage limits.** The account's Fable limit stopped both test runs once (I-13), and the scBench lab moved to Opus analysts because of it (D-21).
- **Next:** run the search-off comparison; try teams on different models (`--team-b-model`), so the two teams do not share one model's blind spots; test I-1 with an agent that names another identity.

Built entirely during the event. A practice lab was built beforehand in a separate repository; no file was copied from it. The full working log, with every decision, observation, issue and measurement, is [explanation.md](explanation.md).
