# Literature review: self-improvement loops for an agent lab

Scope: published work from 2023 to 2026 on automatic optimisation of agents and prompts, which inputs the proposer should see, how to accept changes on a small noisy dev set, agent training beyond prompting, and test-time compute. Every number below was read from the cited source unless it is marked **[not verified]** or **[our arithmetic]**. No benchmark items, datasets or answer files were opened.

## Summary: what matters for our loop

- **Our 20-task panel cannot see small real gains.** Using the paired power formula in Miller (2024), a 20-task paired comparison at 80% power detects only about a 20-24 point change, which is 4-5 tasks net **[our arithmetic, assumed per-task difference variance 0.10-0.15]**. Typical single prompt edits in the literature gain 2-14 points. Loop 1's results (net 0, 0, +1) fit "small or zero effects we cannot resolve" as well as "bad moves".
- **Rich per-example feedback beats scores, but only when it is structured.** GEPA reads traces and text feedback and beats MIPROv2 (scores plus summaries) by more than 10% and GRPO by 6% on average with up to 35x fewer rollouts. A 2026 GEPA ablation found that replacing per-dimension feedback with one scalar turned held-out transfer from +0.075 to −0.021. But raw lists of error cases did not help OPRO, and LLMs reflecting on errors tend to repeat their priors. So give the proposer *why* a task failed, broken into named parts, not just the failing items.
- **Validation gains overstate real gains.** In the closest study to our setting (GEPA on a deep-research agent, 24-task validation set, 240 metric calls), the validation gain was 2.6x the held-out gain. OPRO saw training accuracy 5-20% above test. Expect any accepted move to shrink on fresh tasks.
- **Spend evaluations where the information is.** Staged evaluation (cheap screen first, full panel only for survivors) is standard in DGM, AlphaEvolve and Hyperband-style prompt selection. Re-running only the tasks whose outcome changed is the cheapest way to tell noise from effect.
- **Prompt and per-agent edits pay before structure edits.** In MASS, optimising each block's prompt added about 7 points, topology search about 3 more, and workflow-level prompt tuning under 1.
- **Majority voting is a strong, cheap move for a three-way verdict.** Across seven benchmarks, voting explains most of the gain credited to multi-agent debate. One strong agent with good prompts roughly matches the best discussion setups. Resampling has a ceiling when the verifier is imperfect: often under 10 useful attempts.
- **Fine-tuning is not worth it at our budget.** RL on small open models can beat prompted frontier agents on narrow, verifiable tasks: a 3B model beat a Claude-3.5-Sonnet agent by 22% on 12 Kaggle tasks. But one RL run costs around $10,000-20,000. API-only "experience library" methods (Training-Free GRPO about $18 of learning, ACE +10.6% on agent tasks) are the feasible version.

---

## Q1. Automatic optimisation of agent workflows and prompts

### Comparison table

