# Literature review: debate, abstention and data-analysis agents

Written 2026-11-17. Scope: published work 2023-2026 that bears on our lab design (two analysis teams, a challenge round, optional revision, a referee, and a re-check of abstentions) and its two failures: too many "Not verifiable" answers, and the challenge round turning agreed correct answers into "Not verifiable".

Sources are primary papers (arXiv, OpenReview, ACL Anthology, PMLR, PMC) unless marked. "Unverified" means I saw the claim only in an abstract, a search snippet or a secondary summary, not in the paper body. Where a number is my own calculation from a paper's table, I say so.

Rule followed: for BioDSA-1K I read only the method and results sections. I did not open its dataset, data folders, dataset viewer or any appendix listing hypotheses.

## Summary

- **Debate on its own does not make answers more correct.** At equal agents, majority vote over independent answers explains most of the gain credited to debate, and debate keeps the expected belief in the right answer flat (Choi et al., NeurIPS 2025). Several audits find debate no better than self-consistency or chain-of-thought at higher cost (Smit et al., ICML 2024; Zhang et al., 2025; Wang et al., ACL 2024). Our result, a tie with one strong model at 3-11x the cost, is the usual finding.
- **Challenge rounds damage correct answers through conformity.** When agents change answers, correct-to-wrong changes outnumber wrong-to-correct changes in several studies. Telling agents they are rewarded for being correct did not stop it (Wynn et al., 2025). Being challenged with "are you sure?" alone flips about 46% of answers and costs about 17% accuracy (Laban et al., 2023).
- **Designs that keep the gains share one trait: answers change only on checkable evidence, and the final decision does not depend on reaching consensus.** Examples: verified quotes for a judge (Khan et al., ICML 2024), anti-conformity prompts plus scoring over the whole debate rather than the last round (Free-MAD, 2025), a judge-only referee (Yao et al., 2025), and mixed model families (Zhang et al., 2025; ReConcile, ACL 2024).
- **BioDSA-1K defines "Not verifiable" narrowly.** A hypothesis is Not verifiable only when "no relevant features or data tables exist in the dataset". False needs relevant variables plus contradicting evidence. Weak or null evidence on variables that exist does not fit that definition. Our lab is right on 1 of 9-10 abstentions, which suggests it uses a much wider rule than the benchmark.
- **Agents in BioDSA-1K lean conservative.** They miss true findings (Type II) more often than they accept false ones (Type I) in nearly every setting. When their code fails, CodeGen agents answer Not verifiable about 87% of the time. So a failed or thin analysis is a known route into Not verifiable.
- **Abstention moves a lot with wording.** An "abstain if unsure" clause or chain-of-thought can cut accuracy on answerable questions by 2-21 points (Abstain-QA, COLING 2025). Offering an "insufficient information" option pushed BixBench agents below random, and voting over more runs made it worse. Stating an explicit scoring rule ("answer only if more than t confident; wrong answers cost t/(1-t)") is the cleanest published way to set the trade-off (Kalai et al., 2025).
- **Self-review without new information rarely changes a decision.** Intrinsic self-correction does not reliably fix reasoning (Huang et al., ICLR 2024). This matches our re-check, which never changed an abstention.
- **Fair comparison needs the same task definition in every arm.** Prompt wording alone can move accuracy by about 10 points on average and up to 76 (Sclar et al., ICLR 2024). A single agent with a strong prompt nearly matches the best multi-agent discussion (Wang et al., ACL 2024). Giving every arm the same label convention is standard practice, not an unfair boost to one arm.

## 1. Multi-agent debate and challenge: when it helps and when it hurts

### 1.1 Debate is mostly a vote

**Choi, Zhu, Li. "Debate or Vote: Which Yields Better Decisions in Multi-Agent Large Language Models?" NeurIPS 2025.** https://arxiv.org/abs/2508.17536
- Seven benchmarks, 5 agents. Average accuracy for Qwen2.5-7B: single agent 0.7205, majority vote 0.7691, best debate variant 0.7377. For Llama3.1-8B: single 0.6203, majority vote 0.7242, best debate 0.6990.
- Some debate variants fall below a single agent. Centralized debate with Qwen-7B averaged 0.6551.
- Theory: debate is a martingale over each agent's belief in the right answer. The expected belief stays the same across rounds. Their plots of mean agent accuracy over rounds are flat.
- Fix: bias updates toward likely-correct signals. Over three settings of rounds, "MAD-Conformist" and "MAD-Follower" lift the debate average by 3 to 5 points. Example at 2 rounds: 0.7332 rises to 0.7625 and 0.7629. An oracle version that uses the true answer reaches 0.82-0.83, so there is room left, but only with a correctness signal.
- Debate beat voting in some places, such as MMLU professional medicine with personas (0.8419 vs 0.8235).
- Not reported: counts of answer flips; any measure of confidence.

