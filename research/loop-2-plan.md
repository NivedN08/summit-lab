# Loop 2 plan

**Decision (2026-11-17): Option B.** The owner chose it, subject to its being the best next step. The two literature reviews (`research/lit-review-multiagent.md`, `research/lit-review-self-improvement.md`) support it:
- Over-abstention is the largest lever. BioDSA-1K's own definition of "Not verifiable" is narrow: no relevant variables or tables.
- Giving every arm the same label definition is standard practice for a fair comparison. Wording alone moves accuracy by about 10 points (Sclar et al., ICLR 2024).

Frozen as `prereg/loop-2.json`, with seed moves in `prereg/loop-2-seeds.json`.

## Why loop 1 found nothing

Loop 1 (M-16) tried three moves and kept none. The run records show why. They are the teams' answers and our own pass/fail grades; no sealed file was read.

- **Abstaining is the failure.** The lab answers "Not verifiable" on 9 to 10 of the 20 panel tasks in every run, and is right on 1 of them. When it commits to True or False it is right on 8 to 10 of 10 to 11.

  | Run | "Not verifiable" right | "Not verifiable" wrong | True/False right | True/False wrong |
  |---|---|---|---|---|
  | Phase 1 replicate 1 | 1 | 8 | 9 | 2 |
  | Phase 1 replicate 2 | 1 | 9 | 8 | 2 |
  | Loop 1, m03 | 1 | 8 | 10 | 1 |

- **The re-check cannot help, because the data really lack the variable.**
  - It ran 40 times in m01 and m02, and every agent kept "Not verifiable".
  - The code never refused a True/False answer, because none was given.
  - The agents' reasons were specific:
    - bd28 has the ARAF mutations but no pathway readout.
    - bd14 has 8q and race but no income or ancestry variable.
    - bd33 has a DNA panel with no subtype markers.
- **These claims are about their own dataset.** The benchmark builds its labels as follows (`lab/adapters/biodsa.py`, from the paper's section 2.4):
  - True and False come in pairs from one finding of the study, the claim and its reversal.
  - "Not verifiable" is a claim from another publication attached to an unrelated dataset.

  The failed claims match their dataset's cancer type, cohort and genes, so the benchmark counts them as settled. Our cBioPortal archives are today's versions and can lack the processed variables the study used.
- **Later steps still damage good first answers.** In m03 both teams answered bd28 correctly, but the challenge round moved team A to "Not verifiable", and the final answer followed. The damage guard, which is off in the incumbent, exists to block exactly this.
- **Single changes rarely clear T = 3.** Each move here changed one thing, and its effect was 1 task or less. With one change per move, two changes that each rescue one or two tasks are never kept, because neither clears T alone.

## The decision this needs: what "Not verifiable" means

**Option A, data only (as now).** "Not verifiable" stays whenever the data lack a variable the claim needs.
- Loop 2 searches only lab-design moves: the guard, the challenge, the first-round method.
- Expected gain is small: at most the bd28-type damages, about 1 to 2 tasks. That is likely under T.

**Option B, the benchmark's own convention, for every arm.**
- One paragraph, close to the paper's definition, is added to the task text of every arm: the lab and the single models alike:
  - "Not verifiable" means the hypothesis is not about this dataset: the data hold no relevant variables or tables for it.
  - A hypothesis about this study's own cohort, genes or measurements is settled even when a variable it needs is missing.
  - In that case, decide True or False from the closest evidence in the data and then from published knowledge, and say which.
- Because every arm gets the same text, any lab-over-single-model gain still comes from the lab's design, not from the hint.
- Scores rise for all arms and stop being comparable with earlier runs. Every claim says "with the benchmark's label convention stated".
- It needs a new incumbent run and a new single-model reference under the new text.

A third option, the convention for the lab only, is excluded: it would make the comparison unfair.

## Loop 2 design (both options)

### Code first (no spend)

1. **The proposer sees evidence, not just counts.** For each failed panel task it gets the teams' and the re-check's stated reasons (trimmed). It also gets the table above of which labels were right. It never sees labels beyond the outcome kinds it already has.
2. **Bundled moves.** A move may combine up to two changes of the existing kinds, so pairs like m03 plus the guard can be tested as one.
3. **Seed moves, fixed in the spec.** The first moves are written into the spec in advance and evaluated before the proposer takes over:
   1. the guard on;
   2. m03's analyst edit plus the guard;
   3. the challenge of agreed answers off. Phase 1 found it net harmful: 1 rescue, 3 damages.
4. **`--max-rejections`** becomes a flag. Loop 2 stops after 4 rejections in a row, not 3.

All new switches default to off, so the golden briefs stay byte for byte. Each change gets tests.

### Runs

| Step | Option A | Option B |
|---|---|---|
| Incumbent | v3-opus, Phase 1 replicate 1 (no new run) | v3-opus plus the convention note: one new full run with first-round snapshots (about $40) |
| Single-model reference | Opus alone, Phase 1 (no new run) | Opus alone, 4 attempts, with the note (about $9) |
| Loop | seeds, then proposer | seeds, then proposer |

### Rules (unchanged from loop 1 except where noted)

- **Panel:** the same 20 BioDSA-1K dev tasks, so T = 3 from M-15 still applies.
- **Acceptance:** a move is kept only if all of these hold:
  - net rescues ≥ 3;
  - damages ≤ 1;
  - cost ≤ 1.25 times the incumbent's;
  - a second replicate with net > 0.
- **Stop:** after 8 moves, 4 rejections in a row (changed from 3), or the cap.
- **Caps:** $50 per evaluation run. The total is $300 for Option A, or $350 for Option B ($50 for the new incumbent and reference, $300 for the loop).
- **Known-noisy tasks:** bd36 and bd38 flipped with no change at all. A flip on them is reported as such, but the acceptance rule is not changed after the fact.

### What would count as success

A kept move clears T on the dev panel and a second replicate. It then still has to win in the held-out test (Phase 6): the fresh BioDSA draw plus planted faults, against Fable alone, Opus alone, self-challenge and a same-cost vote. A dev-panel win alone is not a result.

## What the literature added, and what loop 2 does with it

Built for loop 2:
- **Failure reasons for the proposer, not counts.** GEPA-style text feedback beats scores alone. A 2026 ablation found that scalar feedback turned a held-out gain negative.
- **Bundles of two changes.** A 20-task panel detects only about 4 to 5 tasks net, so single small moves are close to invisible.
- **The guard and challenge-off seeds.** Challenge rounds flip correct answers more often than wrong ones (Wynn et al., 2025; Laban et al., 2023). Changes should rest on checkable evidence.
- **The scoring sentence in the convention note.** Stating that a wrong abstention counts as wrong is the published fix for over-abstention (Kalai et al., 2025). It is a fact about the benchmark's grading, so every arm gets it.

Left for later (each would change loop 1's acceptance rule or needs new code or tasks):
- Confirm a move by re-running only the tasks that flipped, 2 more times each, instead of a full second replicate.
- Screen a move cheaply on the 4 to 6 tasks it targets before a full evaluation.
- A majority vote over k = 3 final verdicts as a move. Voting explains most of debate's gains (Choi et al., NeurIPS 2025).
- A deterministic gate on abstentions: a team that abstains must name the missing variable, and code checks the tables for it.
- "Retry, not abstain" when an analysis's code fails. In BioDSA-1K, 87% of failed-code runs ended in "Not verifiable".
- Keep an archive of variants instead of one incumbent line.
- An experience library built from tasks outside the panel.
- Fine-tuning or RL: out of budget (about $10,000 or more per run).
