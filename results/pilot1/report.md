# Report for pilot1

## gb01: unresolved

No two teams agreed and the referee picked neither.
Reported answer: from gb01-referee (referee round).
Grade: failed (score 0.25).

Answers given:
- gb01-team-a, analysis round: therapy_class_code = 1, benefit_rd_pp = 22.51, toxicity_dropout_risk_pp = 30.92, net_clinical_utility_pp = 11.68
- gb01-team-b, analysis round: therapy_class_code = 1, benefit_rd_pp = 29.17, toxicity_dropout_risk_pp = 34.17, net_clinical_utility_pp = 17.22
- gb01-team-b, revise round: therapy_class_code = 1, benefit_rd_pp = 29.17, toxicity_dropout_risk_pp = 34.17, net_clinical_utility_pp = 17.22
- gb01-team-a, revise round: therapy_class_code = 1, benefit_rd_pp = 22.22, toxicity_dropout_risk_pp = 32.88, net_clinical_utility_pp = 10.71
- gb01-referee, referee round: therapy_class_code = 1, benefit_rd_pp = 41.4, toxicity_dropout_risk_pp = 33.51, net_clinical_utility_pp = 29.67
- gb01-team-c, third-team round: therapy_class_code = 1, benefit_rd_pp = 30.31, toxicity_dropout_risk_pp = 31.83, net_clinical_utility_pp = 19.17

Comparisons:
- analysis: gb01-team-a and gb01-team-b differ on benefit_rd_pp, net_clinical_utility_pp, toxicity_dropout_risk_pp
- revise: gb01-team-a and gb01-team-b differ on benefit_rd_pp, net_clinical_utility_pp
- third-team: gb01-team-c and gb01-team-a differ on benefit_rd_pp, net_clinical_utility_pp
- third-team: gb01-team-c and gb01-team-b differ on net_clinical_utility_pp, toxicity_dropout_risk_pp

The director passed 10 item(s) from the library to the teams.