**Smit et al. "Should we be going MAD? A Look at Multi-Agent Debate Strategies for LLMs." ICML 2024.** https://arxiv.org/abs/2311.17371
- Medprompt, which is not a debate method, scored best overall and cost less (MedQA 0.65, PubMedQA 0.77). Self-consistency was best on MMLU (0.78). A single agent was best on GPQA (0.33).
- No debate protocol won across datasets. Debate used more API calls without reliable gains.
- Telling agents how often to agree ("agree X% of the time") changed results a lot. Multi-Persona improved about 15% on a 376-question USMLE subset. High agreement helped on MedQA and PubMedQA; disagreement helped on CIAR. So agreeableness is a setting to tune per task.
- Multi-Persona includes a "devil" agent meant to disagree. It ended below the first agent's own answer, because it argued against answers that were already correct. This is the published version of our challenge damage.

**Zhang et al. "Stop Overvaluing Multi-Agent Debate: We Must Rethink Evaluation and Embrace Model Heterogeneity." 2025 (position paper).** https://arxiv.org/abs/2502.08788
- Five debate methods (Society of Minds, Multi-Persona, Exchange-of-Thoughts, ChatEval, AgentVerse), 9 benchmarks, 4 models. Debate often fails to beat chain-of-thought or self-consistency despite using more compute.
- Example from the paper (via a secondary summary, alphaXiv): MMLU with GPT-4o-mini, chain-of-thought 80.73 vs Society of Minds 74.73.
- Mixing models (each agent drawn at random from GPT-4o-mini or Llama3.1-70B) helped: Heter-SoM averaged 76.7 against 72.1 for same-model SoM and 72.5 for chain-of-thought.
- Unverified: I could not read the PDF tables directly. The numbers above come from an alphaXiv summary of the paper.

**Wang et al. "Rethinking the Bounds of LLM Reasoning: Are Multi-Agent Discussions the Key?" ACL 2024.** https://aclanthology.org/2024.acl-long.331
- With worked examples in the prompt, a single agent scored 75.63 on average against 74.46 for the best discussion method (ChatGPT-3.5).
- Without examples, discussion helped by 3-4 points (70.75 or 71.67 against 67.41).
- They name two discussion errors: judge mistakes, and "wrong answer propagation", where agents give up a correct answer for a wrong consensus. They call the second the most common. No rates given.

**Huang et al. "Large Language Models Cannot Self-Correct Reasoning Yet." ICLR 2024.** https://arxiv.org/abs/2310.01798
- Without outside feedback, asking a model to review its own answer does not reliably improve reasoning and can lower accuracy. The authors say to compare self-correction against baselines with the same inference cost.

### 1.2 Evidence that challenge rounds damage correct answers

**Wynn, Satija, Hadfield. "Talk Isn't Always Cheap: Understanding Failure Modes in Multi-Agent Debate." ICML 2025 workshop on multi-agent systems.** https://arxiv.org/abs/2509.05396
- Three models (GPT-4o-mini, Llama-3.1-8B, Mistral-7B), three tasks, 2 rounds, majority vote before and after debate.
- Every group lost accuracy on CommonsenseQA after debate. The largest drop was 12 points on MMLU (one Llama with two Mistral: 40.0 to 28.0). Three GPT-4o-mini agents changed by less than one point.
- A strong agent in the group did not prevent losses. Two GPT agents plus one Mistral still lost 2.2 points on CommonsenseQA and 2.0 on MMLU.
- Among agents that changed their answer, correct-to-wrong changes outnumbered wrong-to-correct changes. The share of correct-to-wrong changes grew in later rounds. Flips to wrong were most likely when no peer agreed with the agent.
- Telling agents they would be paid for correct answers did not reduce correct-to-wrong flips; in many cases they rose.
- Flip percentages are shown only in figures, not stated in the text.