| Method (year) | What the optimiser sees | Evaluations to a gain | Size of gains | Known failure modes |
|---|---|---|---|---|
| OPRO (2023) | Top 20 past prompts with training scores, plus 3 random examples | 200 steps x 8 prompts by default; a strong GSM8K prompt appeared at step 6 | Up to +8% GSM8K, up to +50% on BBH tasks over human prompts | Train accuracy often 5-20% above test; error cases did not help |
| Promptbreeder (2023) | Fitness scores on a random training batch; mutation prompts also evolve | Population about 50, 20-30 generations **[not verified, secondhand]** | GSM8K 83.9% vs OPRO 80.2% (PaLM 2-L, zero-shot) | Best GSM8K prompt was the single word "SOLUTION", a sign of fitting the scorer |
| EvoPrompt (2023) | Scores only (dev-set fitness) | Population 4-12 tested | Up to +25% on BBH; averages nearer 2.5-3.5% **[secondhand]** | Larger populations cost much more for small gains |
| ProTeGi / APO (2023) | Text "gradients" criticising the prompt on minibatches; beam search plus bandit selection | Not in abstract | Up to +31% over the initial prompt | Not stated in abstract |
| DSPy MIPRO / MIPROv2 (2024) | Dataset summary, program summary, bootstrapped demos, past instructions with scores | 20-50 full evaluations (about 300 minibatch trials); 500 train / 500 dev examples | Best on 5 of 7 programs, up to +13% (Llama-3-8B) | Demos usually matter more than instructions; struggles without a handwritten seed prompt |
| TextGrad (2024) | Natural-language feedback from GPT-4o on each batch of 3 | 12 iterations, 36 training examples; accept only if validation improves | Object counting +14.1, GSM8K +8.2, word sorting +3.1 over zero-shot CoT; GPQA 51%→55% | Only matched DSPy on 2 of 3 tasks; no seed variance reported |
| Trace / OptoPrime (2024) | Full execution trace of the program plus output feedback | Varies by task | Google-proof QA 59.6 vs TextGrad 53.2; other tasks within 1 point | Masking the trace hurt (no numbers given) |
| ADAS (2024) | Archive of agent code plus aggregate scores only | 25-30 iterations; ARC validation 20 tasks, each run 5 times | DROP F1 +13.6, MGSM +14.4; MMLU +2.0, GPQA +1.7 with overlapping intervals | Authors call scalar scores "information-poor"; small validation sets |
| AFlow (2024) | Parent workflow, tree of past edits with scores, and per-example predictions vs expected outputs | 20 rounds; each candidate run 5 times on validation | +5.7% average over best manual workflow; MBPP 83.4 vs 73.6 | Some rounds made things worse; workflows tied to one executor model |
| Gödel Agent (2024) | Its own code and runtime state, validation feedback, error messages | 30 iterations x 6 cycles; validation 128 items (32 for GPQA) | Constrained version: MGSM +10.8 over ADAS, others +0.3 to +1.5; about $15 vs about $300 for ADAS | 92% of runs had temporary drops; 14% ended worse than the start; 4% broke themselves |
| SICA (2025) | Archive summaries, plus the best and worst problems per iteration | 15 iterations on 50-task subsets; about $7,000 | SWE-bench Verified subset 17%→51-53%; reasoning tasks no gain | Path dependence on early ideas; regressions; scaffolding hurt reasoning models |
| Darwin Gödel Machine (2025) | Its own benchmark evaluation logs | 80 iterations, staged evaluation (10 → 50/60 → 200 tasks); about $22,000 per SWE-bench run | SWE-bench 20.0%→50.0%; Polyglot 14.2%→30.7% | Drops below parent at some steps; reward-hacking risk discussed |
| GEPA (2025) | Module inputs, outputs, reasoning traces, score plus text feedback (e.g. failed rubric items) | Reflection minibatch of 3; matched GRPO's best after 243-1,179 rollouts vs GRPO's 24,000 | +6% average over GRPO (up to +20%); over 10% over MIPROv2; +12% AIME-2025 | Most budget spent on validation; crossover hurt one model |
| AlphaEvolve (2025) | Program database plus scores from automated evaluators; can add LLM-graded qualities | Evaluation cascade: small tests first, then larger | 4x4 complex matrix multiply in 48 multiplications; 0.7% of Google's compute recovered; 23% kernel speedup; up to 32.5% FlashAttention speedup | Needs a machine-gradeable evaluator; ablation sizes not given in text |

### Sources and notes