Questions the director wrote:
- to gb01-team-a, delivered: QUESTION FROM THE DIRECTOR: the other team compared the toxicity rate of low-confidence DLR1 calls with that of high-confidence calls at the same activity-score value and found them to behave very differently, which made them doubt both the recorded score of those calls and the functional form of the activity-score term. Check whether that holds in your data: tabulate toxicity by activity-score level, split by call confidence. Does it change how the activity score and the low-confidence calls should enter your primary toxicity model, as opposed to a sensitivity analysis?
- to gb01-team-a, delivered: QUESTION FROM THE DIRECTOR: the other team found a small effective sample size in the non-TXR1 systemic arm and a large maximum inverse-probability weight within the target population, and treated positivity as a real problem. You recorded positivity as not found, yet also recorded residual imbalance for one site and one period after weighting. Check the effective sample size per arm, the largest weights and which patients carry them in your target set. Does it change your method or how much you trust the benefit estimate?
- to gb01-team-a, delivered: QUESTION FROM THE DIRECTOR: the other team's target population has a slightly different number of cases from yours. They placed the ASE, phase-consistency, mapping-quality and breakpoint-homology cutoffs at different values, chosen from visible gaps in the distributions, used a fixed minimum split-molecule count rather than a depth-scaled one, and did not use read N50. They also estimated the treatment effect among the P2T cases their rule excluded, as a check that the rule separates true targets from the rest. List the cases that sit between plausible cutoffs in your data, check where the gaps in each distribution actually fall, and check the effect in the cases your rule excludes. Does it change your rule?
- to gb01-team-a, delivered: QUESTION FROM THE DIRECTOR: the other team describes the toxicity-stop variable as entering only the model for whether week 16 was assessed, not the outcome regression, and noted that the non-TXR1 systemic arm has very few toxicity-stop cases. Your outcome model conditions on toxicity stop. Check how many assessed patients each arm-by-toxicity-stop cell of your outcome model rests on, and whether your estimate still targets the marginal all-assessed effect once that post-treatment variable is in the outcome regression (is it averaged over its distribution under each arm?). Does it change your method?
- to gb01-team-b, delivered: QUESTION FROM THE DIRECTOR: the other team put the 8-week toxicity-stop variable in both the censoring model and the outcome regression of the sequential estimator, and then standardised over it, because among TXR1i patients who stopped for toxicity very few have an assessed week-16 outcome and the censoring weights for them are extreme, so the result for that group leans on the outcome model. Your notes say toxicity stop enters only the censoring model. Check in your data how many assessed patients there are in each arm-by-toxicity-stop cell, how extreme the censoring weights are, and whether your outcome regressions are consistent with the missingness assumption you make. Does it change your method?
- to gb01-team-b, delivered: QUESTION FROM THE DIRECTOR: the other team's target population has a slightly different number of cases from yours. Their artifact cluster also used read N50 alongside segmental duplication, mapping quality, low complexity and homology, with somewhat different cutoffs; they required split-molecule support scaled to sequencing depth rather than a fixed count; and they placed the ASE and phase-consistency cutoffs at different values. They also saw that the estimate moves with the strength of ASE evidence inside the target set. Check whether read N50 and depth carry information about call quality in your data, which cases sit between plausible cutoffs, and whether your result is sensitive to those borderline cases. Does it change your rule?
- to gb01-team-b, delivered: QUESTION FROM THE DIRECTOR: the other team found that DLR1 activity score differs strongly between treatment arms and that, after weighting, imbalance remained for one site and one enrolment period. Check the weighted balance in your target set for each site, each period and the activity score, and check whether the activity score is in your propensity model for the benefit analysis as well as in the toxicity model. Does it change your method?
- to gb01-team-b, delivered: QUESTION FROM THE DIRECTOR: the other team noted that every low-confidence DLR1 call carries the same recorded activity-score value, and handled those calls with an indicator or by exclusion as sensitivity analyses, while you gave them their own level in the primary toxicity model. Check how the toxicity risk standardised to the whole target population moves across the ways of handling those calls (own level, excluded, recoded, taken at face value), and which of them matches what the data show about those calls. Does it change your primary choice?

Team C received 2 of 8 questions as warning signs.

Referee: picked neither. Test: est.py: one common AIPW run on both sets to split the gap into rows vs modelling. border.py/altfrac.py/ccf.py: disputed cases and full admitted set compared on independent time-zero activation markers (alt_molecule_fraction, purity/copy-corrected junction copies per tumour cell, TXR1 TPM, local copy) with benefit by arm.. Outcome: Common estimator: X set RD 29.3/tox 34.1/NU 17.4; Y set RD 25.8/tox 32.8/NU 14.4, so about half the gap is rows and half is Y's modelling. On the disputed rows X is right: X-only cases are core-like (alt fraction 0.16, ASE 0.81) and Y-only cases are not (alt fraction 0.04, lower TPM, TXR1i benefit 0.11). But both sets keep a shared cluster of 154 cases: alt_molecule_fraction is bimodal with a trough at 0.08-0.12, and this cluster has high local copy (median 5.4) and only about 0.27 junction copies per tumour cell versus about 0.9 in the main cluster. In this subclonal or amplification cluster TXR1i benefit is 3% vs 18% on SYS. Removing it gives RD 41.4, tox 33.5, NU 29.7 (on X rows RD 40.9, on Y rows RD 39.7).. Reason: Both teams admit a subclonal-junction, TXR1-amplified cluster that a time-zero marker separates cleanly. It is not SV-driven activation and dilutes the RD by about 12 pp. X is closer on the cases the teams dispute, but that is minor next to the shared error. Code 1 holds under every version. No bootstrap was run for own_answer (time limit).

