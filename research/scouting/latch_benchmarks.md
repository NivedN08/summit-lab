# LatchBio benchmarks (benchmarks.bio): scouting notes

Scout: scout-latch. Researched 2026-11-14 with `lab/tools/exa.py` (about 24 calls). Leaderboard data on the site is dated 2026-11-13.

## The one thing to know first

**None of the 21 leaderboards can be reproduced from outside.** Every benchmark keeps its full task set private to stop training contamination. The public repos ship only a handful of example tasks (2 to 16 per benchmark). The published leaderboard numbers are on the full private sets. So a score we get on the public examples is not directly comparable to a leaderboard number.

What we *can* do honestly this weekend: run the public examples with (a) a strong single model in a standard harness and (b) our team of agents, same tasks, same grader, 3 attempts each, and compare those two. LatchBio has already published per-run single-model results on the public scBench and SpatialBench examples, which gives a second baseline for free.

Other cross-cutting facts:

- **Grading** is almost always automatic and deterministic (a number inside a tolerance band, a set overlap, an exact label match). A task passes only if every answer field is inside tolerance. Exceptions: RareDiseaseBench (rubric), OncologyBench (rubric on the final decision), Refusal (a judge model). Source: https://github.com/latchbio/scbench, https://benchmarks.bio/results/v1/index.md
- **Answer leak risk is real.** The public eval JSON files hold the ground truth inside the `grader.config` field (format shown in https://github.com/latchbio/latch-eval-tools). Our agents must never see the eval JSON; only the prompt and the data. A third-party guide also warns that two fields in each public eval file hold the answer and one holds a contamination canary (https://heurekaskills.com/latch-bench/). The scBench README's "custom agent" example writes answer values for a public QC task, so do not show the README to agents either.
- **Harnesses on the boards** are all single-agent products: Pi, Claude Code, OpenAI Codex, Grok Build (and mini-swe-agent in older runs). I found **no multi-agent or checker-style team on any leaderboard**. OncologyBench is the only benchmark whose own design uses several agents (parallel task agents plus a final decision agent).
- **LSVP rows (marked \*)** are Anthropic's safety-filter-exempt variant. Standard Claude runs are often refused on therapeutics and biosecurity tasks. Example: Claude Opus 5.5 (Pi) scores 1.47% on TxBench-Oligo and 8.33% on TxBench-Antibody-Discovery without LSVP, against 59.88% and 66.67% with LSVP (https://benchmarks.bio/results/v1/latest.csv). If our team runs on standard Claude, avoid those benchmarks.
- **Internet access was removed** in V1 after LatchBio saw agents searching for answers and inventing emails for NCBI. Scores fell about 3.6 points on average; EpiBench fell about 15 (https://blog.latch.bio/p/continuous-benchmarks-for-biology). We should also run offline to be comparable.
- **Data access:** public example data sits in LatchBio storage and, per a third-party check, 563 of 582 data handles resolve to a download link without an account (https://heurekaskills.com/latch-bench/). Not verified by me.
- **Licence:** benchmark repos are Apache 2.0 or a modified Apache 2.0. The shared grader library `latch-eval-tools` is reported as "all rights reserved" (https://heurekaskills.com/latch-bench/; not verified by me). The grading rule is written in each eval file, so a grader can be re-implemented.

## Summary table

"Best" is the top published V1 score. "Best std" is the top run without the LSVP exemption, when different. "Public" is the number of public example tasks. Runnable means: public tasks with data and a published grader that we could run today.

| Benchmark | Tasks (V1 / public) | Grading | Best published (model, harness) | Runnable today | Compute (public set) | Verdict |
|---|---|---|---|---|---|---|
| scBench | 195 / 6 (+30 older tasks in Inspect Evals) | Auto, deterministic | 63.9% GPT-6 Astra, Pi | Yes | 6 files, 9 MB to 1.6 GB; 30-task set is 17.2 GB | **Top pick** |
| SpatialBench | 115 / 16 | Auto, deterministic | 73.3% GPT-6.1 Terra, Pi | Yes | 23.5 GB total; two 9.6 GB files | **Top pick** (skip the 2 huge files) |
| SpatialBench-Long | 21 / 4 | Auto endpoint + rubric diagnostics | 40.5% Opus 5.5\* CC / GPT-6.1 Terra, Pi | Run yes, score no (no grader public) | not found | Big headroom, can't score |
| scBench-Long | 22 / 4 | Auto endpoint + rubric diagnostics | 36.9% Opus 5.5\*, Pi; std 31.8% Sonnet 5.5, CC | Run yes, score no | not found | Big headroom, can't score |
| EpiBench | 106 / 7 | Auto, deterministic | 39.6% Opus 5.5\*, CC; std 39.0% Opus 5.5, Pi | Run yes, score no (no grader public) | not found | Maybe, if we hand-check |
| VariantBench | 118 / 8 (6 in scored set) | Auto, deterministic | 52.8% Opus 5, Claude Code | Yes (grader public) | not found | **Third pick** |
| MetagenomicsBench | 100 / 4 | Auto, deterministic | 62.7% Opus 5.5\*, CC; std 60.0% Sonnet 5.5 | Unclear (grader not confirmed) | not found | Good failure profile, tiny public set |
| TxBench-Antibody-Discovery | 100 / 7 | Auto, deterministic | 67.7% Opus 5.5\*, CC; std 63.7% Gemini 4 Argon | Unclear | not found | Refusal risk on standard Claude |
| TxBench-Antibody-Development | 60 / none found | Auto, deterministic | 39.4% Grok 4.7, Grok Build | No | not found | No |
| TxBench-Preclinical-Pharmacology | 100 / 7 | Auto, deterministic | 65.3% Opus 5.5\*, Pi; std 64.7% Gemini 4 Argon | Unclear | not found | Refusal risk |
| TxBench-Oligo-Discovery | 113 / 7 | Auto, deterministic | 62.2% Opus 5.5\*, CC; std 52.5% GPT-6.1 Sol, Pi | Unclear | not found | Refusal risk |
| NeuroscienceBench | 91 / 3 | Auto, deterministic | 41.0% Fable 5.2\*, CC; std 27.5% Grok 4.7, Grok Build | Unclear (3 tasks) | not found | Too few public tasks |
| RareDiseaseBench | 35 / 2 (not from scored set) | Rubric (all items must pass) | 43.8% Gemini 4 Argon, Pi | No (2 look-alike tasks) | 6 h run limit | No |
| TissueMicroscopyBench | 49 / none found | Verifiable answer | 34.0% Fable 5.2\*, Pi; std 25.9% GPT-6.1 Terra | No | not found | No |
| OncologyBench | 25 / none found | Auto on tasks + rubric on decision | 50.7% Opus 5.5\*, Pi; std 40.0% Opus 5, Pi | No | not found | No (but its design is multi-agent) |
| BioSecBench-Surveillance | 102 / 7 (no data, no grader) | Auto, deterministic | 60.8% Opus 5.5\*, Pi; std 59.8% Gemini 4 Argon | No (restricted) | not found | No |
| BioSecBench-RespiratoryVirus-Surveillance | 110 / none found | Task-specific criteria | 36.4% Opus 5.5\*, Pi; std 25.4% Opus 5.5, Pi | No | not found | No |
| BioSecBench-Structure | 58 / none found | Expert reference answer | 52.3% Fable 5.2\*, Pi; std 43.7% Fable 5.2, CC | No | not found | No |
| BioSecBench-RespiratoryVirus-Countermeasures | 64 / none found | Task-specific criteria | 33.3% Fable 5.2\*, Pi; std 30.2% Gemini 4 Carbon | No | not found | No |
| BioSecBench-Function | 111 / 7 (no data, no grader) | Auto, deterministic | 73.6% Opus 5.5 (std), Pi | No (restricted) | not found | No |
| BioSecBench-Refusal | 107 / not checked | Judge model (MiniMax M3) | 62.4% Grok 4.7, Pi (safeguards score) | No | n/a | Not a capability benchmark |

CC = Claude Code. Scores: https://benchmarks.bio/results/v1/latest.csv for the 13 benchmarks in the overall score; each benchmark's own page (links below) for the other 8. Public task counts for scBench, SpatialBench, EpiBench, VariantBench and the two Long sets come from a third-party audit dated 2026-08-28 (https://heurekaskills.com/latch-bench/); the rest come from each repo's README.

## Overall leaderboard

- Top overall (13 benchmarks): Claude Opus 5.5\* (Pi, LSVP) 58.0%; Opus 5.5\* (Claude Code, LSVP) 57.7%; Claude Sonnet 5.5 (Pi) 54.2%; Fable 5.1\* (Pi) 53.0%; Gemini 4 Argon (Pi) 52.4%; GPT-6 Astra (Pi) 51.3%. Source: https://benchmarks.bio/results/v1/index.md
- 13 of 20 capability benchmarks count toward the overall score. TxBench-Antibody-Development, NeuroscienceBench, RareDiseaseBench, TissueMicroscopyBench and three BioSecBench sets are reported but excluded. Refusal is a separate "Safeguards" score. Same source.
- Leaders differ by benchmark; GPT models lead scBench and SpatialBench, Claude LSVP runs lead most therapeutics and long-horizon sets.

---

## Single-cell and spatial omics

### scBench
- **Task.** The agent gets a single-cell RNA-seq data snapshot (an `.h5ad` file) taken just before one analysis step and a plain-language question (QC, normalisation, dimensionality reduction, clustering, cell typing, differential expression). It writes a JSON answer. 195 tasks in V1 across 6 platforms. https://github.com/latchbio/scbench
- **Grading.** Automatic and deterministic: numeric tolerance, multiple choice, gene-list precision/recall, label-set Jaccard, distribution comparison. Pass = every field in tolerance. Tolerances were set by re-running the analysis with several valid methods. https://benchmarks.bio/papers/scbench.pdf
- **Where.** Repo https://github.com/latchbio/scbench, Apache 2.0. 6 public tasks; full set withheld. An older public set of **30 tasks** is packaged in UK AISI's Inspect Evals (`inspect_evals/scbench`), with data mirrored on Hugging Face (https://ukgovernmentbeis.github.io/inspect_evals/evals/scbench/, https://huggingface.co/datasets/retroam/scbench-data). Runnable today, no application. **Answers are in the public eval JSON** (grader config), so hide it from agents.
- **Compute.** 30-task data set is 17.2 GB in 18 files (Hugging Face page). The 6 current public files range from 8.8 MB to 1.6 GB (https://heurekaskills.com/latch-bench/). Inspect runs each task in Docker with 4 CPUs and 16 GB RAM; default task limit 600 s, but data download on a cold cache averages about 4 minutes, so they advise `--time-limit 1200`. Software: scanpy, anndata, standard Python stack. Fits a laptop or a modest container. Earlier leaderboard runs took about 8 to 32 minutes and $0.60 to $5 per task (https://benchmarks.bio/scbench, older table).
- **Scores.** V1 best 63.9% GPT-6 Astra (Pi); 63.6% GPT-6.1 Sol (Codex); 63.4% GPT-6 Astra (Codex); 62.9% GPT-6.1 Sol (Pi); 61.9% Opus 5.5\* (Pi). Data dated 2026-11-05. https://benchmarks.bio/benchmarks/scbench/index.md. On the 30-task Inspect subset, older models scored 43.3% (GPT-5.1), 36.7% (Gemini 3 Pro), 30.0% (Sonnet 4.5) (Inspect page). No multi-agent system found.
- **Why agents fail.** Platform matters as much as model: 40+ point drops on less-documented platforms such as MissionBio. Cell typing and differential expression need judgement (marker choice, test choice, pseudobulk vs single-cell DE). Authors say agents "cannot yet be trusted ... without stringent verification of intermediate results". Examples on the site: default QC thresholds wipe out a small targeted gene panel; skipping scaling leaves depth-driven PCs. https://benchmarks.bio/papers/scbench.pdf, https://benchmarks.bio/scbench
- **Judgement.** (a) Yes: 30 + 6 tasks, 3 attempts, fits in 12 hours. (b) Yes, best is about 64% on the full set. (c) Yes: wrong platform conventions, unchecked thresholds and skipped sanity checks are exactly what a reviewer agent can catch.

### SpatialBench
- **Task.** Same design on spatial transcriptomics (Xenium, Visium, Vizgen, Curio, AtlasXOmics): QC, normalisation, clustering, cell typing, DE, spatial analysis. 115 tasks in V1 (159 in the repo's latest release, 146 in the original paper). https://github.com/latchbio/spatialbench, https://benchmarks.bio/spatial
- **Grading.** Automatic, same grader families as scBench. https://github.com/latchbio/spatialbench
- **Where.** Repo above, Apache 2.0. 16 public tasks with graders, in the `example_evals` folder, plus published agent trajectories with each run's answer and verdict. A third party re-graded 1,040 published runs from scBench and SpatialBench with zero disagreements (https://heurekaskills.com/latch-bench/). Answers are in the public eval files.
- **Compute.** Public data: 16 files, 23.5 GB total, median 126 MB, five files over 1 GB, two at 9.6 GB (third-party audit, same source). The 9.6 GB ones are risky on a 16 GB laptop; 11 or so tasks under 1.1 GB are fine. Harness used 300 s per command and 600 s per task in the paper's setup. https://benchmarks.bio/papers/scbench.pdf (shared method)
- **Scores.** V1 best 73.3% GPT-6.1 Terra (Pi); 72.8% Opus 5.5 (Pi); 72.8% Sonnet 5.5 (Pi); 71.9% Muse Wonder 1.0 (Pi); 70.7% GPT-6.1 Terra (Codex). Dated 2026-11-08. https://benchmarks.bio/benchmarks/spatialbench/index.md. No multi-agent system found.
- **Why agents fail.** QC and cell typing are worst. Models apply single-cell QC thresholds (min genes 100 to 200) to imaging assays where 5 to 20 is right; the model that calibrated got about 25% on QC vs about 5% for others. Harness design changes results as much as model choice. Agents that inspected stored results in the data object and used them did much better. https://benchmarks.bio/papers/spatialbench.pdf
- **Judgement.** (a) Yes, minus the two 9.6 GB tasks. (b) Yes, about 73% best, much lower on QC. (c) Yes: calibration and convention errors are checkable. Also the strongest evidence that harness design moves the score.

### scBench-Long
- **Task.** Recover a published-style scientific conclusion from raw or near-raw single-cell data with no prescribed method (e.g. which T cells are tumour-reactive, using expression plus TCR data). 22 tasks in V1 (21 in the paper). https://benchmarks.bio/papers/scbench-long.pdf
- **Grading.** Deterministic pass/fail on a controlled-vocabulary final answer. Four rubric judge models score trajectories as diagnostics only. Same source.
- **Where.** Public repo with 4 tasks; **no grader published** for them (https://heurekaskills.com/latch-bench/). Repo link not confirmed by me.
- **Compute.** Not found. Cost per run up to roughly $7 in the paper's plots.
- **Scores.** V1 best 36.9% Opus 5.5\* (Pi); 32.4% Opus 5.5\* (CC); 31.8% Sonnet 5.5 (CC, best standard); 30.3% Sonnet 5.5 (Pi); 29.6% GPT-5.6 Sol (Codex). https://benchmarks.bio/benchmarks/scbench-long/index.md
- **Why agents fail.** Agents get the intermediate pieces right but the conclusion wrong: they fall back on familiar textbook biology instead of the measured data, treat the biggest number as the most important, read association as mechanism, and use one data type when two are needed. A planted label swap caught many models relabelling data back to the expected biology. https://benchmarks.bio/papers/scbench-long.pdf
- **Judgement.** (a) We can run 4 tasks but cannot score them. (b) Large headroom. (c) Failure causes are ideal for a team that challenges conclusions. Without graders, any claim would be weak.

### SpatialBench-Long
- **Task.** Long-horizon spatial studies (pancreatic cancer, glioblastoma organoids, lineage-traced lung cancer, optic nerve ageing). 21 tasks in V1 (24 in the paper). https://benchmarks.bio/papers/spatialbench-long.pdf
- **Grading.** Deterministic endpoint pass/fail plus rubric judges as diagnostics. Same source.
- **Where.** 4 public tasks, no grader published (https://heurekaskills.com/latch-bench/).
- **Compute.** Not found.
- **Scores.** V1 best 40.5% Opus 5.5\* (CC) and GPT-6.1 Terra (Pi); 39.9% GPT-6.1 Sol (Codex); 39.3% Opus 5.5\* (Pi), GPT-6 Astra (Codex), GPT-6.1 Terra (Codex). https://benchmarks.bio/benchmarks/spatialbench-long/index.md
- **Why agents fail.** Authors note paper claims don't always reproduce, so tasks were re-derived; sparse pass rates (only 5.9% of judged runs passed in one matrix). Detailed failure taxonomy not extracted.
- **Judgement.** Same as scBench-Long: headroom yes, scoring no.

## Other omics

### EpiBench
- **Task.** Epigenomics workflow snapshots (CUT&Tag/CUT&RUN, ATAC-seq, ChIP-seq, DNA methylation): QC, peak calling, annotation, differential methylation. 106 tasks. https://github.com/latchbio/epibench
- **Grading.** Deterministic: numeric intervals, label matches, all-fields checks. Same source.
- **Where.** Repo above, modified Apache 2.0. 7 public tasks with prompts and data pointers; **no grader** in the public files (https://heurekaskills.com/latch-bench/).
- **Compute.** Not found.
- **Scores.** V1 best 39.6% Opus 5.5\* (CC); 39.0% Opus 5.5 (Pi, standard); 38.7% Opus 5.5 (CC); 37.4% Opus 5.5\* (Pi). https://benchmarks.bio/results/v1/latest.csv
- **Why agents fail.** "Field-level scores are higher than endpoint scores": agents find useful intermediate results but fail where assay-specific judgement sets the final answer. ATAC-seq is hardest (22.8% in the preprint). V1's extra data anonymisation cut scores about 15 points, implying earlier runs leaned on memorised or looked-up results. https://github.com/latchbio/epibench, https://blog.latch.bio/p/continuous-benchmarks-for-biology
- **Judgement.** (a) Runnable, not scorable without writing our own reference answers. (b) Yes, under 40%. (c) Yes, judgement errors. Second-tier.

### VariantBench
- **Task.** Variant discovery, statistical genetics and personal genomics. 118 tasks. https://latch.bio/variantbench
- **Grading.** Deterministic. Graders are in the public files (https://heurekaskills.com/latch-bench/).
- **Where.** https://github.com/latchbio/variantbench, modified Apache 2.0. 8 public tasks and 28 example trajectories; 2 of the 8 (neoantigen) are not in the scored set. Answers in public files.
- **Compute.** Not found. Variant work may involve BAM/VCF files and alignment tools; check sizes before committing.
- **Scores.** V1 best 52.8% Opus 5 (CC); 51.7% Sonnet 5.5 (Pi); 51.1% Sonnet 5.5 (CC); 50.6% Opus 5 (Pi) and Opus 5.5 (Pi). https://benchmarks.bio/results/v1/latest.csv
- **Why agents fail.** Not extracted.
- **Judgement.** (a) Probably, 6 scored tasks. (b) Yes, about 53%. (c) Unknown. Third pick mainly because graders are public.

### MetagenomicsBench
- **Task.** Microbial community analysis (shotgun, amplicon, long-read): community structure, host-microbiome links, function, time series, interventions. 100 tasks. https://benchmarks.bio/papers/metagenomics.pdf
- **Grading.** Deterministic. Refusals and timeouts count as failures. Same source.
- **Where.** https://github.com/latchbio/metagenomicsbench, Apache 2.0. 4 public tasks with prompt and vocabulary files and trajectories. Grader presence not confirmed.
- **Compute.** Not found. The blog notes agents rarely managed compute (set thread counts in 9.7% of runs), which hints at heavy tasks. https://blog.latch.bio/p/metagenomicsbench-can-ai-agents-reliably
- **Scores.** V1 best 62.7% Opus 5.5\* (CC); 62.3% Opus 5.5\* (Pi); 61.7% Fable 5.1\* (Pi); 60.0% Sonnet 5.5 (Pi and CC). https://benchmarks.bio/results/v1/latest.csv
- **Why agents fail.** 47.2% of characterised failures were wrong problem interpretation, 17.4% statistical or confound reasoning, and **13.0% were runs where the agent noticed a contradiction in its own analysis but did not act on it**. Example: using 16S reads as a proxy for abundance in shotgun data. https://blog.latch.bio/p/metagenomicsbench-can-ai-agents-reliably
- **Judgement.** (a) Only 4 public tasks. (b) Yes, about 63%. (c) Strongly yes, the failure profile is made for a checker. Too few tasks to show a convincing difference.

## Therapeutics (TxBench) and oncology

Note: standard (non-LSVP) Claude runs are heavily refused on TxBench. Opus 5.5 standard (Pi): Antibody-Discovery 8.33%, Preclinical 26.33%, Oligo 1.47% (https://benchmarks.bio/results/v1/latest.csv).

### TxBench-Antibody-Discovery
- **Task.** Experimentally grounded decisions in antibody discovery (target assessment, assay design, engineering, de-risking). 100 tasks. http://github.com/latchbio/txbench-antibody-discovery
- **Grading.** Deterministic. https://benchmarks.bio/papers/txbench-ab.pdf
- **Where.** Repo above, licence "Other". 7 public example tasks with prompt/config metadata and trajectories. Whether data and graders are included: not confirmed.
- **Compute.** Not found.
- **Scores.** V1 best 67.7% Opus 5.5\* (CC); 66.7% Opus 5.5\* (Pi); 63.7% Gemini 4 Argon (Pi, best standard); 63.3% Fable 5.1\* (Pi). https://benchmarks.bio/results/v1/latest.csv
- **Why agents fail.** Paper says failures were often reproducible and more cost or tool use did not help; details not extracted.
- **Judgement.** Unclear runnability, refusal risk. No.

### TxBench-Antibody-Development
- **Task.** Decisions from lead nomination to first human dose (developability, manufacturing, PK, immunogenicity, toxicology, first dose). 60 tasks, built from patents and FDA/PMDA/EMA reviews. https://benchmarks.bio/papers/txbench-ab-dev.pdf
- **Grading.** Deterministic. Same source.
- **Where.** No public repo found.
- **Scores.** Best 39.4% Grok 4.7 (Grok Build); 37.2% Opus 5\* (CC); 36.7% Sonnet 5.5 (Pi); 36.1% Grok 4.7 (Pi). https://benchmarks.bio/benchmarks/txbench-abdev/index.md
- **Judgement.** Not runnable. No.

### TxBench-Preclinical-Pharmacology
- **Task.** Small-molecule preclinical decisions (mechanism, target engagement, target validation, safety, efficacy) from assay data. 100 tasks. https://latch.bio/txbench-pp
- **Grading.** Deterministic. https://benchmarks.bio/papers/txbench-pp.pdf
- **Where.** https://github.com/latchbio/txbench-pp, no licence stated. 7 public tasks plus trajectories. Data/grader not confirmed.
- **Scores.** V1 best 65.3% Opus 5.5\* (Pi); 65.0% Opus 5.5\* (CC); 64.7% Gemini 4 Argon (Pi); 64.3% Sonnet 5.5 (Pi). https://benchmarks.bio/results/v1/latest.csv
- **Judgement.** No licence, refusal risk. No.

### TxBench-Oligo-Discovery
- **Task.** ASO and siRNA discovery decisions, no internet. 113 tasks. https://benchmarks.bio/papers/txbench-oligo.pdf
- **Grading.** Deterministic against a decision rebuilt from the data. Same source.
- **Where.** https://github.com/latchbio/txbench-oligo-discovery, licence "Other". 7 public tasks.
- **Scores.** V1 best 62.2% Opus 5.5\* (CC); 59.9% Opus 5.5\* (Pi); 54.9% Fable 5.1\* (Pi); 52.5% GPT-6.1 Sol (Pi, best standard). https://benchmarks.bio/results/v1/latest.csv
- **Why agents fail.** Rankings vary a lot by task; no model is reliable across the pipeline. Same source.
- **Judgement.** No (refusals, small public set).

### OncologyBench
- **Task.** Cancer research and clinical decisions. **Several agents do analysis tasks in parallel and a final agent synthesises a decision.** 25 tasks, from TCGA, GENIE, DepMap and similar public data plus one donated patient record. https://latch.bio/oncologybench
- **Grading.** Tasks graded deterministically; the prose decision graded by a rubric. Strict score needs full marks on both. https://benchmarks.bio/oncology
- **Where.** No public repo found.
- **Scores.** Best 50.7% Opus 5.5\* (Pi); 44.0% Fable 5.1\* (Pi); 42.7% Sonnet 5.5\* (CC); 40.0% Opus 5 (Pi, best standard). https://benchmarks.bio/oncology
- **Judgement.** Not runnable. Worth citing: LatchBio already treats a multi-agent setup as the natural design here.

## Basic research and clinical

### NeuroscienceBench
- **Task.** Reach the right conclusion from neuroscience data (sequencing, electrophysiology, calcium imaging, EEG, BCI decoding, imaging, behaviour, biomarkers). 91 tasks from 43 papers, run offline. https://benchmarks.bio/papers/neurosciencebench.pdf
- **Grading.** Deterministic. Refusals, timeouts and harness errors are dropped from the denominator. https://benchmarks.bio/neuroscience
- **Where.** https://github.com/latchbio/neurobench, Apache 2.0, 3 public example tasks.
- **Scores.** Best 41.0% Fable 5.2\* (CC); 40.3% Fable 5.2\* (Pi); 32.2% Opus 5.5\* (Pi); best standard 27.5% Grok 4.7 (Grok Build). https://benchmarks.bio/benchmarks/neurosciencebench/index.md
- **Why agents fail.** Data type drives performance (14% on less common omics, about 25% on behaviour and EEG). Same paper.
- **Judgement.** Large headroom but only 3 public tasks. No.

### RareDiseaseBench
- **Task.** Diagnose and plan care for rare-disease patients. Agents must request the data they need (BAMs, images, reports) from an isolated records system through a tool. 35 tasks, one or more visits each. https://benchmarks.bio/papers/rarediseasebench.pdf
- **Grading.** Rubric; a pass means every rubric item passes. 6-hour run limit. Same source.
- **Where.** https://github.com/latchbio/rarediseasebench, Apache 2.0. 2 sample tasks that were removed from the benchmark.
- **Scores.** Best 43.8% Gemini 4 Argon (Pi); 33.3% Opus 5.5\* (CC); 33.3% GPT-6 Astra (Pi); 28.6% Opus 5.5\* (Pi). https://benchmarks.bio/benchmarks/rarediseasebench/index.md
- **Why agents fail.** Failing to get or work up the needed data is the top cause (36% of failed runs), then search design (29%), interpretation (17%), timeouts (13%). Same paper.
- **Judgement.** Not runnable (2 non-benchmark tasks, 6-hour runs). Failure causes are planning failures a team could help with.

### TissueMicroscopyBench
- **Task.** Analyse real tissue microscopy images: find cells and structures, quantify staining and shape, track change over time. 49 tasks. https://benchmarks.bio/tissuemicroscopy
- **Grading.** Verifiable answer per task; refusals and errors count as failures. Same source.
- **Where.** No paper or repo yet ("Publication links are not yet available").
- **Scores.** Best 34.0% Fable 5.2\* (Pi); 32.6% Fable 5.2\* (CC); 29.3% Opus 5.5\* (Pi); best standard 25.9% GPT-6.1 Terra (Pi). https://benchmarks.bio/benchmarks/tissuemicroscopybench/index.md
- **Judgement.** Not runnable. No.

## Biosecurity (BioSecBench)

All of these are restricted. Full sets, data and graders are "shared only with qualified partners" because some tasks are dual-use (https://github.com/latchbio/biosecbench-surveillance). Standard Claude is often refused here. I recommend we stay away from this family entirely.

- **BioSecBench-Surveillance.** Infer the right pipeline from raw pathogen sequencing data (taxonomy, variants, resistance genes, source tracking, engineering detection). 102 tasks in V1. Deterministic grading. Public repo has 7 prompts only, no data or graders. Best 60.8% Opus 5.5\* (Pi); 59.8% Gemini 4 Argon (Pi); 57.8% Opus 5.5\* (CC). Failures come from choices around the right tools: references, thresholds, filters, normalisation. https://benchmarks.bio/papers/biosecbench-surveillance.pdf, https://benchmarks.bio/results/v1/latest.csv
- **BioSecBench-Function.** Recover biological function from assay, structure or sequence data. 111 tasks, deterministic. Public repo: 7 prompts, no data or graders. Best 73.6% Opus 5.5 standard (Pi); 61.5% Opus 5.5 (CC); 56.1% Opus 5.5\* (Pi). https://benchmarks.bio/papers/functionbench.pdf, https://github.com/latchbio/biosecbench-function, https://benchmarks.bio/results/v1/latest.csv
- **BioSecBench-Structure.** Reason from crystal and cryo-EM structures. 58 tasks, graded against expert reference answers. No repo found. Best 52.3% Fable 5.2\* (Pi); 47.1% Fable 5.2\* (CC); 43.7% Fable 5.2 (CC). Notable failure: in 272 of 354 non-refusal failures by Fable 5.2 and Opus 5.5, **the agent found the key evidence but did not act on it**. Fable 5.2 (CC) averaged $11.93 and 38.8 minutes per run. https://benchmarks.bio/papers/structurebench.pdf
- **BioSecBench-RespiratoryVirus-Surveillance.** 110 tasks (diagnostics, pathogen discovery, evolution, burden and forecasting). Best 36.4% Opus 5.5\* (Pi); 31.2% Opus 5.5\* (CC); 25.4% Opus 5.5 (Pi). No repo found. https://benchmarks.bio/papers/respvirus-surveillance.pdf, https://benchmarks.bio/benchmarks/biosecbench-respvirus-surveillance/index.md
- **BioSecBench-RespiratoryVirus-Countermeasures.** 64 tasks (structure, immunity, host response, antivirals, outcomes). Best 33.3% Fable 5.2\* (Pi); 32.3% Fable 5.2\* (CC); 30.2% Gemini 4 Carbon (Pi). No repo found. https://benchmarks.bio/papers/respcounter.pdf
- **BioSecBench-Refusal.** Safeguards score: refuse hidden red-team requests and comply with routine ones (61 routine, 46 red-team). Judged by MiniMax M3. Best 62.4% Grok 4.7 (Pi); 55.3% Muse Wonder 1.0; 54.8% Gemini 3.7 Flash. Claude Opus 5.5 scores 3.8% (Pi) and 0.0% (CC) because the API classifier refused 1,281 of 1,290 runs before any token. https://benchmarks.bio/benchmarks/biosecbench-refusal/index.md, https://benchmarks.bio/results/v1/index.md

---

## Recommendation

1. **SpatialBench public set** (about 14 usable tasks after dropping the two 9.6 GB files). Graders are public, data downloads without an account, and LatchBio published single-model trajectories with verdicts on these exact tasks. Failures are calibration and convention errors a reviewer agent can catch.
2. **scBench**: the 30-task Inspect Evals set plus the 6 current public tasks. Easiest to run today (Docker, 4 CPU, 16 GB, built-in Claude Code and Codex harness options), and Inspect already ships a single-model baseline path.
3. **VariantBench** (6 scored public tasks, graders public) as a third domain, if data sizes are reasonable.

How to make the claim fair: run a single strong model in Claude Code or Pi and our team on the same public tasks, offline, 3 attempts each, with the eval JSON hidden from all agents and graded by a separate script. Report it as "on the public subset", not against the full-set leaderboard.

Open questions not resolved: data sizes for VariantBench, MetagenomicsBench and TxBench public tasks; whether TxBench and MetagenomicsBench public files include graders; whether LatchBio would grade an outside agent on the private sets (no submission process found).
