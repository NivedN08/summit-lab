# Verified shortlist: five computational biology benchmarks

Scout: scout-verify. Date: Saturday 14 November 2026. 28 calls to `lab/tools/exa.py` (logged). No repositories cloned, no datasets downloaded, no benchmark code run. I did not open any answer file. Where a page showed answers in passing (the BioDSA-1K dataset viewer), I left them out.

"Tasks we could run" assumes about three hours of wall time, about ten agents at once, and a lab run that uses four agents per task (two blind teams, a director, a referee) plus a single-agent run on the same tasks. The time per task is my estimate unless a source is given.

## Summary table

| Benchmark | Tasks we could run in ~3 h (per arm) | Grading | Newest published score, model, date | Data size | Verdict |
|---|---|---|---|---|---|
| BioDSA-1K | 30-40 hypotheses from ~20 studies | Exact match on True / False / Not verifiable (our own 10-line scorer). The paper's evidence-alignment metric uses a judge model: drop it | Type I / II error 0.06-0.19 by category; GPT-4o and o3-mini agents, May 2025. Nothing newer found | Task file under 1 MB; data are cBioPortal study tables, size per study not found | **Start now** |
| GenoTEX | 5-8 traits (unconditional problems) | `eval.py`: F1 of predicted significant genes vs expert lists, no judge | 60.48% F1, GenoMAS six-agent team (Claude Sonnet 4, o3, Gemini 2.5 Pro; 2025 models); humans 71.63% | 354 MB and 472 MB for the two traits I measured; 41.5 GB input in total | **Possible with work** (2-3 h setup) |
| LABBench2 CloningQA | All 14, twice each | Deterministic: simulates PCR, assembly and digestion, passes if product is at least 95% similar to a hidden reference | 43% (6/14): GPT-5.6 Luna (audit, Oct 2026), Claude Opus 4.6 with tools (Mar 2026), Gemini 3 Pro with tools (Jan 2026) | 1.14 MB task file; sequence files fetched on demand | **Start now, as a side bet** (too few tasks to prove a difference) |
| VariantBench public | 6 scored tasks, once each | Deterministic per-field tolerance (`latch-eval-tools`) | 52.8% on the full 118: Claude Opus 5, Claude Code (data 2026-11-05) | Not found; reference sandbox was 6 CPU, 34 GiB RAM, 6 h per task | **Possible with work** (check data sizes first) |
| ScienceAgentBench, bio + chem | 3 non-drug comp-bio tasks (about 19 if drug-property tasks were allowed) | Per-task evaluation programs; 25 of the 47 bio/chem tasks are figures judged by GPT-4o | 33.33% overall, o3 (HAL, 2025 models; HAL still paused). 43.1% Mimosa multi-agent, DeepSeek-V3.2, Mar 2026 | Datasets ship inside the same zip as the gold programs | **Skip** |

## 1. BioDSA-1K

