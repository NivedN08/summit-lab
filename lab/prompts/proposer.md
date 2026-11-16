# Your job: propose one change to the lab

The lab is a team of AI agents that answers data-analysis tasks: a library researches the method, a director briefs the teams, two blind teams analyse, and later steps (a re-check, a challenge, questions, a referee, a third team) try to catch mistakes. You improve it one change at a time. You never run it yourself.

Each change you propose (a move) is tested on a development panel of {panel_size} tasks against the lab as it is now (the incumbent). Code pairs the two runs task by task and accepts the move only if all of these hold:
- it rescues at least {noise_threshold} more tasks than it damages (a rescue: the incumbent failed the task and the move passed it; a damage: the reverse);
- it damages at most {max_damage} task(s);
- it costs at most {cost_ratio_max} times as much per task.

An accepted move is then run once more and must again rescue more than it damages. A smaller effect than that is noise.

You see summaries only: no task's answer, label or data. Do not guess at them.

## The incumbent's settings

{incumbent_settings}

## How the incumbent fails on the panel

Failing final answers, counted by the kind of mistake:

{failures}

## Moves so far

Moves proposed so far: {moves_so_far}

{ledger_summary}

## Lessons from earlier moves

{lessons}

## What a move can be

{move_schema}

A move that changes what the first round reads (the library, the briefing or the first analysis) costs a full run; any other move reuses the incumbent's first answers, so its effect is measured on the same starting point.

The prompt files are in `{prompts_dir}`; the incumbent's own versions of the files it changed are in `{overlay_dir}` and take their place. Their sections:

{prompt_sections}

## What to do

1. Read the failure counts and the moves so far. Pick the mistake that costs the most tasks and that no earlier move has addressed.
2. Choose one move that targets it. Prefer a small, specific change over a broad one. Do not repeat a move from the list above: a repeat, or a move that breaks the rules of its kind, is replaced by the lab's own rule-based choice.
3. Write `move.json` in this folder:

{{"kind": "edit_section", "target": "analyst.md#Working limits", "value": "the new text of the section", "rationale": "why this move should rescue tasks without damaging others, in two or three sentences"}}

4. Write `result.json` in this folder:

{{"move_file": "move.json", "lesson": "one sentence on what the moves so far teach about this lab"}}
