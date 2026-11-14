# Study-scale bioinformatics benchmarks for agents (scouting, 14 Nov 2026)

Scope: long-horizon, multi-step benchmarks where an agent analyses real or realistic biology data end to end. Cheaper alternatives to BixBench3. The LatchBio benchmarks.bio family belongs to another scout, so scBench-Long gets only a short entry here.

Every number has a source link. "Not found" means I looked and could not find it. Scores marked "self-reported" or "third-party" were not checked against an official leaderboard.

## Summary

| Benchmark | Tasks | Grading | Best published score (model, harness) | Runnable today? | Compute | Verdict |
|---|---|---|---|---|---|---|
| **GeneBench-Pro** (OpenAI, Jun 2026) | 129 problems; 10 public | Automatic: pass/fail against numeric targets within set tolerances | 37.8% GPT-6 Astra (third-party listing of a launch post); 31.5% GPT-5.6 Sol Pro; best non-OpenAI model is Claude Opus 4.8 at 16.0% | Yes, for the 10 public problems | Small staged tables (2 to 9 files), standard Python stack; laptop is fine | **Top pick.** Low scores. The failure the authors name ("notices the clue, doesn't act on it") is what a reviewer agent catches. |
| **OmicsBench** (Shaked & Nagisetty, Oct 2026) | 97 tasks, 662 subtasks; 3 public | Automatic: 58 biology-aware graders against an answer key | 0.57 Opus 5 on OpenCode (single pass); 0.67 with feedback retries | Only 3 tasks; the other 94 need a request to the authors | 4 cores, 12 GB RAM, 20 GB disk, no network; up to 3.6 h per task | **Top pick if we can get the private tasks.** Same spirit as BixBench3 on a laptop-sized box. Agents fail by using their own criteria, which a checker can catch. |
| **Terminal-Bench-Science 0.1** (Stanford / Harbor, Aug 2026) | 70 tasks; 19 in life sciences | Automatic: task-specific tests | 30.0% Claude Opus 5 + Claude Code (official, Aug); a third-party site lists 75.3% for a newer model | Yes (Apache-2.0, Harbor) | Per-task limits not found; the docs show runs on Modal/Daytona cloud | **Third pick.** Open and well built, but only 19 biology tasks, and they are workflows, not study reproductions. The current top score is unclear. |
| **scBench-Long** (LatchBio, Jun 2026) | 21 evaluations from 5 studies | Automatic pass/fail on a fixed answer vocabulary; judge rubrics for diagnosis only | 25.4% (16/63 runs) Claude Opus 4.8 + Claude Code | Covered by the other scout | Raw single-cell data; size not found | Good fit, but it belongs to the other scout. |
| **BixBench** v1.5 (FutureHouse, 2025) and **BixBench-Verified-50** (Phylo, Feb 2026) | 205 questions over 60 capsules; Verified: 50 questions | LLM judge (GPT-4o) plus exact and range matching | Full set: 71.2% Ploid AI (self-reported). Verified-50: 92% (Ploid AI, Axon), 90% (Claude Code + Opus 4.7) | Yes (Apache-2.0, needs a Hugging Face login) | Light per question; the full multi-model agentic run takes 24 to 48 h | **Avoid.** An audit found 38% of tasks broken and calls it effectively saturated. Answers are public. |
| **BixBench 2** | n/a | n/a | n/a | Not found: no such release turned up | n/a | n/a |
| **CORE-Bench Hard / v1.1** (Princeton) | 45 papers (13 medical); v1.1: 39 tasks | Automatic answer match | 77.8% Claude Opus 4.5 + Claude Code (95.5% after manual checks) | Yes | Code Ocean capsules | **Avoid.** The authors declared it solved. |
| **BioAgent Bench** (ICML 2026) | 10 pipelines | LLM judge | 100% pipeline completion, Claude Opus 4.5 | Yes | Heavy on some tasks (e.g. an exome with about 75M reads) | **Avoid.** Saturated on completion, and too few tasks. |
| **ClaroAI-Bench** (May 2026) | 35 NIH-funded papers | 5-part rubric scored by LLMs | 60.6% (20/33) Claude Code (paper); its dataset card says 54.5% (18/33) | Yes (Hugging Face, Zenodo) | Needs internet to fetch data and code | **Weak.** Small, scored by judges, numbers inconsistent, single author. |
| **BioML-bench** (2025) | Task count not found | Automatic metrics compared with human baselines | Agents score below human baselines; no single headline number found | Yes (MIT, pre-release) | Not found | **Off-target.** It is about building ML models, not reproducing a study. |
| **HeurekaBench / sc-HeurekaBench** (ICLR 2026) | Not found | Not found | Not found | Not found | Not found | Worth a look later. Adding a critic module helped open-model agents by up to 22%. |

