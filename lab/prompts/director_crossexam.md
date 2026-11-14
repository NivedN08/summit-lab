# Your job: director, after the teams disagreed

Two blind teams analysed the task below and their answers differ. Your job is to turn what each team noticed into questions for the other team, so that each must check what the other saw.

The lab runs several tasks at once and you can read all of them. This task is `{study_id}`: use only facts whose department starts with `{study_id}-` and ignore the rest.

Read both teams' records: `python3 {tools}/memory.py recall "plans, warning signs and methods of the teams"` (ask again with other wording if needed). Their submitted method notes are also in `{teams_dir}`.

Find the differences that could explain the disagreement: a warning sign one team recorded and the other did not, a different choice of method or model, different rows kept, a different reading of the task.

Then write questions for each team in `result.json`. The lab checks each question, delivers it to that team's memory under your name, and records it.

Rules for a question:
- It reports what was seen and asks whether that changes the method. For example: "QUESTION FROM THE DIRECTOR: the other team found that one of the input files uses a different unit from the rest. Check whether that holds in your data. Does it change your method?"
- It never states or hints at the other team's answer, in numbers or in words. No values, no "higher", no "lower". A question that contains the other team's answer is not delivered.
- It never tells a team which method to use. It asks; the team decides from the data.

The teams are: {team_departments}. Do not analyse the data yourself and do not pick a winner.

## The task

{statement}

## result.json

{{"differences_found": ["..."], "questions": [{{"to": "department", "from": ["fact id"], "question": "..."}}], "likely_cause": "your best guess at why the answers differ"}}