- **OPRO**: Yang et al., "Large Language Models as Optimizers", 2023 (ICLR 2024), https://arxiv.org/abs/2309.03409. Section 5.4: without a held-out set, training accuracy was "often 5% to 20% higher" than test, though test still beat human prompts. Suggested fixes: larger training set and early stopping. Removing scores or examples from the meta-prompt hurt. Using error cases instead of random examples gave similar results; the authors say error cases alone are not informative enough.
- **Promptbreeder**: Fernando et al., 2023, https://arxiv.org/abs/2309.16797. GSM8K 83.9 vs OPRO 80.2 confirmed via search snippets of the paper table. Population and generation counts come from a third-party reimplementation **[not verified]**.
- **EvoPrompt**: Guo et al., 2023 (ICLR 2024), https://arxiv.org/abs/2309.08532. "Up to 25%" on BBH is from the abstract. The 2.5-3.5% averages come from a secondary summary **[not verified]**.
- **ProTeGi**: Pryzant et al., "Automatic Prompt Optimization with 'Gradient Descent' and Beam Search", EMNLP 2023, https://arxiv.org/abs/2305.03495.
- **MIPRO**: Opsahl-Ong et al., "Optimizing Instructions and Demonstrations for Multi-Stage Language Model Programs", EMNLP 2024, https://arxiv.org/abs/2406.11695. Uses noisy minibatch scores to fit a Bayesian surrogate, then fully evaluates top candidates every few steps. Lessons: instructions matter most when the task has rules that examples cannot show; grounding the proposer helps on average but the best strategy varies by task.
- **TextGrad**: Yuksekgonul et al., 2024 (later in Nature), https://arxiv.org/abs/2406.07496. Prompt grew much longer on GSM8K.
- **Trace**: Cheng, Nie, Swaminathan, 2024, https://arxiv.org/abs/2406.16218. Table 2 (5 seeds, GPT-4o optimiser). Trace-masking ablations are curves only **[no numbers]**.
- **ADAS**: Hu, Lu, Clune, 2024, https://arxiv.org/abs/2408.08435. Meta agent GPT-4, agents run on GPT-3.5. Search cost is in an appendix we did not read **[not verified]**.
- **AFlow**: Zhang et al., 2024 (ICLR 2025), https://arxiv.org/abs/2410.10762. Validation is 20% of data, then narrowed to high-variance items. AFlow runs each candidate 5 times because single runs gave unreliable feedback.
- **Gödel Agent**: Yin et al., ACL 2025, https://arxiv.org/abs/2410.04444. The unconstrained version's large gains (e.g. MGSM 90.6) partly come from it calling stronger models, so they are not like-for-like.
- **SICA**: Robeyns, Szummer, Aitchison, "A Self-Improving Coding Agent", 2025, https://arxiv.org/abs/2504.15228.
- **DGM**: Zhang, Hu, Lu, Lange, Clune, 2025, https://arxiv.org/abs/2505.22954. Ablations: without self-improvement, gains "taper off quickly"; without the open archive (always branch from latest), one bad change makes later progress harder. Baseline runs cost about $10,000. The hallucination / objective-hacking case study is in an appendix we did not read **[not verified]**.
- **GEPA**: Agrawal et al., "GEPA: Reflective Prompt Evolution Can Outperform Reinforcement Learning", 2025, https://arxiv.org/abs/2507.19457. Split sizes: e.g. HotpotQA 150/300/300, PUPA 111/111/221. Pareto selection (keep any candidate that is best on at least one task) gave +12.44 aggregate vs +6.05 for always mutating the current best and +5.11 for beam search. GEPA prompts are shorter than MIPROv2's (paper says "around 33% shorter" in one place and "less than 33% of the size" in another).
- **AlphaEvolve**: Novikov et al., 2025, https://arxiv.org/abs/2506.13131; numbers from the DeepMind blog, https://deepmind.google/discover/blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/. Multiple metrics at once often improved the single target metric by diversifying candidates.
- **MASS** (related): Zhou et al., "Multi-Agent Design: Optimizing Agents with Better Prompts and Topologies", 2025 (ICLR 2026), https://arxiv.org/abs/2502.02533. Average over 8 tasks (Gemini 1.5 Pro): CoT 63.54 → single-agent prompt optimisation 67.44 → per-block prompt optimisation 74.56 → topology 77.55 → workflow prompt tuning 78.40. Validation sets were only 50-100 items. Principle: optimise each agent before composing.

