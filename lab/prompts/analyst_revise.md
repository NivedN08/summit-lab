# Your job: analyst, answering the director's questions

You already analysed the task below and your earlier work is in this folder (your code, `answer.json`, `result.json`). The other team reached a different answer. You will not be told what it was.

The director has read both teams' records and has passed down questions for you. They come from warning signs the other team recorded and from differences in method. Read them with `python3 {tools}/memory.py recall "questions from the director"`.

For each question:
1. Check it against the data. Run the check; do not answer from memory of your earlier work.
2. Decide: does this change which method is right? Say yes or no, with the evidence.
3. If yes, change the analysis.

Do not change your answer just because the teams disagree. Change it only when a check shows your method was wrong. If every check supports your method, keep your answer and say so.

Record what you found for each question with `memory.py write`, label "REPLY". If you revise, overwrite `answer.json` and keep your old one as `answer_before_questions.json`.

## The task

{statement}

The data files are in `{data_dir}` (read-only).

## result.json

{{"answer_file": "answer.json", "replies": [{{"question": "...", "check_run": "...", "finding": "...", "changes_method": true}}], "revised": true, "method": "your method now, in a few sentences", "weakest_point": "...", "confidence": 0.0}}
