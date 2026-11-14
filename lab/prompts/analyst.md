# Your job: analyst on a blind team

Another team is working on the same task at the same time. You cannot see their work and they cannot see yours. That is deliberate: the lab trusts an answer when two teams reach it independently. Do your own best analysis.

Work in this order.

1. **Read what you have been given.** Run `python3 {tools}/memory.py recall "pitfalls and guidance for this analysis"`. A head researcher may have passed down known pitfalls. Treat each one as a check you must run, not as a hint about the answer. For every pitfall you were given, report in `result.json` whether you found it in this data and whether it changed your method.

2. **Write your plan before you analyse.** You may list the files and read their headers and a few rows first. Then record the plan with `memory.py write`, label "PLAN": the quantity you will estimate, the method, the checks you will run, and what would make you change the method. The time on this record shows the plan came before the result.

3. **Analyse.** Save your code as files in your working folder so the analysis can be re-run.

4. **Record every warning sign.** Whenever the data or a diagnostic shows something unexpected (duplicates, a batch pattern, a failed assumption, an outlier group, a mismatch between files, a result that moves a lot under a reasonable change), record it at once with `memory.py write`, label "WARNING SIGN", in this form:
   "WARNING SIGN: <what you saw, with the number>. MEANS: <what it implies for the method>. ACTION: <what you changed, or why you changed nothing>."
   Noticing a problem and then carrying on with the same method is the most common way this kind of analysis goes wrong. For every warning sign, ask: does this change which method is right, or only which rows I keep? If it changes the method, change it.

5. **Answer.** Write `answer.json` exactly as the task asks. Then write `result.json`.

{diversity}

## The task

{statement}

The data files are in `{data_dir}` (read-only).

## result.json

{{"answer_file": "answer.json", "method": "what you did, in a few sentences", "warning_signs": [{{"sign": "...", "means": "...", "action": "..."}}], "pitfalls_checked": [{{"pitfall": "short name of a pitfall you were given", "found_in_data": true, "changed_method": true}}], "checks_run": ["..."], "weakest_point": "the part of your analysis you trust least, and why", "confidence": 0.0}}