### Common failure modes across methods
- Overfitting a small dev set (OPRO 5-20% gap; GEPA deep-research study 2.6x inflation; see Q3).
- Noise mistaken for progress: ADAS and AFlow both re-run each candidate 5 times; Gödel Agent saw drops in 92% of runs.
- Greedy hill-climbing stalls (GEPA, DGM): keeping a diverse archive beats always building on the latest best.
- Path dependence: early weak ideas steer later ones (SICA).
- Prompt bloat and memorised answers (TextGrad prompt growth; GEPA deep-research study, where 39-52% of the best prompt was memorised answer text).

---

## Q2. Which proposer inputs matter most

**Evidence that per-example traces and reasons help:**
- **GEPA vs MIPROv2**: GEPA's reflection reads traces and text feedback; MIPROv2's proposer sees summaries and past scores. GEPA wins by over 10% and also beats GRPO (scalar reward) by 6% on average with up to 35x fewer rollouts. Caveat: GEPA also differs in search strategy (Pareto selection), so this is not a clean ablation. Source: https://arxiv.org/abs/2507.19457.
- **Scalar vs decomposed feedback, controlled ablation**: Lumer et al., "Reflective Prompt Optimization of a Deep-Research Agent: A Topic-Disjoint, Multi-Seed Protocol for Measuring Transferable Gains and Prompt-Level Overfitting", Oct 2026, https://arxiv.org/abs/2610.12601. GEPA with per-dimension feedback: held-out transfer +0.075. Same with one scalar reward: −0.021 (held-out got worse while validation rose +0.040). Finer or repeated scoring did not raise transfer; 3x repeated scoring mainly cut seed spread. Single study, one task **[treat as one data point]**.
- **ADAS authors** call aggregate scores "expensive and information-poor" (https://arxiv.org/abs/2408.08435). **AFlow**, which shows per-example predictions next to expected outputs, beat ADAS by 19.5% relative on average (https://arxiv.org/abs/2410.10762). Confounded by other design differences.
- **Trace**: masking the execution trace "leads to a significant decline in performance and stability" in robot control; numbers not given (https://arxiv.org/abs/2406.16218).
- **OPRO**: removing scores from the meta-prompt hurt; scores are still needed (https://arxiv.org/abs/2309.03409).

**Evidence that raw failure lists are not enough:**
- **OPRO**: showing error cases instead of random examples gave "similar" results; error cases alone did not tell the optimiser why predictions were wrong.
- **Ma et al., "Are Large Language Models Good Prompt Optimizers?"**, 2024, https://arxiv.org/abs/2402.02101: LLM optimisers struggle to find the true causes of errors and are "biased by their own prior knowledge". Even a valid reflection often produces a prompt the target model does not follow.
- **Zhang et al., "Which Agent Causes Task Failures and When?"**, 2025, https://arxiv.org/abs/2505.00212: the best method finds the responsible agent in multi-agent traces 53.5% of the time and the decisive step only 14.2% of the time. Some methods were below chance. So "read the trace and find the bug" is unreliable when traces are long.

**Takeaway.** The gain comes from feedback that says *what kind* of failure happened, in named parts (GEPA's rubric items, the per-dimension scores in Lumer et al.). Long raw traces and bare failure counts are weak inputs. How much better: about +10% over a scores-plus-summary proposer (GEPA vs MIPROv2), and the difference between a positive and a negative held-out result in the one controlled ablation.

---

## Q3. Small, noisy dev sets: accepting changes without fooling yourself

### How noisy are identical runs?
- **Agents on SWE-bench Verified**: Bjarnason, Silva, Monperrus, "On Randomness in Agentic Evals", 2026, https://arxiv.org/abs/2602.07150. 60,000 trajectories, 3 models, 2 scaffolds. Single-run pass@1 varies by 2.2-6.0 points depending on which run you pick; standard deviation above 1.5 points even at temperature 0; trajectories diverge within the first few percent of tokens. Reported gains of 2-3 points "may be evaluation noise". Power table (500 tasks, 80% power): detecting a 2-point gain needs 9 runs per agent at σ = 1.5%, a 1-point gain needs 36.
- **"Deterministic" settings**: Atil et al., "Non-Determinism of 'Deterministic' LLM Settings", 2024, https://arxiv.org/abs/2408.04667. Accuracy varied up to 15% across runs; best-to-worst gap up to 70%.
- **Root cause in serving**: Thinking Machines, "Defeating Nondeterminism in LLM Inference", 2025, https://thinkingmachines.ai/blog/defeating-nondeterminism-in-llm-inference/. 1,000 temperature-0 completions of one prompt gave 80 distinct outputs, because kernels are not batch-invariant and server load changes the batch size.
- **Agent reliability**: Yao et al., "τ-bench", 2024, https://arxiv.org/abs/2406.12045. pass^8 (succeed on all 8 tries) below 25% in retail, even though pass@1 is near 50%.

### Statistical practice
- **Pair by task; plan for power.** Miller, "Adding Error Bars to Evals", 2024, https://arxiv.org/abs/2411.00640. Recommendations: compare at question level (paired differences), cluster standard errors for related items, resample each question K times, and run a power analysis first. Paired analysis cut variance by one third in his example; per-model correlations across questions were 0.37-0.64. K = 2, 4, 6 samples per question cut total variance by 1/3, 1/2, 5/9 in his example. Using next-token probabilities removes sampling noise entirely. Minimum detectable effect: δ = (z_α/2 + z_β)·sqrt((ω² + σ²_A/K_A + σ²_B/K_B)/n).
  - **[our arithmetic]** For n = 20, α = 0.05, 80% power, no resampling, ω² = 0.10-0.15: δ ≈ 2.8 x sqrt(0.10/20 to 0.15/20) ≈ 0.20-0.24, i.e. 4-5 tasks net. Our net ≥ 3 rule sits below this, so it will miss many real small gains and still let some noise through.
  - **[our arithmetic]** A one-sided sign test on discordant tasks: 3 rescued / 0 damaged gives p = 0.125; 4 / 1 gives p = 0.19. Each single panel result is weak evidence; the replicate is doing the real work.
- **Prefer continuous metrics and drop noisy items.** Heineman et al., "Signal and Noise", 2025, https://arxiv.org/abs/2508.13144 (30 benchmarks, 375 models). Benchmarks with higher signal-to-noise give more reliable small-scale decisions. Interventions that help: a continuous metric instead of accuracy, filtering noisy subtasks, averaging checkpoints.
- **Account for variance in comparisons.** Madaan et al., "Quantifying Variance in Evaluation Benchmarks", 2024, https://arxiv.org/abs/2406.10229. Item-response-theory methods did not meaningfully reduce variance.
- **Racing, successive halving, multi-fidelity.** Schneider et al., "Hyperband-based Bayesian Optimization for Black-box Prompt Selection" (HbBoPs), ICML 2025, https://arxiv.org/abs/2412.07820: Hyperband spends few validation items on weak prompts and more on strong ones; they note that small random subsets raise variance and make validation-to-test gaps hard to diagnose. Shi et al., "Efficient Prompt Optimization Through the Lens of Best Arm Identification" (TRIPLE), 2024, https://arxiv.org/abs/2402.09723: prompt selection under a fixed budget as a bandit problem. DGM and AlphaEvolve both use staged cascades (small test, then larger). Classic background **[not fetched; standard references]**: F-Race/irace (López-Ibáñez et al., 2016), successive halving and Hyperband (Li et al., JMLR 2018).
- **Repeated use of one dev set inflates results.** Lumer et al. 2026 (above): validation gain +0.202 vs held-out +0.079 (2.6x); across 3 seeds, the validation-minus-held-out gap ranged 0.034-0.160. Recommendation: held-out topics, multiple seeds, verifier ablations. Kapoor et al., "AI Agents That Matter", 2024, https://arxiv.org/abs/2407.01502: many agent benchmarks lack proper holdouts, which leads to shortcuts and overfitting; evaluate cost and accuracy together. Background **[not fetched]**: Dwork et al., "The reusable holdout" (Science, 2015) on adaptive reuse of one holdout.
- **Repeat candidates.** ADAS and AFlow run every candidate 5 times on validation; GEPA keeps evaluations on a fixed Pareto set.

---

## Q4. Agentic training beyond prompting

### Reported gains
- **Trajectory fine-tuning.** FireAct (Chen et al., 2023, https://arxiv.org/abs/2310.05915): 500 GPT-4 trajectories fine-tuned Llama2-7B for +77% on HotpotQA. SWE-Gym (Pan et al., ICML 2025, https://arxiv.org/abs/2412.21139): 2,438 real tasks; fine-tuning on sampled trajectories gave up to +19% absolute resolve rate; with a trained verifier, 32.0% on SWE-bench Verified.
- **Agentic RL.** Search-R1 (Jin et al., 2025, https://arxiv.org/abs/2503.09516): +41% (Qwen2.5-7B) and +20% (3B) over retrieval baselines on 7 QA sets. Yang, He-Yueya, Liang, "Reinforcement Learning for Machine Learning Engineering Agents", 2025 (ICLR 2026), https://arxiv.org/abs/2509.01684: Qwen2.5-3B with RL beat a prompted Claude-3.5-Sonnet agent by 22% on average over 12 Kaggle tasks; needed a reliable verifier and fixes for variable action durations.
- **Data-analysis agents.** DeepAnalyze-8B (Zhang et al., 2025, https://arxiv.org/abs/2510.16872): curriculum training plus synthesised trajectories; claims to beat workflow agents on proprietary models; no numbers in abstract **[not verified]**. Edison Scientific trained a Jupyter-notebook bioinformatics agent with step-level GRPO (NVIDIA blog, 2025, https://developer.nvidia.com/blog/how-to-train-scientific-agents-with-reinforcement-learning); no results reported **[no numbers]**. A 2025 survey says RL is still "underexplored" for data-science agents (https://arxiv.org/abs/2510.04023, search snippet only **[not verified]**).
- **Process reward models.** Lightman et al., "Let's Verify Step by Step", 2023, https://arxiv.org/abs/2305.20050: process supervision solved 78% of a MATH subset, using 800,000 step labels. Choudhury, "Process Reward Models for LLM Agents", 2025, https://arxiv.org/abs/2502.10325: 3B models trained with agent PRMs beat GPT-4o on ALFWorld. Step-level labels are costly; and failure attribution in traces is weak (14.2% step accuracy, Q2).
- **Cost of RL runs.** The Training-Free GRPO paper lists ReTool and AFM (32B RL) at about $10,000 each and ZeroTIR / SimpleTIR at about $20,000 each; ReTool-style RL used about 20,000 GPU hours.

### API-only alternatives (feasible for us)
- **Training-Free GRPO** (Cai et al., 2025, https://arxiv.org/abs/2510.08191): no weight updates. It samples groups of rollouts, asks the model why some beat others, and stores the lessons as a text "experience library" added to the prompt. 100 training problems, 3 epochs, about $18. AIME24 80.0→82.7, AIME25 67.9→73.3 (with tools), WebWalkerQA 63.2→67.8 (DeepSeek-V3.1). It beat the $10,000 RL-trained 32B models on AIME.
- **ACE, Agentic Context Engineering** (Zhang et al., ICLR 2026, https://arxiv.org/abs/2510.04618): an evolving "playbook" built by small incremental edits from execution feedback. +10.6% on agent benchmarks, +8.6% on finance tasks. It targets two failure modes: brevity bias (summaries drop useful detail) and context collapse (repeated rewrites erode detail).
- **OpenAI reinforcement fine-tuning** (o4-mini only) exists, but OpenAI's docs now say the fine-tuning platform is winding down and closed to new users (https://developers.openai.com/api/docs/guides/reinforcement-fine-tuning) **[status not verified beyond search snippets]**.

### Feasibility for us
- Fine-tuning Opus: not possible.
- Small open model with RL: gains are large only on narrow tasks with a reliable automatic verifier, and need thousands of tasks and $10,000+ of compute. Our verdict label is verifiable, but 20 dev tasks are far too few to train on, and the panel must stay untouched for evaluation. **Not feasible at our budget.**
- Experience library from contrasted rollouts (Training-Free GRPO, ACE): **feasible** as one move type, at tens of dollars. Must be built from tasks *outside* the dev panel, or it memorises the panel (Lumer et al. found 39-52% of the best prompt was memorised answers).

---

## Q5. One strong agent vs a multi-agent pipeline at equal cost

- **More samples plus a verifier.** Brown et al., "Large Language Monkeys", 2024, https://arxiv.org/abs/2407.21787: SWE-bench Lite 15.9% (1 sample) → 56% (250 samples) with a verifier. Without automatic verifiers, majority voting and reward models plateau after several hundred samples.
- **Imperfect verifiers cap gains.** Stroebl, Kapoor, Narayanan, "The Limits of Inference Scaling Through Resampling", 2024, https://arxiv.org/abs/2411.17501: false positives set a ceiling; the best number of attempts is "often fewer than 10"; weak models cannot resample their way to a strong model's single-sample accuracy.
- **Adaptive compute.** Snell et al., 2024, https://arxiv.org/abs/2408.03314: compute-optimal allocation beats best-of-N by more than 4x in efficiency; a small model with test-time compute beat a 14x larger model, but only on problems where it already had some success.
- **Debate vs voting.** Choi, Zhu, Li, "Debate or Vote", NeurIPS 2025, https://arxiv.org/abs/2508.17536: across 7 benchmarks, majority voting accounts for most of the gains credited to debate; debate alone does not raise expected correctness. Smit et al., "Should we be going MAD?", 2023, https://arxiv.org/abs/2311.17371: debate does not reliably beat self-consistency or ensembling. Wang et al., "Rethinking the Bounds of LLM Reasoning", 2024, https://arxiv.org/abs/2402.18272: one agent with strong prompts reaches almost the same performance as the best discussion method; discussion helps only when there are no examples in the prompt.
- **When multi-agent helps and hurts.** Kim et al., "Towards a Science of Scaling Agent Systems", Dec 2025, https://arxiv.org/abs/2512.08296: 260 configurations, 6 benchmarks. Multi-agent +80.8% on decomposable financial reasoning, −70.0% on sequential planning. Gao et al., 2025, https://arxiv.org/abs/2505.18286: multi-agent benefits shrink as models get stronger. Cemri et al., "Why Do Multi-Agent LLM Systems Fail?", 2025, https://arxiv.org/abs/2503.13657: 14 failure modes from over 1,600 traces across 7 frameworks.
- **Industry case for multi-agent.** Anthropic, "How we built our multi-agent research system", 2025, https://www.anthropic.com/engineering/multi-agent-research-system: +90.2% over single-agent Opus 4 on an internal research eval, but about 15x the tokens of chat; token use alone explained 80% of the performance variance on BrowseComp. Poor fit when agents need the same context or tasks have many dependencies. Internal eval **[not independently verifiable]**.
- **MASS** (Q1): prompt-optimised single agents were more token-efficient than self-consistency, self-refine or debate at matched cost on MATH.

**Takeaway for a three-way verdict task.** Hypothesis validation is mostly sequential (read data, analyse, judge), the setting where Kim et al. found multi-agent hurts most. The strongest cost-matched baseline is one strong agent with a tuned prompt, plus k independent final verdicts and a majority vote, plus a narrow verifier for checkable steps. Extra roles should earn their place against that baseline.

---

## Design implications for loop 2 (ranked by expected value per dollar)

1. **Re-run only the tasks that changed (acceptance rule).** After a full-panel evaluation, re-run baseline and candidate 2 more times only on the tasks whose outcome flipped. Accept based on the majority outcome per task. This replaces most of the cost of a full second replicate and targets the noise directly (Miller's resampling result; AFlow and ADAS run 5 repeats). Cost: about 5-8 task-runs instead of 20.
2. **Build a "flaky task" list from the two identical baseline runs, and score on stable tasks.** Report net on stable tasks and flaky tasks separately; require the net ≥ 3 on the stable set, or down-weight flaky tasks. Heineman et al. show that dropping noisy items raises signal-to-noise. Cost: zero new runs.
3. **Add a continuous score next to the verdict.** Ask the final agent for a probability over True / False / Not verifiable, and track the paired Brier or log score per task as a second metric. Continuous metrics have better signal-to-noise (Heineman et al.) and probabilities remove sampling noise (Miller). Use it to break ties and to screen, not to replace the verdict rule. Cost: one prompt line.
4. **Give the proposer structured per-task failure reasons, not counts.** For each damaged or still-failing task: the expected and given verdict, the confusion type (e.g. called False when Not verifiable), and a one-line, checklist-style reason from a judge (data not found, wrong test, wrong direction, overclaim). Pair it with one trace where the same kind of task succeeded. This copies GEPA's design; the scalar-vs-decomposed ablation is the strongest evidence. Do not dump raw traces: failure attribution from long traces is 14-54% accurate. Cost: one cheap judge call per failed task.
5. **Screen moves cheaply before spending a full evaluation.** Run each proposed move first on the 4-6 tasks it is meant to fix plus 2-3 tasks most at risk of damage; spend a full-panel evaluation only if it fixes at least one targeted task and damages none. This is the DGM / AlphaEvolve cascade idea. Caution: targeted screens overstate gains, so keep full-panel acceptance. Cost: about a quarter of an evaluation per screen; it lets 8-10 full evaluations cover many more ideas.
6. **Add majority voting over final verdicts as a move.** Run the verdict step k = 3 times and vote (Debate or Vote; Wang et al.; Stroebl et al. say keep k small). It is cheap if only the final judgement is resampled, and fits the cost cap if the analysis itself is not repeated. Expected gain: removes random verdict flips, which also lowers panel noise for later moves.
7. **Order the move space: per-agent prompt sections first, structure later.** MASS found per-block prompt tuning gave about 7 points, topology about 3, and workflow tuning under 1. Prefer moves that add a rule the model cannot infer from examples (MIPRO's lesson 3), such as when to answer Not verifiable.
8. **Allow bundles of two to three related moves when the expected single effect is below what the panel can detect.** At n = 20, single moves smaller than about 4 tasks net are close to invisible **[our arithmetic]**. Accept the bundle on the panel, then ablate one part at a time only if it is kept.
9. **Keep an archive, not a single line.** Keep every evaluated variant with its per-task results, and let the proposer branch from any variant that is best on some task (GEPA Pareto: +12.44 vs +6.05 greedy; DGM: branching only from the latest stalls). Merge two variants that win on different tasks as a move type.
10. **Hold out a small check set and expect shrinkage.** Keep 8-10 tasks the loop never sees, and run the final kept configuration on them once. Expect a validation gain to shrink by around half or more (2.6x inflation in Lumer et al.; 5-20% gap in OPRO). Watch for prompt growth and any text that restates dev-panel tasks.
11. **Consider an experience library move, built from tasks outside the panel.** Contrast a correct and an incorrect rollout on non-panel tasks, and distil short lessons into a playbook section (Training-Free GRPO about $18; ACE +10.6%). Higher upside than single edits but needs separate tasks to avoid memorising the panel. Lower priority because it needs new task material.
12. **Do not plan fine-tuning or RL for this event.** The gains are real only with thousands of verifiable tasks and $10,000+ of compute (Q4).

### What we could not verify
- Promptbreeder population and generation counts; EvoPrompt average gains (secondhand).
- Numbers behind the Trace and AlphaEvolve ablation curves (figures only).
- ADAS search cost; the DGM hallucination / reward-hacking case study (appendices not read).
- DeepAnalyze benchmark numbers; Edison Scientific RL results (none published in the source we read).
- Current status of OpenAI's reinforcement fine-tuning service (search snippets only).
- Lumer et al. (2026) and Bjarnason et al. (2026) are recent single studies; their results have not been replicated elsewhere that we found.
- All "[our arithmetic]" figures depend on an assumed per-task variance; they should be recomputed from our two identical baseline runs.