Not chosen:
- gb01-team-a (analysis round): replaced by the same team's revise answer
- gb01-team-b (analysis round): replaced by the same team's revise answer
- gb01-team-b (revise round): no two teams agreed and the referee picked neither; the reported answer is gb01-referee's
- gb01-team-a (revise round): no two teams agreed and the referee picked neither; the reported answer is gb01-referee's
- gb01-team-c (third-team round): no two teams agreed and the referee picked neither; the reported answer is gb01-referee's

## gb02: replicated

Teams A and B answered blind and agreed.
Reported answer: from gb02-team-a (analysis round).
Grade: failed (score 0.0).

Answers given:
- gb02-team-a, analysis round: parent1_A_fraction = 0.301, parent1_t = 3.5, parent2_A_fraction = 0.747, parent2_t = 6.34
- gb02-team-b, analysis round: parent1_A_fraction = 0.298, parent1_t = 3.48, parent2_A_fraction = 0.749, parent2_t = 6.32

Comparisons:
- analysis: gb02-team-a and gb02-team-b agree

The director passed 8 item(s) from the library to the teams.

Not chosen:
- gb02-team-b (analysis round): agreed with gb02-team-a; that team's file is the reported copy

## Totals

Studies by status: replicated 1, unresolved 1.

| Role | Agent runs | Failed | Minutes | Cost (USD) |
|---|---|---|---|---|
| analyst | 7 | 0 | 23.3 | 5.20 |
| baseline | 8 | 0 | 41.0 | 10.51 |
| director | 3 | 0 | 3.5 | 1.26 |
| library | 2 | 0 | 4.8 | 1.11 |
| referee | 1 | 0 | 2.6 | 0.59 |
| all | 21 | 0 | 75.3 | 18.67 |

Graded: the lab passed 0 of 2 studies; single agents passed 0 of 8 attempts; the vote over attempts passed 0 of 2 studies.

## Measures

Counts come first because there are few studies; treat rates as rough.

| Measure | Passed |
|---|---|
| Lab final answers (studies) | 0 of 2 |
| Single-agent attempts (all attempts) | 0 of 8 |
| Single-agent attempt 1 only (studies) | 0 of 2 |
| Same-budget vote (k = 3, by dollars) | 0 of 2 |
| Vote over all attempts (studies) | 0 of 2 |

The same-budget vote uses the first 3 single attempts: the lab cost 4.0814 dollars per study and one attempt cost 1.3137.

| Label | Studies | Passed |
|---|---|---|
| replicated | 1 | 0 |
| converged | 0 | 0 |
| refereed | 0 | 0 |
| third-team | 0 | 0 |
| unresolved | 1 | 0 |
| single-source | 0 | 0 |
| no-answer | 0 | 0 |

**False agreement.** 1 of 1 replicated studies failed the grader. The two teams' first answers agreed in 1 studies; in 1 of them both answers were wrong, and in 0 only one was.

**Rescues and damage.** The question round turned 0 team answers from failing to passing and 0 from passing to failing, out of 2. The referee ran in 1 studies: it turned 0 team answers right and 0 wrong, and settled nothing in 0. Team C ran in 1 studies: it turned 0 studies right and 0 wrong.

**Selection.** In 0 studies at least one answer in the lab passed, and the lab shipped a passing answer in 0 of them. Studies where it shipped a failing answer although a passing one existed: none.

**Noticing to acting.** Teams recorded 25 warning signs. The director proposed 8 questions; the leak check blocked 0 and 8 were delivered. 1 of 9 replies said the question changed the method. 1 teams changed their answer, and 0 of them went from failing to passing.

**Library.** The library found 18 pitfalls, 18 of them with at least one source (32 sources in all). Teams reported 39 pitfall checks: 32 found the pitfall in the data and 31 changed a method.

**Cost.** The lab used 13 agent runs, 34.3 minutes and $8.16 (per study: 6.5 runs, 17.1 minutes, $4.08). The single agents used 8 runs, 41.0 minutes and $10.51. Passes per dollar: lab 0.00, single attempts 0.00.
