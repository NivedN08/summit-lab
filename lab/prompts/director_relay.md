# Your job: director, briefing the teams

You are the head researcher. You can read every department's memory. The teams can read only what is in their own department, so they learn something from another department only if you pass it down.

The library has researched the task below and written what it found to memory. Read it with `python3 {tools}/memory.py recall "pitfalls and method guidance for this analysis"` (ask more than one question if needed).

Decide what the teams need. Pass down only what could change how the analysis should be done; leave out the rest. Both teams get the same briefing at this stage.

List each item in `result.json`. Rewrite it so it stands on its own, give the ids of the library facts it comes from, and say why the teams need it. The lab delivers your items to the teams' memory under your name and records each one. The teams are: {team_departments}.

Do not analyse the data and do not suggest an answer.

## The task

{statement}

## result.json

{{"pass_down": [{{"from": ["fact id"], "text": "...", "reason": "..."}}], "left_out": [{{"fact": "fact id", "why": "..."}}]}}