BixBench3, for reference: 20 studies, best average 0.523, needs heavy compute ([Edison](https://advances.edisonscientific.com/research/bixbench3-benchmarking-study-scale-computational-biology/)).

A caution that applies to every pick: if we run only the public subset, our score cannot be compared directly with a full-benchmark record. We would need to run a strong single model ourselves on the same subset, under the same harness and time limits, and compare our team against it.

---

## GeneBench-Pro (OpenAI, 30 June 2026)

**What the agent does.** Each problem stages a messy dataset (from genomics, quantitative biology or translational medicine), gives a short prompt, and names a target quantity tied to a decision. The agent must clean the data, choose a method, revise it when diagnostics disagree, and return one JSON answer. There are 129 problems in 10 domains. The data are simulated, so the true answer is known. ([paper PDF](https://cdn.openai.com/pdf/21938268-21af-442f-af93-3b2249afb241/genebench-pro.pdf), [OpenAI post](https://openai.com/index/introducing-genebench-pro/))

**Grading.** Automatic. Each problem passes or fails against "recoverable targets under calibrated tolerances". Each problem was checked by testing plausible wrong analyses, to confirm they miss by a clear margin. The prompt also tells the agent that "analytical reasoning quality is graded alongside numerical correctness". I did not find how that reasoning part is scored. ([paper](https://cdn.openai.com/pdf/21938268-21af-442f-af93-3b2249afb241/genebench-pro.pdf))

**Where it lives.** 10 of the 129 problems are open; 50 go to Artificial Analysis for third-party testing; the rest are private ([Jeremy Li's post](https://jeremyhli.org/writing/genebench-pro/)). The public package is on Hugging Face at [openai/genebench-pro-public-package](https://huggingface.co/datasets/openai/genebench-pro-public-package). Each problem folder holds the data, the prompt, an `eval_config.json` and a `report_public.pdf`. Licence: not found.
- **Leak risk: high.** Each public report includes "an analysis walkthrough corresponding with the correct answer" ([paper](https://cdn.openai.com/pdf/21938268-21af-442f-af93-3b2249afb241/genebench-pro.pdf)). The eval configs most likely hold the targets. Our agents must never see these files: copy only the data and the prompt into the sandbox.

**What it takes to run.** An isolated workspace with staged files and "a standard scientific Python stack" ([paper](https://cdn.openai.com/pdf/21938268-21af-442f-af93-3b2249afb241/genebench-pro.pdf)). The public problems have 2 to 9 data files each ([HF viewer](https://huggingface.co/datasets/openai/genebench-pro-public-package)). Data size and time per problem: not found. Tables of this kind should run on an 8-core, 16 GB laptop. Published runs used 5 to 10 attempts per problem ([BenchmarkList](https://benchmarklist.com/benchmarks/genebench_pro/)).

**Published scores** (mean pass rate over 129 problems):
- GPT-6 Astra 37.8%. Third-party listing citing a launch post dated 3 Sep 2026 ([BenchmarkList](https://benchmarklist.com/benchmarks/genebench_pro/)); another aggregator says 37.1%. Not verified.
- GPT-5.6 Sol Pro (Extended) 31.5%; GPT-5.6 Sol at max reasoning 28.7%; GPT-5.5 at max reasoning 12.0%. Best non-OpenAI model: Claude Opus 4.8 at 16.0% ([Jeremy Li](https://jeremyhli.org/writing/genebench-pro/), [BenchmarkList](https://benchmarklist.com/benchmarks/genebench_pro/)). Open models score low (GLM 5.2 4.6%).
- Over half the problems score 0% even for the top OpenAI runs (55.8% for GPT-5.6 Sol Pro) ([BenchmarkList](https://benchmarklist.com/benchmarks/genebench_pro/)).
- Harnesses: OpenAI's own setup. No multi-agent or scaffolded system has been tried: not found.
- Note: OpenAI built this benchmark and its own models lead it.

**Why agents fail.** The authors describe "a consistent gap between noticing and acting". Agents spot the diagnostic signal but do not change the estimator or the analysis path ([GeneBench paper](https://cdn.openai.com/pdf/6dc7175d-d9e7-4b8d-96b8-48fe5798cd5b/oai_genebench_benchmark.pdf), [GeneBench-Pro paper](https://cdn.openai.com/pdf/21938268-21af-442f-af93-3b2249afb241/genebench-pro.pdf)).

**The original GeneBench** (Apr 2026) had 103 problems. Best scores: GPT-5.5 Pro 33.2%, GPT-5.5 at xhigh reasoning 25.0%, Gemini 3.1 Pro 11.2% ([paper](https://cdn.openai.com/pdf/6dc7175d-d9e7-4b8d-96b8-48fe5798cd5b/oai_genebench_benchmark.pdf)). GeneBench-Pro replaces it.

**Judgement.** (a) Yes: 10 problems × several attempts fits in 12 hours. (b) Yes: big headroom; top single models pass about a third of problems. (c) Yes, strongly: "noticed but didn't act" is the failure a separate reviewer agent is built to catch. Weak point: only 10 public problems, so our results will be noisy.

## OmicsBench (Shaked & Nagisetty, arXiv 2610.21074, 15 Oct 2026)

Do not confuse it with LatchBio's "OmicsBench v1", a separate 784-task meta-benchmark on benchmarks.bio ([phage.directory](https://phage.directory/items/latchbio-releases-omicsbench-v1-a-784-task-meta-benchmark-of-ai-agents-on-omics-data-analy-c2468816fbeb57deae6dae987aaa6906)). That one belongs to the other scout.

**What the agent does.** Each task is a published study's dataset taken from raw data to a scientific result. The work is split into dependent subtasks linked as a graph. Each subtask arrives as one user turn in a single continuous session. There are 97 tasks and 662 subtasks, with a median of 7 subtasks per task ([arXiv](https://arxiv.org/abs/2610.21074)).

**Grading.** Automatic. 58 biology-aware graders check each answer file against a key. Gene, organism and metabolite names are matched through reference databases, not exact strings. Each subtask scores 0 to 1, and the task score is the mean. In the optional "gated" mode, an answer scoring below 0.7 is sent back for a retry (up to 3 attempts) ([arXiv HTML](https://arxiv.org/html/2610.21074)).

**Where it lives.** Runner and graders: MIT licence. Three public tasks: CC BY 4.0, 5.26 GB with their datasets ([Hugging Face](https://huggingface.co/datasets/gideonshaked/OmicsBench)). Grader databases are a separate download ([HF](https://huggingface.co/datasets/AfterQuery/OmicsBench-grader-databases)). The other 94 tasks are private; researchers "may contact the authors" ([arXiv HTML, Appendix E](https://arxiv.org/html/2610.21074)).
- **Leak risk:** the three public tasks ship with their answer keys. Our agents must not see `questions/` answer fields.

**What it takes to run.** Each run had its own Docker container with 4 cores, 12 GB RAM, 20 GB disk and **no network** (Appendix K). Each turn is capped at 30 minutes. Datasets range from 365 KB to 8.4 GB. The slowest agent averaged 3.6 hours per task, with up to 350 tool calls and 110M tokens ([arXiv](https://arxiv.org/abs/2610.21074)). Needs Python 3.13+, Docker and an API key ([runner README](https://huggingface.co/datasets/gideonshaked/OmicsBench/blob/main/omicsbench_runner/README.md)). This fits a 16 GB laptop, one task at a time.

**Published scores.** 12 models on 3 harnesses (OpenCode, Claude Code, Codex). Best: Opus 5 on OpenCode, 0.57 single-pass and 0.67 gated. Worst: Grok 4.3 at 0.43 and 0.54. Opus 5 and GPT-5.6 Sol on their own harnesses landed within 0.02 of their OpenCode scores. No multi-agent system was tested. Only 22 to 34% of subtasks reach the 0.7 gate within three attempts ([arXiv HTML](https://arxiv.org/html/2610.21074)).

**Why agents fail.** The authors list these causes ([arXiv HTML](https://arxiv.org/html/2610.21074)):
- The most common failure is choosing the wrong set of results: submitting too many or too few, for example a fixed "top N genes" per group. This is 25 to 71% of failures.
- Agents use their own selection criteria instead of the ones the subtask states.
- Agents write answers from memory without opening the data.
- Scores drop 0.06 to 0.12 after the first turn, and drop again on subtasks that build on earlier answers.
- Gated retries invite answer-guessing, so the authors excluded closed-choice subtasks from that analysis.

**Judgement.** (a) Partly: the 3 public tasks run easily, but 3 is too few to claim anything. To be meaningful we need the private set, which means emailing the authors today. That is an outside message, so it needs your approval. (b) Yes: 0.57 leaves headroom. (c) Yes: "follow the stated criteria" and "check the answer against the data" are exactly what a checker agent enforces. The benchmark's own design choice, the gated retry, roughly mimics a reviewer and adds about 0.1.

## Terminal-Bench-Science 0.1 (Stanford / Harbor, 27 Aug 2026)

**What the agent does.** Expert-written research workflows run in a terminal: data analysis, statistical inference, simulation, model fitting and similar. There are 70 tasks across five science areas, 19 of them in the life sciences ([announcement](https://www.terminal-bench-science.ai/announcement), [Frontier Atlas](https://www.frontieratlas.tech/benchmarks/terminal-bench-science-life)).

**Grading.** Automatic, with task-specific tests on concrete outputs. Three trials per task ([announcement](https://www.terminal-bench-science.ai/announcement)).

**Where it lives.** [GitHub](https://github.com/harbor-framework/terminal-bench-science/), Apache-2.0. It runs through the `harbor` CLI and is open to anyone today. Whether tests and solutions sit in the repository: not checked. It is a Terminal-Bench-style repo, so assume the tests are visible and keep them out of the agent's sandbox.

**What it takes to run.** The README examples use Modal or Daytona cloud sandboxes with 32 tasks in parallel. Per-task resource limits and local Docker support: not found. Running Opus 5 over all 70 tasks × 3 trials cost $7.0k, about $33 per trial ([announcement](https://www.terminal-bench-science.ai/announcement)).

**Published scores.** Official, August: Claude Opus 5 + Claude Code 30.0%, GPT-5.6 Sol + Codex 22.4%, Claude Fable 5 + Claude Code 21.4%, Claude Opus 4.8 10.5% ([announcement](https://www.terminal-bench-science.ai/announcement)). Life-science-only scores: not found.
- A third-party site lists far higher "measured" scores as of 13 Nov: Claude Mythos 5.2 75.3%, Claude Fable 5.2 74.6% ([benchgap](https://benchgap.net/b/terminal-bench-science/current)). Not verified. We would have to check the official leaderboard before claiming a record.

**Why agents fail.** Not described in what I read.

**Judgement.** (a) Yes: 19 life-science tasks fit in 12 hours. (b) Unclear: 30% officially, but possibly about 75% now. (c) Unknown. These are scientific workflows rather than study reproductions.

## scBench-Long (LatchBio, arXiv 2606.26563, 25 Jun 2026) — short entry; the other scout owns it

21 evaluations from 5 single-cell studies: melanoma CD8 T cells, RNA+ATAC, a human–monkey chimera, KRAS lung tumours, and COVID-19 lung. The agent starts from raw or near-raw data and must give a structured biological conclusion. Grading is automatic pass/fail over a fixed answer vocabulary; judge rubrics are used only for diagnosis. Best: Claude Opus 4.8 + Claude Code passed 16/63 runs (25.4%); Gemini 3.5 Flash + PI passed 14/63 ([arXiv](https://arxiv.org/abs/2606.26563), [LatchBio blog](https://blog.latch.bio/p/benchmarking-ai-agents-on-long-horizon)).

Agents often found the right intermediate results but did not assemble them into the right claim ([blog](https://blog.latch.bio/p/benchmarking-ai-agents-on-long-horizon)). Specific patterns:
- Familiar marker-gene knowledge overrode what the data showed.
- The most abundant class was taken to be the most important.
- A correlation was read as a mechanism.
- Agents used RNA alone when the task needed RNA and chromatin data together.

These are checkable errors, so the benchmark is a good fit for a checking team.

## BixBench (FutureHouse/Edison, 2025) and BixBench-Verified-50 (Phylo, Feb 2026)

**What the agent does.** The agent answers questions about a published analysis by working in a Jupyter notebook on that analysis's data. v1.5 has 205 questions from 60 "capsules" (data plus original notebook) ([GitHub](https://github.com/Future-House/BixBench), [HF](https://huggingface.co/datasets/futurehouse/BixBench)). The questions are single-answer, so this is less "whole study" than BixBench3.

**Grading.** Open answers are graded by an LLM judge (GPT-4o); exact-match and range checks cover the rest ([GitHub](https://github.com/Future-House/BixBench)). Verified-50 has 50 questions over 33 capsules, with 17 questions revised ([HF](https://huggingface.co/datasets/phylobio/BixBench-Verified-50)).

**Where it lives.** Apache-2.0; needs a Hugging Face login; Docker image `futurehouse/bixbench:aviary-notebook-env` ([GitHub](https://github.com/Future-House/BixBench)).
- **Leak risk: very high.** The answers sit in the public dataset, and the Hugging Face viewer shows them on the dataset page. Verified-50 capsules even include the executed reference notebook ([HF](https://huggingface.co/datasets/phylobio/BixBench-Verified-50)).

**What it takes to run.** Light per question. The full multi-model, 5-replica agentic run takes 24 to 48 hours ([GitHub](https://github.com/Future-House/BixBench)). Verified-50 is 2.81 GB.

**Published scores** (mostly self-reported):
- Full set: Ploid AI 71.2%, BIOS 64.4%, Biomni Lab 52.2%, Edison Analysis 42.4%, Claude Code (Opus 4.6) 39.5%, OpenAI Agents SDK (GPT-5.2) 38.5% ([Ploid blog](https://ploid.ai/blog/evaluating-ai-agents-in-bioinformatics)).
- Verified-50: Ploid AI 92.0% ([Ploid](https://ploid.ai/blog/evaluating-ai-agents-in-bioinformatics)), Axon 92.0% ([Mirror Physics](https://mirrorphysics.substack.com/p/benchmarking-scientific-intelliogence)), K-Dense Web 90.0% ([K-Dense](https://www.k-dense.ai/blog/bixbench-verified-50)), Claude Code + Opus 4.7 90% ([SOTA2](https://www.sota2.com/research/sota/quantitative-reasoning-and-autonomous-analysis-on-bixbench-verified-50-full-set)).
- OpenAI reported GPT-5.5 at 80.5% "on BixBench", which version is unclear ([llm-stats](https://llm-stats.com/benchmarks/bixbench)). The audit below suspects it was Verified-50.
- Multi-agent and scaffolded systems (Biomni Lab, K-Dense, Ploid) beat plain single models by a wide margin on the original set. On Verified-50, Claude Code + Opus 4.6 scored 65.3% against Biomni Lab's 88.7% ([K-Dense](https://www.k-dense.ai/blog/bixbench-verified-50)). Single agents with added skills now reach about 90% too ([bioRxiv](https://www.biorxiv.org/content/10.64898/2026.04.28.721523v1)).

**Why agents fail.** Mostly the benchmark's fault. An independent audit found that flawed tasks and judge errors explain about 70% of failed submissions ([Generality Labs, Sep 2026](https://generality.org/blog/posts/bixbench-broken-ceiling/)):
- 20% of tasks have reference answers that cannot be reproduced from the supplied data.
- A further 17% depend on analysis choices the question does not state.
- The judge has a 9.2% false-negative rate.
- Correcting these raises scores by up to 42 points. The audit calls BixBench "effectively saturated".

**BixBench 2.** Not found. Searches turned up only BixBench (2025), Verified-50 (2026) and BixBench3 ([Edison](https://advances.edisonscientific.com/benchmarks/bixbench3/)).

**Judgement.** (a) Yes. (b) No: saturated once broken tasks are removed. (c) Partly. Clarifying ambiguous questions is something a team could help with, but the published numbers are mostly measuring noise.

## CORE-Bench (Princeton)

**What the agent does.** Given a paper's code repository, the agent installs dependencies, runs the code, and answers questions about the results. The Hard public test set has 45 papers: 18 from computer science, 14 from social science, 13 from medicine ([HAL](https://hal.cs.princeton.edu/corebench_hard), [arXiv 2606.26158](https://arxiv.org/html/2606.26158)).

**Grading.** Automatic answer match. Answers for the test set are not shipped in the clear: HAL traces are encrypted.

**Scores.** Claude Opus 4.5 + Claude Code (Carlini's scaffold): 77.8%, or 95.5% with manual validation. The organisers "declared CORE-Bench solved" ([HAL](https://hal.cs.princeton.edu/corebench_hard)). The Claude Code scaffold nearly doubled the CORE-Agent scaffold's score with the same model (42%), so scaffolding mattered more than the model ([Kapoor](https://substack.com/@sayash/note/c-183913356)).
- Follow-up versions: v1.1 (39 tasks, 15 task errors and 20 shortcuts fixed) and CORE-Bench OOD (19 non-biology tasks) ([arXiv 2606.26158](https://arxiv.org/html/2606.26158)).

**Judgement.** (a) Yes. (b) No. (c) Not relevant: no headroom left.

## BioAgent Bench (Fa et al., ICML 2026)

**What the agent does.** Ten end-to-end pipelines: RNA-seq with DESeq2, GIAB variant calling, metagenomics, single-cell, and others ([arXiv](https://arxiv.org/html/2601.21800v4), [GitHub](https://www.github.com/bioagent-bench/bioagent-bench)).

**Grading.** An LLM judge compares outputs with truth files. The README warns: "Do not expect 'Truth' files or evals to be correct unless explicitly stated" ([GitHub](https://www.github.com/bioagent-bench/bioagent-bench)). Truth files can be downloaded, so the leak risk is high.

**Scores.** Pipeline completion: Claude Opus 4.5 100%, Gemini 3 Pro 96.6%, GPT-5.2 92.5% ([author page](https://dionizijefa.com/research/bioagent-bench)). Agents fail mainly under deliberate disruption: corrupted inputs, decoy files, and padded prompts ([arXiv](https://arxiv.org/html/2601.21800v4)).

**Judgement.** (a) Some tasks are heavy (an exome with about 75M reads). (b) No: completion is saturated. (c) The disruption tests are the interesting part, but they are not the record the track asks about.

## ClaroAI-Bench (O'Connell, bioRxiv, May 2026)

**What the agent does.** For 35 NIH-funded papers, the agent finds the data and code, rebuilds the environment, runs the analysis, and checks the published claims. Scoring uses five 0–2 rubric parts, from "data findable" to "results match" ([bioRxiv](https://doi.org/10.64898/2026.05.08.723611)).

**Grading.** Three frontier models score each run. Agreement between them on "environment rebuilt" is only r = 0.51 to 0.81 ([bioRxiv](https://doi.org/10.64898/2026.05.08.723611)).

**Scores.** Claude Code (all tools) reproduced 20/33 papers (60.6%) in the abstract. The dataset card says 18/33 (54.5%) ([HF](https://huggingface.co/datasets/kyleaoconnell22/claroai-bench)). A bash-only agent scored 0%.

**Where it lives.** [Hugging Face](https://huggingface.co/datasets/kyleaoconnell22/claroai-bench) and Zenodo. Per-paper scores and logs are public. Running it needs internet access to download data.

**Judgement.** (a) Maybe. (b) Some headroom. (c) Partly. But the judge-based scoring, the inconsistent numbers and the single author make it weak ground for a record claim.

## BioML-bench (Miller et al., 2025)

The agent builds ML models for protein engineering, single-cell, imaging and drug-discovery tasks. Scoring uses standard metrics compared with human baselines. MIT licence; pre-release. Agents underperform humans on average, and biology-specialist agents (STELLA, Biomni) showed no consistent edge over generalists ([bioRxiv](https://www.biorxiv.org/content/10.1101/2025.09.01.673319v3), [GitHub](https://github.com/science-machine/biomlbench)). Task count, compute needs and the current best score: not found. It is off-target, because it is about model building, not reproducing a study.

## HeurekaBench (Panigrahi et al., ICLR 2026)

A framework that builds open-ended research questions from published single-cell studies and their code, checked against the reported findings. Its instance, sc-HeurekaBench, compares single-cell agents. Its notable finding for us: "the addition of a critic module can improve ill-formed responses for open-source LLM-based agents by up to 22%" ([ICLR paper](https://proceedings.iclr.cc/paper_files/paper/2026/file/12d25b1673452b017fe9c866d9f494a0-Paper-Conference.pdf)). Task count, grading details, availability and best scores: not found in the part I read.

---

## Cross-cutting notes

- **Checking helps; the record shows it.** CORE-Bench nearly doubled (42% to 78%) from a better scaffold with the same model. On BixBench, scaffolded systems beat plain single models by 20 to 50 points. On OmicsBench, one round of "your answer failed, retry" adds 0.09 to 0.13. HeurekaBench's critic added up to 22%.
- **Answer leaks are everywhere.** BixBench, Verified-50, the OmicsBench public tasks, the GeneBench-Pro public package and BioAgent Bench all ship answers or reference notebooks next to the data. Our sandbox must hold only the data and the prompt.
- **Grader quality matters as much as agent quality.** Both BixBench and CORE-Bench were found to have 15 to 38% bad tasks once agents got good. Benchmarks built on simulated data with a known answer (GeneBench-Pro) or reviewed by experts (OmicsBench, Terminal-Bench-Science) are safer targets.
