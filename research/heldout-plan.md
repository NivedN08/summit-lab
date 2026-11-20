# Held-out test plan (Phase 6)

**Status: decided 2026-11-18 (decisions at the end); not frozen or paid for yet.**

## The question

Does the lab beat a single model on tasks nobody has seen, at a fair cost? Every earlier comparison ran on tasks that had already been looked at, or on a panel built around one failure mode. The held-out test is the first result that can count, so it is pre-registered in full, run once, graded once, and reported whatever it shows.

## What the dev results predict

| | Lab | Opus alone, vote of 4 | Tasks where they differ |
|---|---|---|---|
| Loop-2 panel, convention stated (M-17) | 19 of 20 | 18 of 20 | 1 (bd34, the lab's) |
| Phase 1 panel, plain text (M-15) | 10 and 9 of 20 | 11 of 20 | 1 to 2 |
| BioDSA-1K test, plain text (M-14) | 27 of 36 (all Fable) | Fable alone 26 of 36 | small |

So the lab and a single model disagree on about 1 task in 20. The test is decided only on those tasks: a task both get right, or both get wrong, says nothing about which is better.

**Power.** An exact one-sided sign test at α = 0.05 needs the lab to win at least 7 of 8 discordant tasks, 9 of 10, or 12 of 15. The chance of reaching that, if the lab really is better:

| Tasks | Discordant 5%, lab wins 75% | 5%, 90% | 10%, 75% | 10%, 90% | 15%, 90% |
|---|---|---|---|---|---|
| 56 (the original plan) | 3% | 8% | 16% | 41% | 68% |
| 96 | 12% | 31% | 33% | 75% | 93% |
| 136 | 21% | 54% | 49% | 91% | 99% |

At the dev rate of disagreement, even 136 tasks give at best an even chance. The planted-fault tasks are where disagreement should be higher, since they are built so the careless analysis is wrong. A null result is the most likely outcome, and the report must say what size of advantage it rules out, not just "not significant".

## Design

### Tasks

- **BioDSA-1K, fresh draw** (`bh01`...). Drawn with `python3 -m lab.adapters.biodsa stage --split heldout --seed S --n N`.
  - It excludes every pair, study, PMID and hypothesis the dev set used.
  - The seed S is chosen and written into the spec before the draw. It is a number nobody has drawn with.
  - Nobody looks at the label mix. The stage command prints counts only.
  - This draw keeps the benchmark's natural label mix, with about a third "Not verifiable". This is what the dev panel lacked.
- **Planted faults, held-out split** (16 tasks: 8 with a planted fault and 8 clean twins). Already generated and self-checked; `planted-heldout` in the CLI.
- **Not included:** scBench and GeneBench. Every task in them has been seen. They stay as earlier results labelled as such.

### Task text

**Recommended: the convention text for BioDSA, for every arm.** M-17 showed it is the largest single lever, and every arm gets the same paragraph, so the comparison stays fair. The planted tasks keep their own text, for every arm. The BioDSA note describes how BioDSA built its labels, which does not apply to tasks we generated.

The convention could hurt on a natural label mix: it pushes models toward True or False, and a third of the labels are "Not verifiable". So one cheap extra arm measures exactly that: Opus alone on the plain text, on the BioDSA tasks only.

### Arms

| Arm | What it is | Command | Measured cost a task |
|---|---|---|---|
| **Lab** | v3 lab, Opus in every role, exactly the loop-2 incumbent (`runs/loop2/variants/v3-opus`); re-check and guard off, since the loop accepted neither | `lab --config-file ... --prompt-dir ...` | $1.56 |
| **Same-cost vote** (primary control) | Opus attempts with rotated diversity lines, majority vote; k = the lab's dollars a task ÷ dollars an attempt, measured on dev before freezing (about 14) | `arm --arm vote --attempts k` | about $1.56 |
| Opus alone | 4 attempts, vote | `baseline --model opus --attempts 4` | $0.43 |
| Self-challenge | one Opus solver, one challenger, one revision | `arm --arm self --model opus` | about $0.40 (estimate; never run live) |
| Fable alone | 2 attempts, vote | `baseline --model fable --attempts 2` | about $0.20 |
| Opus alone, plain text | 4 attempts, BioDSA only | `baseline --benchmark biodsa-heldout` | $0.43 |

The lab uses Opus in every role, not the Fable director the original v4 plan named. Opus everywhere is the configuration actually measured on dev. A Fable director would be an untested change made at the last step.

### Endpoint and analysis

- **Primary:** the lab against the same-cost vote, on every held-out task (BioDSA draw and planted held-out pooled).
  - The test is an exact one-sided sign test on discordant tasks, at α = 0.05.
  - The same-cost vote is the hardest fair control: the same model and the same dollars, with no lab design.
  - Fixing one comparator in advance avoids choosing the best-looking one afterwards.
- **Secondary**, reported with counts and 95% intervals and no significance claim:
  - the lab against each other arm;
  - each benchmark separately;
  - on the planted tasks, faulted against clean twin, per fault family;
  - the convention's effect: Opus alone with the note against without it, on the same BioDSA tasks.
- **Always reported:**
  - every arm's pass count and dollars;
  - the lab's passes per dollar;
  - the discordant tasks by id;
  - the size of advantage the result rules out: the upper end of a 95% interval for the lab's share of discordant wins.

### Rules

- **Freeze before anything runs.** The spec `prereg/heldout.json` holds the task ids (after the draw), seed, arms, k, caps, endpoint and these rules; `freeze()` hashes it with git HEAD and the lab files. Every command runs with `--prereg`.
- **One run per arm.** No arm is re-run because of its result.
  - A halt from a usage limit, a budget cap or a crash is resumed with the same command.
  - A run that stops without an error of its own is not restarted without the owner's go-ahead.
- **Grade once, after every agent of every arm has exited.**
  - BioDSA is graded by our own exact match.
  - The planted tasks are graded by our own answer key.
  - No third-party grader is involved.
- **The config never changes mid-batch.** If something is broken, the batch is stopped and reported, not quietly fixed.

## Before the paid batch (no spend)

1. **Code.**
   - Register `biodsa-heldout` and `biodsa-heldout-convention` in `lab/run.py`; `_biodsa` gains a split argument.
   - Refresh the per-run cost estimates in `lab/budget.py` from the runs since measured: the Opus baseline estimate is $0.95 against a BioDSA mean of $0.11, which made the Opus-alone run halt twice in loop 2.
   - Add estimates for the arms' new roles, so they don't fall back to $1.50.
   - Each change gets tests, and the golden briefs stay byte for byte.
2. **The draw.** Write the seed into the spec, run the stage command, then upload the tables to the Modal Volume. This downloads public cBioPortal archives and uses a little Modal storage.
3. **The planted tables.** Check that the planted suite is on its Modal Volume, and upload it if not.

## Small paid steps before the batch (dev tasks only)

4. **Smoke test, about $10:** 2 BioDSA dev tasks and 2 planted dev tasks through every arm, under a $10 cap. This is the first live run of the self-challenge and vote arms and of the planted suite. It checks mounts, grading and the cap halting cleanly.
5. **Planted dev pilot, about $35:** all 8 planted dev tasks through the lab and Opus alone.
   - It is the only look at how the arms handle planted faults before the held-out run.
   - It measures the lab's cost a task on planted tables, which sets k for the vote arm.
   - Anything learned here may change the design before freezing, never after.

## The paid batch

| Size | BioDSA draw | Planted | Estimated cost | Power at 10% disagreement, lab wins 90% |
|---|---|---|---|---|
| Small | 40 | 16 | about $250 | 41% |
| **Medium (recommended)** | 80 | 16 | about $430 | 75% |
| Large | 120 | 16 | about $620 | 91% |

The estimates use measured costs a task: $4.15 for the five arms on every task, plus $0.43 for the plain-text arm on each BioDSA task. The smoke test and the pilot add about $45. Each arm gets its own cap, set at its estimate plus headroom for the runs in flight. The batch's total cap is the sum.

The Fable runs are 2 a task: 192 runs at the medium size. They should start at the beginning of a fresh Fable week, so a weekly limit doesn't stop the Fable arm halfway.

## After the batch

- Results go in `results/heldout/`, with an M-entry in `explanation.md`, claims in `claims.json` checked by `verify_results.py`, and ledger entries.
- The README's top screen is rewritten around this result, whatever it shows (Phase 7).

## Decisions (owner, 2026-11-18)

1. **Size: medium.** 80 BioDSA tasks plus the 16 planted held-out tasks, 96 in all.
2. **Task text:** the convention note for every arm on BioDSA, plus the plain-text Opus-alone arm.
3. **Lab director: Opus,** as measured on dev.
4. **Timing:** start the paid batch as soon as the prep steps, the smoke test and the pilot are done. If the Fable arm hits a weekly limit, it is resumed with the same command.

## Result (Nov 20)

Ran Nov 19-20; M-19 in `explanation.md`, records in `results/heldout/`. The lab passed 89 of 96, the same-cost vote 87, Opus alone 87, the self-challenge arm 82, Fable alone 91. Primary endpoint: 2 lab wins (pf13, pf14), 0 losses, p = 0.25, not significant. Deviations, all in the ledger: the BioDSA lab's cap was raised from $145 to $205 and then $220 after a network outage (entries 78, 79); the planted lab's past.dev audiences were deleted so the BioDSA lab could start (I-17).
