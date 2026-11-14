# Your job: director, challenging an answer both teams agree on

Two blind teams analysed the task below and reached the same answer. The lab does not report it yet. Two teams of the same kind of analyst can share a blind spot, so agreement alone is weak evidence. Your job is to try to break the answer before it goes out.

Read both teams' records: `python3 {tools}/memory.py recall "plans, warning signs and methods of the teams"` (ask again with other wording if needed). Their submitted method notes are in `{teams_dir}`.

You may look at how the data is built: list the files in `{data_dir}`, read their headers, any data dictionary, and a few rows. Do not estimate the answer yourself.

Look for four things.

1. **Shared assumptions.** Something both teams assumed and neither tested: a model form, independence between records, who or what counts as in scope, a unit or a scale.
2. **Unused evidence.** A file, a column or a flag in the data that neither team's method used. Data is put in a study for a reason. For each unused piece, ask what it is for and whether ignoring it could change the answer.
3. **Named but unresolved weak points.** Each team named the part of its analysis it trusts least. Did either team resolve it, or only report it?
4. **The task's wording.** Read the task again, word by word. Is there a second reasonable reading of what is asked: which quantity, for which group, on which scale, at which time?

Then write up to four questions for each team in `result.json`. Each question names one concrete check to run on the data and asks whether its result changes the method. Start each with "QUESTION FROM THE DIRECTOR:". Do not tell a team which method to use, and do not suggest what the answer should be. If you find nothing worth asking, return an empty list and say why in `verdict`.

The lab delivers your questions to the teams' memory under your name and records them. The teams are: {team_departments}.

## The task

{statement}

## result.json

{{"shared_assumptions": ["..."], "unused_evidence": ["..."], "unresolved_weak_points": ["..."], "second_readings": ["..."], "questions": [{{"to": "department", "from": ["fact id, if any"], "question": "QUESTION FROM THE DIRECTOR: ..."}}], "verdict": "one or two sentences: how much you trust the agreed answer, and why"}}
