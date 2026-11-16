# Your job: analyst, answering the director's questions

You already analysed the task below and your earlier work is in this folder (your code, `answer.json`, `result.json`). A director has read your code and notes and does not think one analysis alone makes your answer right. It has questions for you.

Its questions are in `director_questions.json` in this folder.

For each question:
1. Check it against the data. Run the check; do not answer from memory of your earlier work.
2. Decide: does this change which method is right? Say yes or no, with the evidence.
3. If yes, change the analysis.

Do not change your answer just because you were asked. Change it only when a check shows your method was wrong. If every check supports your method, keep your answer and say so.

Record what you found for each question in `result.json` (below). If you revise, overwrite `answer.json` and keep your old one as `answer_before_questions.json`.

## Working limits

- You have 30 minutes in total. When they run out you are stopped, and only what is in `answer.json` at that moment counts.
- Write a first `answer.json` as soon as you have a defensible estimate, then improve it. Do not leave it to the end.
- Keep every script under about 3 minutes. If a method would take longer (a large bootstrap, an exhaustive search), use fewer repetitions or a faster approximation.
- Use at most 2 CPU cores, and do not leave jobs running in the background.

## The task

# Hypothesis

Current or former smokers have a lower FEV1 percent than never smokers.

# Your task

Decide whether the data support the hypothesis ("True"), contradict it ("False") or cannot settle it
("Not verifiable"). Write `answer.json`: {"answer": {"label": "<label>"}, "reasoning": "..."}


The data files are in `<DATA>` (read-only).

## result.json

{"answer_file": "answer.json", "replies": [{"question": "...", "check_run": "...", "finding": "...", "changes_method": true}], "revised": true, "method": "your method now, in a few sentences", "weakest_point": "...", "confidence": 0.0}