**Laban et al. "Are You Sure? Challenging LLMs Leads to Performance Drops in the FlipFlop Experiment." 2023.** https://arxiv.org/abs/2311.08596
- Ten models, seven classification tasks. After a simple challenge ("are you sure?"), models changed answers about 46% of the time and accuracy fell about 17% on average. The drop ranged from about 8% (GPT-4) to about 34% (Claude V2).
- Fine-tuning on synthetic challenge conversations cut the drop by about 60% but did not remove it.

**Bertalanič, Fortuna. "The Cost of Consensus: Isolated Self-Correction Prevails Over Unguided Homogeneous Multi-Agent Debate." 2026.** https://arxiv.org/abs/2605.00914
- Ten same-model agents, 3 rounds, three 7-8B models, GSM-Hard and an MMLU-Hard subset.
- Isolated self-correction matched or beat debate on all six model-dataset pairs, with 2.1x to 3.4x fewer tokens.
- Share of agents that dropped a correct answer after seeing peers: 6.9% to 70.0% depending on model and task.
- Agents adopted the majority peer answer 26-86% of the time.
- Debate shrank the confidence gap between right and wrong answers, so confidence was a weak tie-breaker.
- Small models only; may not carry over to frontier models.

**Hao et al. "Not All Flips Are Conformity: Decomposing Stance Convergence in Multi-Agent LLM Debate." 2026.** https://arxiv.org/abs/2606.00820
- Five GPT-4o agents. With no peer input at all, 37% of agent-question pairs changed answer on MMLU-Pro (39% on GPQA-Diamond). So raw flip counts overstate peer influence.
- Pure conformity (seeing only peers' answers) moved 29.3% of otherwise stable pairs. Those moves split roughly evenly: 30.7% correct-to-wrong, 36.4% wrong-to-correct, 32.9% wrong-to-wrong.
- Majority-vote accuracy was 76.2% before and 77.4% after, with or without peer reasoning; full peer reasoning cost 57% more tokens.
- A deployable rule that suppressed peer adoption without a correctness signal cut harmful and helpful changes equally and did not change accuracy (-0.15 points). A rule targeted using true answers worked, but it is not deployable.
- Their advice: convergence is not evidence of improvement; always compare against a matched no-peer self-review baseline.

**Yao et al. "Peacemaker or Troublemaker: How Sycophancy Shapes Multi-Agent Debate." 2025.** https://arxiv.org/abs/2509.23055
- Qwen3-32B and Llama-3.3-70B on MMLU-Pro and CommonsenseQA.
- Without a judge, two-agent debate often fell below the best single agent (CommonsenseQA: 81.0-83.7 vs 85.5). How often agents gave up their disagreement ("disagreement collapse") reached 86%.
- Adding a separate judge cut collapse roughly in half (Qwen-Qwen: 81.7% to 41.3%) and raised accuracy above the single agent (86.65 vs 85.50).
- Agents' sycophancy scores tracked their tendency to switch (r = 0.90).
- Best mixes paired an agreeable agent with an independent one. Judge sycophancy level barely mattered.

**Wu, Li, Li. "Can LLM Agents Really Debate? A Controlled Study of Multi-Agent Debate in Logical Reasoning." 2025.** https://arxiv.org/abs/2511.07784
- Logic puzzles with known answers. Agents in a correct majority stayed correct over 97% of the time. Agents in a wrong majority corrected themselves only 3.6-34.4% of the time. Majority pressure suppresses independent correction.
- Showing confidence and adding more rounds had no significant effect. Stronger base models, larger teams and diverse reasoning mattered most.

**Cemri et al. "Why Do Multi-Agent LLM Systems Fail?" (MAST) NeurIPS 2025.** https://arxiv.org/abs/2503.13657
- 1,642 annotated traces from 7 frameworks. Failure shares: specification issues 41.8%, misalignment between agents 36.9%, verification 21.3%. Verification that is missing or wrong is 13.5% of failures.
- Clearer role specification raised success by 9.4% in one case study; adding a high-level verification step raised it by 15.6 points absolute.

### 1.3 Designs that keep the gains and avoid the damage

| Design | Evidence | Effect |
|---|---|---|
| Answer changes only with checkable evidence | Khan et al., ICML 2024 (verified quotes) | Judge accuracy 76% (LLM) and 88% (human) vs 48% / 60% with no debate. Removing quote checking lowered accuracy because the side arguing the wrong answer could lie more easily. |
| Anti-conformity prompt plus scoring over the whole debate | Free-MAD, 2025 | One round: 64.4% vs 54-56% for standard debate. Robust when half the agents were compromised. Small samples (80 questions per benchmark). |
| Separate judge that does not debate | Yao et al., 2025 | Halved disagreement collapse and beat the single agent. |
| Decide by vote over independent first answers | Choi et al., 2025; Hao et al., 2026 | Vote explains most of the gain; peer reasoning added about 1 point at 57% more tokens. |
| Mixed model families | Zhang et al., 2025; ReConcile, ACL 2024; Wynn et al., 2025 | Heter-SoM +4.6 points over same-model SoM. ReConcile (ChatGPT, Bard, Claude 2, confidence-weighted vote) beat earlier single and multi-agent baselines by up to 11.4%. Mixing weak models in hurts. |
| Bias updates toward the majority | Choi et al., 2025 | +3 to +5 points over plain debate. |
| Fewer rounds | Yao et al., 2025; Wynn et al., 2025 | Sycophancy and correct-to-wrong flips grow in later rounds. |

What does not work: telling agents they are rewarded for correctness (Wynn et al.); showing confidence scores (Wu et al.); blanket "don't change your answer" rules without a correctness signal (Hao et al.); higher sampling temperature (Bertalanič and Fortuna).

**Khan et al. "Debating with More Persuasive LLMs Leads to More Truthful Answers." ICML 2024.** https://arxiv.org/abs/2402.06782
- Two expert debaters see the full text; the judge does not. Quotes are checked by a tool and marked verified or unverified, and judges are told to trust only verified quotes. Arguments are length-capped; each debate is judged twice with the order swapped.
- Debate beat consultancy, where one adviser argues one side (LLM judge 76% vs 54%; human 88% vs 78%).
- Caveat: models assigned the wrong side often conceded, which flatters debate accuracy.

**Liang et al. ReConcile.** https://arxiv.org/abs/2309.13007 (ACL 2024). The gains depend on model diversity, per their ablations. The headline gain is 7.7% in v1 and 11.4% in v3; check the version before quoting.

## 2. Over-abstention and calibrated abstention

### 2.1 What the evidence says about why models over-abstain

The literature mostly reports the opposite problem: models answer when they should abstain. Over-abstention appears in specific conditions, and those conditions match our setup.

1. **An easy exit option plus instructions that favour caution.** In Abstain-QA (Madhusudhan et al., COLING 2025, https://arxiv.org/abs/2407.16221), 2,900 multiple-choice questions, half answerable. Adding an "abstain if unsure" clause lowered accuracy on answerable MMLU items by 2.0 (GPT-4 Turbo), 3.8 (GPT-3.5 Turbo) and 5.6 (Mixtral 8x7B) points. With chain-of-thought plus the clause, answerable accuracy fell 11.8 (Mixtral 8x7B) and 20.8 (GPT-3.5 Turbo) points. In an unfamiliar domain (Carnatic music), chain-of-thought alone raised GPT-4 Turbo's abstention rate from 20.3% to 53.5% and cut answerable accuracy from 48.0 to 40.2. (Differences are my calculations from their Table 1.)
2. **Hard, unfamiliar analysis tasks.** BixBench (Mitchener et al., 2025, https://arxiv.org/abs/2503.00096), about 300 questions over 50+ bioinformatics scenarios. With an "Insufficient information" option, GPT-4o and Claude 3.5 Sonnet scored worse than random. Without it, they scored slightly above random. Voting over 10 runs made the refusal arm slightly worse, because refusals piled up. Exact per-model numbers are only in figures; secondary sources give about 20-25% with refusal and 30-35% without (unverified).
3. **Failed or thin analysis.** In BioDSA-1K, when CodeGen's code failed, agents answered Not verifiable about 87% of the time (see section 3.1).
4. **A three-way label with a "not enough info" class.** "(Fact) Check Your Bias" (Bakke and Heggelund, 2025, https://arxiv.org/abs/2506.21745): Llama 3.1, asked directly to verify claims, labeled nearly half "Not Enough Evidence", yet could reach a verdict on the rest from its own knowledge. Different prompting strategies did not change final verdicts.
5. **Training rewards.** Reward schemes with weak penalties for false refusals make models overly cautious; schemes with zero cost for wrong answers make them guess (Abstain-R1, 2026, https://arxiv.org/abs/2604.17073; TruthRL, 2025, https://arxiv.org/abs/2509.25760; Reinforced Hesitation, 2025, https://arxiv.org/abs/2511.11500). Unverified beyond abstracts.
6. **Challenge treated as a signal to abstain.** "Don't Hallucinate, Abstain" (Feng et al., ACL 2024 outstanding paper, https://arxiv.org/abs/2402.00367) built an abstention method, COMPETE, that shows a model other models' conflicting answers and abstains if the model changes its answer in most cases. This raised reliability (ChatGPT on MMLU: accuracy among answered items 0.947) but abstained more, and its abstain accuracy (0.602) fell below the simple instruction baseline (0.748). Our challenge round does the same thing by accident: disagreement becomes abstention.

### 2.2 Context from AbstentionBench

**Kirichenko et al. "AbstentionBench: Reasoning LLMs Fail on Unanswerable Questions." NeurIPS 2025 Datasets and Benchmarks.** https://arxiv.org/abs/2506.09038
- 20 models, 20 datasets, over 35,000 unanswerable questions.
- Reasoning fine-tuning lowered abstention by 24% on average, even in maths and science. More reasoning budget did not help abstention.
- Model scale had almost no effect.
- A system prompt describing when not to answer raised abstention without a significant drop in precision. So stating the rule helps, in both directions.
- Their main failure is under-abstention; precision of abstentions was generally high. Our lab shows the reverse, which points to our prompts and procedure rather than a model trait.
- Underspecified context was the hardest scenario.

**Wen et al. "Know Your Limits: A Survey of Abstention in Large Language Models." TACL 2025.** https://arxiv.org/abs/2407.18418 Useful for terms and metrics. It frames abstention by the query, the model and human values, and recommends reporting both sides of the trade-off.

### 2.3 Fixes that do not wreck true abstentions

- **State the scoring rule.** Kalai et al., "Why Language Models Hallucinate", 2025 (https://arxiv.org/abs/2509.04664), propose adding "Answer only if you are >t confident" to the instruction, with +1 for correct, 0 for abstaining, and -t/(1-t) for wrong (t = 0.5 means a wrong answer costs 1; t = 0.75 costs 2). They argue binary grading makes abstaining always sub-optimal, so the threshold must be explicit to be fair. Corollary for us: if the rule an agent assumes makes "Not verifiable" cost nothing while a wrong True/False feels costly, agents will over-abstain. The real grading gives no credit for a wrong Not verifiable, so the prompt should say that.
- **Define the abstention class narrowly and concretely.** AbstentionBench's system prompt, which spells out when not to answer, helped without hurting precision. BioDSA-1K gives a concrete definition (section 3.1).
- **Measure both sides.** Report accuracy on committed answers and on abstentions separately, as Abstain-QA does with its confusion matrix. VisionTrap (2025, https://arxiv.org/abs/2507.17262) is a warning: one model marked almost everything unanswerable, with a 0.972 false-negative rate, which a recall-only metric hid.
- **Require the abstention to be checkable.** POPPER (section 3.4) uses a relevance checker to stop irrelevant tests from supporting a hypothesis. The same idea applies in reverse: an abstention should name the missing variable, and a check should confirm it is missing.
- **Do not count on self-review.** Liu et al., AAAI 2026 (https://arxiv.org/abs/2508.18760), report that reasoning models often notice a problem during reasoning but do not act on it in the final answer (unverified beyond abstract). Huang et al. (ICLR 2024) show self-review without new information rarely changes decisions. Our re-check matches this.

## 3. Data-science and biomedical analysis agents

### 3.1 BioDSA-1K (method and results sections only)

**Wang, Danek, Sun. "BioDSA-1K: Benchmarking Data Science Agents for Biomedical Research." 2025.** https://arxiv.org/abs/2505.16100
- 1,029 hypothesis tasks with 1,177 analysis plans from over 300 published studies. Agents see a hypothesis and dataset descriptions, write and run code, and label the hypothesis True, False or Not verifiable. Scored on decision accuracy, evidence alignment, reasoning and code that runs.
- **Label definitions (Section 2.4).** Not verifiable: "no relevant features or data tables exist in the dataset to support or reject the claim." False: relevant variables exist and the evidence contradicts the hypothesis. The paper does not give the prompt wording in the main body.
- **Errors.** Type I = share of false hypotheses labeled True. Type II = share of true hypotheses labeled False. The paper says agents "tend to be conservative": Type II is higher than Type I in nearly every setting. Unweighted means over the seven publication types in Table 2 (my calculation): CodeGen gpt-4o 0.097 / 0.156; CodeGen o3-mini 0.123 / 0.163; ReAct gpt-4o 0.093 / 0.152; CodeGen-Reasoning 0.088 / 0.139; ReAct-Reasoning 0.076 / 0.117. ReAct-Reasoning is best on both.
- **Not verifiable handling (Section 3.4).** On 100 hypotheses built to be unverifiable, the share correctly labeled Not verifiable was 63% (CodeGen gpt-4o), 57% (CodeGen o3-mini) and 92% (ReAct-Reasoning). The others were described as "more conservative" without numbers.
- **Failed code.** When CodeGen code did not run, agents answered Not verifiable about 87% of the time, False about 8% and True about 5%. The authors call the last two hallucinated findings.
- **Code that runs.** ReAct-Reasoning 86.6%, ReAct 84.9%, CodeGen 76.9%, CodeGen-Reasoning 58.2%. Most common code error: misusing variables or objects (27.7-32.1%).
- **Evidence alignment** was modest, about 0.20-0.25 for all methods, and lower for clustering and survival analyses.
- **Not reported in the main body:** overall label distribution, per-label accuracy on the main set, and how often true hypotheses went to Not verifiable versus False. Cost is not reported.
- **What this means for us.** The best published design is a single iterative agent (ReAct with a reasoning model). Iterating to working code is the main lever. The paper's Not verifiable definition is about missing variables, not weak results.

### 3.2 Other benchmarks

**BixBench (Mitchener et al., FutureHouse, 2025).** https://arxiv.org/abs/2503.00096 Open-answer accuracy: Claude 3.5 Sonnet 17%, GPT-4o 9%. Multiple-choice was no better than random and fell below random when refusal was allowed (section 2.1). Telling agents not to make images improved results. Models read plots poorly. Reasoning models (o1, DeepSeek-R1) struggled with long contexts and structured tool output. A later curated subset, BixBench-Verified-50, removed ambiguous questions; scores on it are not comparable to the original (secondary source, unverified).

**DSBench (Jing et al., 2024; ICLR 2025).** https://arxiv.org/abs/2409.07703 466 analysis tasks and 74 modeling tasks. Best analysis accuracy 34.12% (AutoGen with GPT-4o); humans 64.06%. Best modeling relative performance gap 34.74%. Failure modes: misreading what a field means, failing to find needed data and then reporting the data is missing, and wrong method. The second one is a direct route to false "Not verifiable" answers.

**DiscoveryBench (Majumder et al., ICLR 2025).** https://arxiv.org/abs/2407.01725 264 real and 903 synthetic tasks. On real tasks, CodeGen, ReAct and a multi-component agent (DataVoyager, with planner and critic) all scored about 14-16% with GPT-4o. The extra components did not beat simple CodeGen. Only Reflexion with oracle feedback (the true score) reached 24.5%. Biology tasks scored 0%. Adding domain hints lowered one archaeology setting from 17.5% to 9.9%.

**ScienceAgentBench (Chen et al., ICLR 2025).** https://arxiv.org/abs/2410.05080 102 tasks. Best agent 32.4% without expert knowledge, 34.3% with it (Claude 3.5 Sonnet with self-debug). Simple self-debug beat OpenHands CodeAct by 10.8 points at 17x lower cost. o1-preview reached 42.2% at over 10x the cost.

**BLADE (Gu et al., EMNLP 2024 Findings).** https://arxiv.org/abs/2408.09667 Agents produced runnable but basic analyses with little variety in analysis choices. Agents that interacted with the data did better.

**Biomni (Huang et al., 2025 preprint; reported later in Science, unverified).** https://pmc.ncbi.nlm.nih.gov/articles/PMC12157518/ One agent that plans, retrieves tools and runs code (CodeAct style) over 150 tools, 105 packages and 59 databases. LAB-Bench DbQA 74.4% (human expert 74.7%, coding agent 40.8%); SeqQA 81.9% (human 78.8%); a 52-question biomedical subset of Humanity's Last Exam 17.3% (base model 6.0%, coding agent 12.8%). The gain comes from the environment and tool retrieval, not from more agents.

### 3.3 Pattern across benchmarks

- The best designs are single agents that write code, run it, read the output and fix errors (ReAct, self-debug, CodeAct). Planner and critic add-ons (DataVoyager) and full software-agent frameworks (OpenHands) did not beat them.
- Code that runs is the main driver. Failed code leads to abstention (BioDSA-1K) or to invented findings.
- Common failures: misreading variables, not finding data that is there, wrong statistics, misreading p-values, and over-cautious refusals.

### 3.4 Hypothesis validation with error control

**Huang et al. "Automated Hypothesis Validation with Agentic Sequential Falsifications" (POPPER). ICML 2025.** https://arxiv.org/abs/2502.09858
- An agent designs falsification tests, runs them, and combines the results with e-values. Type I error stayed below the 0.1 target on all three benchmarks; most baselines did not control it.
- About 66.5% more power than ReAct on DiscoveryBench.
- Removing the relevance checker raised Type I error, because irrelevant tests could falsely support a hypothesis.
- On one biology benchmark, results matched nine computational biologists, about 9.7x faster.
- Failure causes: misreading p-values 35.9%, poor test design 28.1%, tests that do not bear on the hypothesis 17.2%, wrong implementation 8.6%, hallucination 0.8%.

## 4. Same task-definition clarification for all arms

No paper tests "give every arm the same label convention" as its own question. Several results support it as the fair and standard choice:

- **Prompt wording moves results a lot.** Sclar et al., ICLR 2024 (https://arxiv.org/abs/2310.11324): formatting changes alone moved accuracy about 10 points on average across 50+ tasks, and up to 76 points (Llama-2-13B). So any wording given to one arm and not the other confounds the comparison.
- **Single agents catch up when given the same strong prompt.** Wang et al., ACL 2024: with worked examples, a single agent matched or beat multi-agent discussion. Without them, discussion led by 3-4 points. Discussion partly makes up for a weak prompt.
- **Equal-cost, equal-information baselines are the published standard.** Huang et al. (ICLR 2024) ask for baselines with the same inference cost. Choi et al. (2025) compare at equal agent counts. Hao et al. (2026) ask for a matched no-peer baseline. Zhang et al. (2025) argue that weak baselines make debate look better than it is.
- **Unstated requirements cause unstable results.** Yang et al., "What Prompts Don't Say", 2025 (https://arxiv.org/abs/2505.13360): models guess unstated requirements correctly only part of the time (41.1% default rate in the abstract), and underspecified prompts are about twice as likely to regress when models or prompts change. Writing every requirement in does not fully fix it.
- **Benchmarks themselves state the convention.** BioDSA-1K writes its label definitions in its method section. BixBench later removed ambiguous questions (Verified-50). The Agentic Benchmark Checklist (Zhu et al., 2025, https://arxiv.org/abs/2507.02825) found task-validity problems in 7 of 10 popular agent benchmarks and up to 100% relative distortion in measured performance. Clarifying the task definition counts as restoring task validity, not tuning to the test.
- **Definitions in prompts help some models.** One 2024 study (https://arxiv.org/abs/2406.11980) found adding label definitions helped ChatGPT but lowered compliance for PaLM2 and Falcon-7B. Mu et al. (LREC-COLING 2024) found prompt strategy moved accuracy and F1 by over 10%. Unverified beyond abstracts.

Practical rule from these: give every arm (single model, lab, any baseline) the exact same label text, drawn from the paper's public method section. Fix it before any run on the scored panel, and record that it was fixed. Do not derive it from benchmark items.

## Design implications, ranked by expected effect

Ranked by how much each should reduce our two failures. Effect sizes are my judgment from the evidence above, not measured on our system.

### Over-abstention (1 of 9-10 abstentions correct)

1. **Put BioDSA-1K's own Not verifiable definition in every arm's prompt, word for word.** Not verifiable means the needed variables or tables are absent. If they exist, the answer must be True or False, even when the result is weak or null. Evidence: BioDSA-1K Section 2.4 definition; AbstentionBench (stating when to abstain helps without precision loss); Sclar et al. (wording effects). Whether a null result on existing variables should be False is my reading of "contradicting evidence"; check it against the paper's text before fixing the wording. Expected effect: large. Our abstentions are almost all wrong, which fits a rule that is too wide.
2. **Make abstention a checkable claim with a hard gate.** An abstaining team must name the missing variable(s). A deterministic check, not another model pass, looks for matching columns or tables. If relevant columns exist, Not verifiable is not allowed and the team must commit. Evidence: POPPER relevance checker; Khan et al. verified quotes; DSBench (agents say data is missing when it is there); Huang et al. (self-review alone does not change decisions), which explains why our show-the-unused-columns re-check did nothing. Expected effect: large.
3. **Treat failed or thin analysis as "retry", not as Not verifiable.** If code fails or finds nothing, the team must fix it and run again before abstaining. Evidence: BioDSA-1K (87% of failed-code runs became Not verifiable; ReAct-Reasoning, which iterates, is best); ScienceAgentBench (self-debug is the strongest simple design). Expected effect: medium to large.
4. **State the scoring rule.** Tell agents that a wrong Not verifiable scores the same as a wrong True/False. Evidence: Kalai et al. (state the threshold and penalty); Abstain-QA and BixBench (caution clauses and refusal options cause over-abstention). Expected effect: medium.
5. **Do not turn disagreement into abstention.** If the teams disagree, the referee must pick True or False unless rule 1 is met. Evidence: COMPETE (Feng et al.) shows that abstaining when the answer changes under challenge raises abstention and lowers abstain accuracy. Expected effect: medium; overlaps with the challenge fixes below.

### Challenge damage (agreed correct answers turned into Not verifiable)

1. **Lock unanimous independent answers.** If both teams independently reach the same True/False, the challenge round cannot change it to Not verifiable. Allow a change only if a new, executed analysis result contradicts it. Evidence: Choi et al. (independent vote explains the gains); Wynn et al. (correct-to-wrong flips outnumber the reverse); Hao et al. (convergence is not evidence; peer reasoning added about 1 point). Expected effect: large on this failure, close to zero risk because both teams already agreed.
2. **Answer changes need new verified evidence.** In the challenge and revision steps, a team may change its label only by citing an output from code it ran, checked by the system. Arguments alone do not count. Evidence: Khan et al. (verified quotes); Free-MAD (change only when your answer is clearly wrong); Choi et al. (bias updates toward correctness signals). Expected effect: large.
3. **Decide from the first independent answers plus evidence, not from the last round.** Keep the referee judge-only, and have it score the whole record rather than the final positions. Evidence: Free-MAD (whole-debate scoring beat standard debate by about 8-10 points in one round); Yao et al. (a separate judge halved collapse). Expected effect: medium.
4. **One challenge round at most, and consider dropping it.** Evidence: Wynn et al. and Yao et al. (damage grows in later rounds); Bertalanič and Fortuna (self-correction beat debate at 2-3x fewer tokens); Smit et al. and Zhang et al. (debate rarely beats self-consistency at equal cost). Test a cheaper arm: two or three independent analyses plus a vote, with the abstention gate above. Given our tie at 3-11x the cost, this is likely the best cost-adjusted design. Expected effect: medium on accuracy, large on cost.
5. **Use different model families for the two teams if possible, and never pair a strong team with a weak one.** Evidence: Zhang et al. (Heter-MAD); ReConcile; Wynn et al. (weak agents drag strong ones down). Expected effect: small to medium.
6. **Do not rely on "be confident" or reward prompts.** Telling agents they are paid for correct answers made flips worse (Wynn et al.). Showing confidence had no effect (Wu et al.). Confidence became less informative after debate (Bertalanič and Fortuna).

### Evaluation

- Give every arm the same label-definition text (section 4). Report committed accuracy and abstention accuracy separately, plus cost per task. Add a no-peer self-review baseline at equal cost, as Hao et al. recommend.

## Could not verify

- Zhang et al. 2025, exact win rates and Heter-MAD numbers: read through an alphaXiv summary, not the PDF tables.
- BixBench per-model multiple-choice numbers with and without refusal: in figures only; the 20-25% vs 30-35% figures are from a secondary summary.
- Liu et al. AAAI 2026, Abstain-R1, TruthRL, Reinforced Hesitation, arXiv 2406.11980, Mu et al. 2024: abstracts or snippets only.
- Biomni's later Science publication and its 57% Biomni-Eval1 figure: secondary sources only. The numbers quoted above are from the PMC preprint.
- ReConcile: headline gain differs between arXiv versions (7.7% vs 11.4%).
- Free-MAD: the paper's headline 13.0% and 16.5% average gains do not match my calculation from its Table 4 (about 12.3-12.7%).
- BioDSA-1K label distribution and per-label accuracy on the main set: not in the main body. I did not look elsewhere, by rule.