**Where.** Tasks: Hugging Face `zifeng-ai/BioDSA-1K`, one parquet file (https://huggingface.co/datasets/zifeng-ai/BioDSA-1K). The repo copy at `benchmarks/BioDSA-1K/dataset/biodsa_1k_hypothesis.parquet` says the Hugging Face version is the current one (https://github.com/Keiji-AI/BioDSA/blob/main/benchmarks/README.md). Data: each row names a cBioPortal study (`cBioPortal_ID`); the paper says to download the raw tables from cBioPortal and run code locally. cBioPortal data are under the Open Database License (paper, section 2.1: https://arxiv.org/html/2505.16100). Harness: BioDSA framework, MIT (https://github.com/Keiji-AI/BioDSA). Dataset licence on the card: not found. No login or form seen.

**Task format.** Each row: hypothesis text, PMID, paper title, publication class (8 classes), cBioPortal study ID and URL, supporting evidence, label, sample ID. The agent gets a hypothesis and the study's tables, writes and runs code, and returns one of True, False, Not verifiable. 1,029 hypotheses from 329 papers. We define the output file (for example `answer.json` with `{"decision": ..., "evidence": [...]}`).

**Grading.** Decision accuracy (Type I and Type II error, and Not-verifiable recall) is plain label matching. We write the scorer ourselves. The paper's evidence-alignment score uses an LLM judge, and code executability is a side metric; leave both out. Grader code location in the repo: not found.

**Data size and compute.** Task file is small. Study table size: not found (studies range from small gene panels to full expression matrices, per the paper's Figure 1). CPU only; pandas, lifelines and scipy are enough. My estimate: 5-15 minutes per hypothesis for one agent.

**Where the answers are.** In the same parquet row as the input: `hypothesis_label` is the answer, and `supporting_evidences` holds the published result values. Strip both columns before any agent sees a task. Also: most hypotheses come as a true/false pair from one paper (sample IDs end `_true` and `_false`, the false one a reversed statement). Never show both halves of a pair to the same agent, and run each in a fresh session.

**Published scores.** Only 2025 models: CodeGen and ReAct agents on GPT-4o and o3-mini (May 2025). Type I errors 0.06-0.10, Type II up to 0.191; best Not-verifiable recall 92% (ReAct with reasoning); evidence alignment 0.20-0.25 (paper). Newer scores: not found.

**Single agent vs lab.** A 2026 frontier agent will probably get most True/False decisions right; GPT-4o agents already erred only 10-19% of the time. The likely errors are two: calling a claim Not verifiable when the variable exists under another name, and the reverse, inventing a test on a proxy variable when the data truly lack it. A second blind team plus a director who asks "which column did you use, and does it measure the claim?" targets exactly those. A risk: labels come from published claims, so an honest reanalysis can disagree with the label. Disagreement between the two teams is a useful flag for that. Headroom is unknown until we run our own single-agent baseline.

**Verdict: start now.** Work needed: strip two columns, pick about 20 studies (one hypothesis per pair, plus some Not-verifiable ones), download those studies from cBioPortal, write the scorer. Run 30-40 hypotheses per arm in three hours.

## 2. GenoTEX

**Where.** Code and docs: https://github.com/Liu-Hy/GenoTEX. Full bundle: Hugging Face `Liu-Hy/GenoTEX` (also Kaggle, Google Drive, Baidu). Licence CC BY 4.0 (README). No login seen. The Hugging Face copy is split into one folder per trait (`input/GEO/<Trait>/<GSE>/`, `input/TCGA/<cancer>/`), so one trait can be fetched alone with a file-pattern filter (https://huggingface.co/api/datasets/Liu-Hy/GenoTEX/tree/main).

**Task format.** For a trait (and optionally a condition such as age or gender), the agent gets the raw GEO series files (SOFT and series matrix) and, for cancers, a TCGA cohort. It must preprocess each cohort into gene, clinical and linked CSVs, then write `output/regress/<Trait>/significant_genes_condition_<None|Condition>.json` with a ranked gene list (README). 1,384 problems: 132 unconditional, 1,252 conditional. 36 TCGA cohorts.

**Grading.** `eval.py -p <pred> -r <ref> -t analysis` (also `selection`, `preprocessing`). No judge model, no container, no cloud. Needs the repo's `requirements.txt` (pandas, scikit-learn and similar).

**Data size and compute.** Two traits I measured: Breast_Cancer 30 files, 354 MB; Asthma 30 files, 472 MB. So one trait fits our 2 GB limit. The reference regression (Lasso over about 18,500 genes and about 167 samples per cohort) is light on CPU. Time per problem: not found; my estimate is 20-40 minutes per agent per trait.

**Where the answers are.** Same download, different folders. `output/` holds the expert preprocessed tables and the significant-gene lists. `code/<Trait>/*.ipynb` holds the expert notebooks with their outputs. Both are answers. Give agents only `input/<their trait>`, `metadata/gene_synonym.json` and, if we follow the paper, `metadata/task_info.json`.

**Published scores.** GenoMAS 60.48% F1 (team of six on Claude Sonnet 4, o3, Gemini 2.5 Pro; all 2025 models); same team all-Claude-Sonnet-4 52.98%; GenoAgent 43.63%; Biomni 14.82%; human experts 71.63%. The ACL Anthology lists the GenoMAS paper in Findings of EMNLP 2026 (https://aclanthology.org/2026.findings-emnlp.1395.pdf); the lab blog dated 16 October 2026 says ACL Findings 2026 (https://dream.ischool.illinois.edu/blogs/ai_agents_gene_expression_analysis.html). No late-2026 single-model score found.

**Single agent vs lab.** The published weak spot is clinical metadata: experts and agents agree 91% on gene tables but only 33% on clinical trait tables. A single agent tends to misread how a cohort encodes disease status or skip probe-to-gene mapping. Removing GenoMAS's code reviewer cut F1 from 60.48% to 24.98%. A director who checks "how did you code cases vs controls in each cohort?" should change outcomes here more than on any other benchmark in this list.

**Verdict: possible with work.** Work: download 5-8 traits' inputs only, keep `output/` and `code/` in a folder agents cannot read, use unconditional problems first, run `eval.py -t analysis` ourselves. Setup is about 2-3 hours. Then 5-8 traits per arm.

## 3. LABBench2 CloningQA

**Where.** Tasks: Hugging Face `EdisonScientific/labbench2` (1.14 MB, CC BY-SA 4.0). Harness: https://github.com/EdisonScientific/labbench2 (CC BY-SA 4.0); README asks for `HF_TOKEN`; sequence files come from Google Cloud Storage on demand. Whether the dataset is gated: not found. An Inspect port pins a fork at `Generality-Labs/lab-bench@081864a` (https://ukgovernmentbeis.github.io/inspect_evals/evals/lab_bench_2/index.html).

**Task format.** 14 end-to-end cloning designs (restriction-ligation, Gibson, Golden Gate). The agent returns the whole workflow as one expression in a small custom language. Sequences are injected in the prompt or given as files (3,000 bp or longer). Paper: https://arxiv.org/html/2604.09554.

**Grading.** Deterministic: the pipeline executes the expression, simulating PCR, assembly and digestion, and passes only if the product is at least 95% similar to a hidden reference, plus a restriction digest check on some tasks (https://generality.org/blog/posts/labbench2-full-audit/). Needs Go 1.21+ and `uv`. No judge for this subset.

**Other LABBench2 subsets.** Only SeqQA2 (400) and CloningQA are code-and-data with deterministic scoring. SeqQA2 is "functionally saturated" after the audit fixed flawed tasks (86-90%). The other nine families are literature, figure or protocol questions, most judged by a model and rated saturated or confounded in the audit. Skip them.

**Data size and compute.** Tiny. Claude Opus 4.6 with tools took 14-22 minutes per task (https://advances.edisonscientific.com/benchmarks/labbench2/cloning/).

**Where the answers are.** The dataset holds an ideal answer per task (its changelog corrects one). Treat the parquet as an answer file: give agents only the prompt and the sequence files.

**Published scores.** Best 43% (6/14): GPT-5.6 Luna (Generality Labs audit, 7 October 2026), Claude Opus 4.6 with tools (22 March 2026), Gemini 3 Pro with tools (27 January 2026). GPT-5.6 Terra 29%; Claude Opus 4.5 14%. Caution: GPT-5.2 refused about 40% of the 14 on biosecurity grounds (Inspect report, June 2026). Standard Claude may also refuse some.

**Single agent vs lab.** Failures are exact string handling: wrong overhangs, primers that do not anneal, a product off by a few bases. A lab whose referee re-simulates the construct with code (Biopython) before submitting could catch many. But with 14 tasks, the 95% interval on a mid-range score is about 45 points wide (audit), so even a real gain will look like noise.

**Verdict: start now, as a side bet.** The harness runs today. Run all 14 per arm, twice. Report it as a case study, not as proof.

## 4. VariantBench (public subset)

**Where.** https://github.com/latchbio/variantbench. Licence: README says Apache 2.0, GitHub shows "Other". `evals/manifest.json` lists 8 tasks; 6 are in the scored benchmark: `cis_mr_bcaa`, `fine_mapping_effector_gene_prioritization`, `chry_father_son_concordance_11`, `ont_rfc1_repeat_expansion_pathogenicity`, `small_variant_calling_mnv_codon_consequence`, `sv_chromosome_end_signal`. The two neoantigen tasks are not scored. `trajectories/` has 28 example runs (Claude Opus 4.8, GPT-5.6 Sol, Gemini 3.5 Flash, Grok 4.5).

**Task format.** One `eval.json` per task with `task` (prompt with an answer template), `data_node` (a `latch://` path to the data), and `grader`. The agent writes `eval_answer.json` (https://github.com/latchbio/latch-eval-tools). Data are BAM, VCF, long-read and GWAS summary files, judging by task names and the paper.

**Grading.** `pip install latch-eval-tools`; graders such as `numeric_tolerance`, `jaccard_label_set`, `multiple_choice`. A task passes only if every field passes. No judge model. Whether the data download needs a Latch account: not verified by me (an earlier third-party check said most handles resolve without one).

**Data size and compute.** Per-task size: not found. The paper's sandbox gave each task 6 CPU cores, 34 GiB memory, 128 GiB disk and a six-hour limit, with internet on (https://latch.bio/variantbench). Some tasks (ONT repeat expansion, structural variants) may exceed our 2 GB and three-hour limits.

**Where the answers are.** In the same `eval.json` as the prompt and the data path (`grader.config.ground_truth`). A person, not an agent, must copy out the prompt and data path. Do not show agents the trajectories either: they contain answers and verdicts.

**Published scores.** Full 118 tasks: Claude Opus 5 with Claude Code 52.8%, Claude Sonnet 5.5 with Pi 51.7%, Claude Opus 5.5 with Pi 50.6%, Gemini 4 Argon 50.3%, GPT-6 Astra 46.6% (https://benchmarks.bio/benchmarks/variantbench/index.md, data 2026-11-05). Scores on the 6 public tasks only: not found, apart from the 28 public trajectories.

**Single agent vs lab.** The paper says agents fail on tacit knowledge and judgement, for example strict allele-frequency cutoffs, and LINE or structural-variant calls (1-18% across configurations), even when the trajectory shows the agent knew the concept. A challenger who asks "does this filter throw away real signal?" fits that. Only 6 tasks, so it shows examples, not a rate.

**Verdict: possible with work.** Work: extract prompts and data paths, test-download each task's data, drop any task over 2 GB or needing hours of alignment. Likely 3-6 tasks, once per arm.

## 5. ScienceAgentBench (bioinformatics and computational chemistry)

**Where.** Inputs: Hugging Face `osunlp/ScienceAgentBench`, split `verified` only (CC BY 4.0). Code: https://github.com/OSU-NLP-Group/ScienceAgentBench (MIT). Datasets, evaluation programs, gold programs and rubrics are in one zip (`benchmark_verified.zip`) with a password printed in the README; the authors ask that it not be redistributed.

**Task format.** The agent writes one Python program that reads `benchmark/datasets/...` and saves `pred_results/<file>`. Verified split, counted by me from the rows: Bioinformatics 27 (16 figures, 11 CSV/JSON/TXT), Computational Chemistry 20 (9 figures, 11 other).

**Grading.** Per-task evaluation programs. Figure tasks are judged by GPT-4o, so an OpenAI key is required (README). Docker harness recommended (SWE-bench style; all 102 in about 30 minutes on 8 threads).

**The catch.** Of the 22 non-figure bio and chem tasks, most are drug-property models: ClinTox toxicity, DAVIS drug-target binding, HIV activity, Ames toxicity, liver injury (4 tasks), blood-brain barrier, antibiotic hit filtering, Papyrus bioactivity, Tox21, protein-ligand fingerprints. That breaks our no-drug-discovery rule. Five more are materials science (band gap, bulk modulus, perovskite energy, refractive index, diffusion). What is left for computational biology: cell counting on BBBC002 images (trains a deep model), protein melting temperature, and saliva sample features. That is 3 tasks.

**Where the answers are.** Gold programs sit in the same zip as the datasets. We would have to unzip it on our side and copy only `datasets/` to agents.

**Published scores.** HAL (paused): o3 33.33%; Claude Sonnet 4.5 High, Claude 3.7 Sonnet High, GPT-5 Medium 30.39%; newest model listed is Claude Haiku 4.5 (October 2025) (https://hal.cs.princeton.edu/scienceagentbench). Mimosa, March 2026, same model DeepSeek-V3.2: single agent 38.2%, one-shot team 32.4%, iterative team 43.1% (https://arxiv.org/html/2603.28986). D3-Gym (2026) reports only open-weight Qwen3 models. Per-domain scores and late-2026 frontier scores: not found.

**Single agent vs lab.** Mimosa shows a naive team can do worse than one agent here, and a learning team a little better. With 3 usable tasks we could not show either.

**Verdict: skip.** Too few non-drug computational biology tasks, figure tasks need a judge, and the newest scores are from 2025 models.

## Ranking and first move

1. **BioDSA-1K**: best fit to "reject wrong hypotheses", cheapest to start, enough tasks for a rate. Start first.
2. **GenoTEX**: strongest published evidence that checking helps, clear headroom, but 2-3 hours of setup.
3. **LABBench2 CloningQA**: runs today, unsaturated, but 14 tasks and refusal risk.
4. **VariantBench**: current-model scores and real headroom, but data sizes unknown and compute heavy.
5. **ScienceAgentBench**: skip.

Open items not resolved: BioDSA-1K dataset licence and per-study data size; whether LABBench2 needs a gated Hugging Face token; VariantBench per-task data size and whether `latch://` downloads need an account; GenoTEX time per problem.
