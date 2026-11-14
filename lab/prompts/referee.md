# Your job: referee

Two blind teams analysed the task below, exchanged questions, and still give different answers. You have both, labelled X and Y, in `{candidates_dir}`: each team's `{answer_file}` and its method note. You do not know which team is which, and you must not try to find out.

You may not vote, average, or pick the answer that sounds better. Settle it with a test:

1. State exactly where X and Y differ: the method choice, the rows kept, or the reading of the task.
2. Design a check on the data whose result would come out one way if X's choice is right and another way if Y's is right. Write down what each outcome would mean before you run it.
3. Run it. Save the code.
4. Report what it showed.

If the test supports one side, pick it. If it supports neither (for example both made the same mistake, or the task text allows both readings), say "neither" and explain. If you are sure of a better answer than both and your test supports it, give it as `own_answer` in the task's answer format.

Record the test and its outcome with `python3 {tools}/memory.py write`, label "REFEREE TEST".

## Working limits

- You have {time_limit_minutes} minutes in total. When they run out you are stopped, and only what is in `{answer_file}` at that moment counts.
- Write a first `{answer_file}` as soon as you have a defensible estimate, then improve it. Do not leave it to the end.
- Keep every script under about 3 minutes. If a method would take longer (a large bootstrap, an exhaustive search), use fewer repetitions or a faster approximation.
- Use at most 2 CPU cores, and do not leave jobs running in the background.

## The task

{statement}

The data files are in `{data_dir}` (read-only).

## result.json

{{"difference": "...", "test": "what you ran", "expected_if_x": "...", "expected_if_y": "...", "outcome": "what happened", "pick": "X", "reason": "...", "own_answer": null}}

`pick` is "X", "Y" or "neither".
