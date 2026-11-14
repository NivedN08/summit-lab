# Wider sweep: comp-bio benchmarks we had not looked at

Scout: scout-sweep. Date: Saturday 14 November 2026. Research used 35 calls to `lab/tools/exa.py` (14 searches run in batches, plus page reads). No repositories were cloned, no data downloaded, no benchmark code run, and no answer files opened.

The question: which other public benchmarks have checkable answers, small tasks, and enough headroom that a checked team of agents could beat one frontier model?

The short answer: four are worth a look. **SciAgentArena** is the most ready: open tasks with automatic scorers, and low published scores on single-cell and spatial work. **BioMysteryBench** (Anthropic) has a "human-hard" split where published model scores are 5% to 30%. **GeneTasks** is a generator of GeneBench-Pro-style statistical-genetics tasks with a deterministic grader, but it lives on a paid platform and has no published scores. **Power Agent Biostatistics** is tiny and fully numeric, but a specialised agent already scores 99%. Several new benchmarks turned out to be saturated already: CompBioBench is at 91-94% and the Galaxy IWC workflows at 94-99%.

## Summary table

| Candidate | Field | Tasks | Checking | Newest published score | Data size | Verdict |
|---|---|---|---|---|---|---|
| SciAgentArena (Yale, Broad, Microsoft, Stanford, Harvard) | Single-cell, spatial omics, EHR, genetics (also drug discovery, which we exclude) | About 200 in the paper; a Harbor port has 304, of which 229 are not drug discovery | Per-task scorer programs (pytest wrapping the benchmark's own evaluators); exact match for cross-domain, EHR and validity tasks | Best agent per task type: DEG detection F1 29.4, spatially variable genes Jaccard 47.6, EHR direct diagnosis 70% accuracy, polygenic risk score 80% (June 2026; models in paper) | Total 22 GB; single-cell datasets 194 MB to 8.3 GB each; EHR 2.5 GB; cross-domain 67 KB | **Start now** on the spatial, EHR and small single-cell tasks. The Harbor port's Hugging Face page asks you to accept its conditions first |
| BioMysteryBench (Anthropic) | Bioinformatics on real anonymised data (sequencing, scRNA-seq, proteomics, metabolomics, structures) | 90 (v11): 73 human-solvable, 17 human-hard | Final answer against an expert-written answer key. Anthropic's own grader is not published; third-party harnesses exist | Human-hard (v8, 23 problems): Claude Haiku 4.5 5.2%, Sonnet 4.6 19.1%, Opus 4.6 23.5%, Opus 4.7 27.0%, Mythos Preview 29.6% (April 2026). Human-solvable: Opus 4.6 77.4% reported | 5-problem sample is 11.4 MB; full set not found | **Possible with work**: get dataset access, confirm the answer format is machine-checkable, use the human-hard split |
| GeneTasks (General Reasoning, on OpenReward) | Statistical, clinical, population and quantitative genetics; pharmacogenomic study design | 173 (49 base, 124 hard) | Deterministic: numeric within per-task tolerance, integer exact, label exact. No LLM judge | Not found | Small simulated tables; sandbox is 2 CPU and 2 GB RAM | **Possible with work**: needs an OpenReward account with per-second billing, or the generator code (repository not found) |
| Power Agent Biostatistics Benchmark (Yale) | Biostatistics: power and sample-size calculation | 106 in 4 tiers | Numeric comparison with per-task tolerance; R reference code for every answer | Power Agent (Claude Opus 4.5) 99.1%; Gemini 3.1 Pro with code 69.8%; GPT-5.2 Pro (no code) 53.8% (February 2026) | No data files; prompts only | **Possible with work**: run our own single-agent baseline with R first; drop it if that is above 90%. Tier 4 (21 tasks) is the hard part |
| BAISBench (cell-type annotation part) | Single-cell | 15 annotation datasets, plus 193 multiple-choice questions | Hierarchical cell-type score computed by code (uHAF tree); multiple choice for the second task | Multiple choice: 75.9 Claude Code with Opus 4.7 (May 2026). Annotation: experts about 16% ahead of AI (2025) | Not found | **Possible with work**: annotation part only; check current scores and dataset sizes |
| frontier-bench science tasks (Scale AI, Harbor) | Genomics (variant effect), proteomics (GSEA), glycomics | About 3 biology tasks | pytest verifiers; golden answers stored as SHA-256 hashes | Not found | Not found; tasks allow 4 CPUs and 4 GB | **Possible with work**: too few tasks for a benchmark, but useful as hard showcase tasks (expert time 4-7 hours each) |
| ProteinGeoBench | Protein structure analysis (lookup and geometry calculation) | 131 (28 numeric) | Not found in detail; tasks have numeric and lookup answers | Frontier models 0.707-0.816 overall; Claude Opus 5 0.716 on numeric tasks (October 2026) | PDB-sized files | **Possible with work**: repository not found; only the numeric subset has headroom |

## Candidates that qualify

### SciAgentArena

- **What the agent does**: runs a multi-step analysis and writes one output. For single-cell and spatial tasks this is an AnnData `.h5ad` file after a pipeline (QC, integration, clustering, marker or DEG genes, spatially variable genes, trajectory). For EHR and cross-domain tasks it is a text answer.
- **Tasks**: about 200 in the paper, across 5 domains and 4 categories (data analysis, optimisation, discovery, validity). The Harbor port has 304: drug discovery 75 (exclude), EHR 70, single-cell 50, cross-domain 40 (synthetic lethality and target identification; target identification is close to drug discovery), spatial 33, task validity 30 (yes/no), perturbation 6. The genetics tasks (Mendelian randomisation, polygenic risk scores) are listed on the site but "not emitted" in the port.
- **Checking**: each task's `tests/test_outputs.py` calls the benchmark's own evaluator, for example `pipeline_evaluator.evaluate_single_cell`, `evaluate_spatial`, or the EHR `stepwise_score`. The pass marks for the pipeline tasks were set by whoever made the port, not by the original authors (for example clustering ARI/NMI 0.50, spatial SVG F1 0.15). The marker-overlap score is capped by design: 10 predicted genes against a list of 3 to 50 reference genes.
- **Where**: paper https://arxiv.org/abs/2606.12736; site https://sciagentarena.github.io/; code https://github.com/HelloWorldLTY/SciAgentArena; task data https://huggingface.co/datasets/iLOVE2D/SciAgentArena; Harbor port https://huggingface.co/datasets/iLOVE2D/sciagentarena_tb (you must accept its conditions on Hugging Face to see the files). Licence not found. EHR uses Synthea and the open MIMIC-IV Demo v2.2.
- **Size and compute**: 22 GB in total. Single-cell is 16 GB, spatial 3.8 GB, EHR 2.5 GB. Datasets run from 194 MB to 8.3 GB, so some single-cell tasks break our 2 GB limit. The port supports skipping the big files and still running 225 tasks. Per-task run time not found.
- **Answers relative to inputs**: in the port, answer keys and scorers sit under `tests/`, which only reaches the container after the agent exits. Nothing under `tests/` is in the image or any mounted folder.
- **Scores**: best agent per task type (paper, June 2026; 18 agents): DEG detection F1 29.4, spatially variable genes Jaccard 47.6, EHR direct diagnosis 70% accuracy, polygenic risk score 80%. The models behind each number were not extracted.
- **Verdict**: **start now** on spatial (33), EHR (70) and the single-cell tasks whose data is under 2 GB. Someone must accept the Hugging Face conditions (ask the user first). Leave out drug discovery and probably target identification.

### BioMysteryBench (Anthropic)

- **What the agent does**: gets anonymised real data (WGS, scRNA-seq, methylation, ChIP-seq, Hi-C, metagenomics, proteomics, metabolomics, crystal structures) and one question with an objective answer, for example which organism a structure comes from. It can install tools and query NCBI, Ensembl and UniProt. Looking up the source dataset is forbidden.
- **Tasks**: v11 (July 2026) has 90: 73 human-solvable and 17 human-hard (no expert on a panel of up to five solved them). v8 (April 2026) had 99 (76 and 23).
- **Checking**: final answer against an expert-written answer key, averaged over five runs. Anthropic did not publish its grader. Third-party harnesses score final answers: https://github.com/safer-ai/biomysterybench-inspect, https://github.com/iandonaldson/bio-mystery-bench, https://github.com/Lore-Hex/prometheus-biomysterybench. Whether the answers can be checked by exact match or need a model judge: not confirmed.
- **Where**: https://huggingface.co/datasets/Anthropic/BioMysteryBench-full (the page has an "Access" section, so it may be gated); write-up https://www.anthropic.com/research/Evaluating-Claude-For-Bioinformatics-With-BioMysteryBench; audit https://spectrai-initiative.github.io/bio-benchmark-atlas/benchmarks/biomysterybench/. Licence not found.
- **Size and compute**: a 5-problem public sample has an 11.4 MB data zip. Full-set size and run time per task not found. Some problems start from raw sequencing reads, so some may be large.
- **Answers relative to inputs**: `problems.csv` / `problems.parquet` sit next to the data; whether they hold the answers was not checked (we did not open them).
- **Scores**: human-hard v8, mean of 5 runs: Haiku 4.5 5.2%, Sonnet 4.6 19.1%, Opus 4.6 23.5%, Opus 4.7 27.0%, Mythos Preview 29.6% (Anthropic, April 2026). Human-solvable: Opus 4.6 77.4% (reported); one outside harness got 64.9%. The Claude Opus 5 system card (July 2026) reports a human-hard score; the number was not extracted.
- **Verdict**: **possible with work**. Get access, confirm the grading can be done by code, then run the 17 human-hard problems. Small task count but real headroom, and a direct comparison with Anthropic's published numbers. Harness choices clearly move the score (64.9% versus 77.4%), so we must run our own single-agent baseline.

### GeneTasks (General Reasoning, OpenReward)

- **What the agent does**: gets messy simulated genetic data and a target quantity, and must find hidden traps: QC failures, allele orientation, ancestry stratification, winner's curse, immortal-time bias, assortative mating, and so on. Families: carrier risk, cis-Mendelian randomisation, GWAS, Wright-Fisher selection, heritability (twins), time-to-event pharmacogenomics, LD-score regression, admixture.
- **Tasks**: 173 in one split: 49 base (3-4 decision points), 124 hard (5-6). A generator can make more, so we could make fresh tasks no model has seen.
- **Checking**: deterministic, no LLM judge. Numbers within per-task tolerance, integers exact, labels exact. The weighted reward gives partial credit; an all-fields-pass flag matches GeneBench-Pro's scoring. The agent only sees pass or fail per field, never the truth.
- **Where**: https://openreward.ai/GeneralReasoning/genetasks. MIT licence. Data is hosted on the OpenReward platform, not in git. A `datagen/` generator is mentioned, but its repository was not found.
- **Size and compute**: 2 CPUs, 2 GB RAM, standard Python stack. Cost about $0.01 per 5-minute session.
- **Answers relative to inputs**: `eval_config.json` (truth and grader) stays on the host and never enters the sandbox.
- **Scores**: not found. It is built as a training environment.
- **Verdict**: **possible with work**. It needs an OpenReward account with billing, and creating one needs the user's approval. It also needs our own single-agent baseline. Design is very close to GeneBench-Pro, which is our current top pick, so it could serve as a bigger, fresh test set for the same skill.

### Power Agent Biostatistics Benchmark

- **What the agent does**: power and sample-size calculations for t-tests, regression, mixed models, survival, count data, cluster and stepped-wedge trials, and Riley prediction-model sizes.
- **Tasks**: 106 (tier 1: 30, tier 2: 35, tier 3: 20, tier 4: 21).
- **Checking**: direct numeric comparison with per-task tolerances. An LLM is used only to pull the number out of free text, and that step is optional.
- **Where**: https://github.com/ykzeng-yale/power-agent-benchmark. MIT licence.
- **Size and compute**: no data files. Needs R with `pwr`, `simr`, `pmsampsize` and similar packages. Tier 3 includes simulation-based power, which may take minutes.
- **Answers relative to inputs**: in the same repository (ground truths with R reference code). Not opened.
- **Scores**: Power Agent (Claude Opus 4.5) 99.1%; Gemini 3.1 Pro with code 69.8%; ChatGPT Thinking 57.5%; GPT-5.2 Pro without code 53.8%; Claude Opus 4.6 without code 49.1% (February 2026). Tier 4 is the hard part: best general model 38.1%.
- **Verdict**: **possible with work**. A specialised single agent with R already reaches 99%, so a plain Claude Code agent with R may also come close. Run that baseline first, and keep this only if it scores below about 80%. Also, the answers are public in the same repository.

### BAISBench

- **What the agent does**: (1) process 15 expert-labelled scRNA-seq datasets and annotate cell types; (2) answer 193 multiple-choice questions taken from 41 single-cell studies by analysing their data.
- **Checking**: annotation is scored by code with a hierarchical tree score (full credit for an exact match, 0.5 for a parent, 0.2 for a grandparent). The second task is multiple choice.
- **Where**: https://github.com/EperLuo/BaisBench and https://huggingface.co/datasets/EperLuo/BaisBench. MIT licence.
- **Size and compute**: not found.
- **Scores**: multiple choice: 75.9 Claude Code with Opus 4.7 and 75.8 BioResearcher with GPT-5.4 (May 2026, https://www.sota2.com/research/sota/scientific-discovery-on-baisbench-scientific-discovery-bais-sd). Annotation: the paper says experts lead AI by about 16% (2025 models). No current annotation score found.
- **Verdict**: **possible with work**, annotation part only (15 tasks). Check dataset sizes and run a current baseline. The multiple-choice part is near the top of our useful range and is multiple choice.

### frontier-bench science tasks (Scale AI)

- **What the agent does**: long expert tasks, for example running Ensembl VEP on CRISPR-edited ATRX transcripts and writing a mutation report (`atrx-vep-crispr`), TMT-10plex differential expression plus GSEA (`gsea-proteomics`), or identifying a glycan from an MS2 spectrum.
- **Tasks**: about 3 in biology among the science tasks.
- **Checking**: pytest; golden answers stored as SHA-256 hashes.
- **Where**: https://github.com/harbor-framework/frontier-bench (tasks under `tasks/`). Licence: a per-task `LICENSE.md`.
- **Size and compute**: 2-4 CPUs, 4 GB RAM, agent timeout 8 hours; expert time 4-7 hours. Data size not found.
- **Scores**: not found.
- **Verdict**: **possible with work**. Too few tasks for a scored comparison, but good hard demonstrations. The 8-hour timeout is far over our half-hour target.

### ProteinGeoBench

- **What the agent does**: answers questions about protein structure files: metadata lookup, sequence checks, numeric geometry (distances, torsion angles), concepts.
- **Tasks**: 131; 28 numeric; 30 use structures not in any public database.
- **Scores**: four frontier models 0.707 to 0.816; Claude Opus 5 0.907 on lookup but 0.716 on numeric tasks; 12 tasks failed by all four (https://arxiv.org/abs/2610.26829, October 2026).
- **Checking, repository, licence, size**: not found (paper is CC BY 4.0).
- **Verdict**: **possible with work**, only if the tasks are public. The numeric subset is the interesting part.

## Considered and rejected

- **CompBioBench (Genentech)**: saturated. GPT-5.6 Sol 91-92%, Gemini 3.8 Flash 94% (https://goeckslab.github.io/agent-benchmark-results/, https://www.k-dense.ai/blog/compbiobench-three-runs).
- **Galaxy IWC Workflow Benchmark (10 tasks)**: saturated, 88-99%.
- **Bioinformatics-Workflow-Bench (142 tasks: Galaxy, nf-core, WDL)**: heavy workflow execution (assembly and similar); the related IWC results are 94-99%; the rubrics are auto-generated.
- **Extended-BioAgentBench (71 tasks)**: checker not found (its parent, BioAgent Bench, uses an LLM grader); raw FASTQ data.
- **MetagenomicsBench, BioSecBench-Structure**: LatchBio benchmarks.bio family (already covered); BioSecBench is also biosecurity.
- **RRBench (UCL, epidemiology data cleaning)**: ground truth needs a UK Data Archive dataset (UKDA-5545, safeguarded, registration required).
- **EpiBench-1.0 (Deeplearn-PeD, 21 tasks)**: needs live downloads from 10+ health data systems; scoring mixes human expert review with automatic parts.
- **LifeSciBench (OpenAI, 750 tasks)**: graded against expert rubrics (judge), single-turn. Not saturated (GPT-Rosalind 36.1% pass), but fails the checkable-answer rule.
- **SciReplicBench**: LLM judge against rubric trees.
- **Biomni-Eval1 (433)**: mostly knowledge and database questions, part of it is LAB-Bench (covered); S1-NexusAgent already at 76%.
- **Rosalind-based studies (104 questions)**: algorithm exercises that coding agents likely saturate; the checker is the Rosalind site, which needs an account.
- **SC-Arena, MetaBench, llm-epidemia, ecology knowledge tests**: knowledge or natural-language tasks, not data analysis, or judge-graded.
- **EpiCurveBench**: reading numbers off chart images, not data analysis.
- **GeoNatureAgent Benchmark, TerraBench**: geospatial and earth-system tool calling, not biology data analysis.
- **MetaboSquad benchmark (100 queries), MetaboT, MSAgent**: metabolomics assistants; checker and headroom unclear.
- **ProteoBench (ohmcpatel)**: only smoke-test tasks so far; the real tasks are still being built.
- **NeCast benchmark (population-history inference)**: a methods comparison with true simulated histories, not agent tasks. Could be turned into tasks, but that is our own build.
- **Virtual Embryo Challenge, scPerturBench**: model-training challenges.
- **PMxBench (population pharmacokinetics), DrugDiscoveryBench (Scale)**: pharmacology; DrugDiscoveryBench is also judge-graded.
- **ABLE, BioDesignBench, PROTEAN**: protein design with generative models (GPU); ABLE is also dual-use.
- **BixBench3, BixBench Verified-50**: BixBench family (excluded). Note: the Galaxy lab reports Verified-50 at 87-90%, so it is saturated too.
- **BioFlowBench, SBMLLM-Bench (75 systems-biology models), spmind SP-Bench, verify-or-trust**: not checked in detail; seen too late or too narrow.

## What surprised me

1. Two new 2026 benchmarks are already saturated: CompBioBench at 91-94% and the Galaxy IWC workflows at 94-99%.
2. BioMysteryBench is Anthropic's own benchmark, and it has a split that no human expert on the panel solved. Its published scores (5-30%) are exactly the kind of gap a checking team might close. But the harness changes the score a lot: one outside harness got 64.9% where Anthropic reports 77.4%.
3. GeneTasks reads like an open sibling of GeneBench-Pro, our top pick: it has a generator, so we could make fresh tasks that no model has seen.
4. A useful critique paper turned up: "Are We Measuring Scientific Intelligence?" (https://borowiecki.dev/pdf/2610.04915). It argues that answer-key benchmarks do not check whether the answer was actually derived from the supplied data. That is something our director role could check.
5. A maintained catalogue of bio benchmarks exists: BioBench Atlas (https://spectrai-initiative.github.io/bio-benchmark-atlas/), covering 35 families. It is worth using for future scouting.
