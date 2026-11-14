{statement}

The data files are in `{data_dir}` (read-only). You have no web access. Write your final answer to `answer.json` in your working folder, exactly as the task asks. Then write `result.json` containing {{"answer_file": "answer.json"}} and stop.

## Working limits

- You have {time_limit_minutes} minutes in total. When they run out you are stopped, and only what is in `answer.json` at that moment counts.
- Write a first `answer.json` as soon as you have a defensible estimate, then improve it. Do not leave it to the end.
- Keep every script under about 3 minutes. If a method would take longer (a large bootstrap, an exhaustive search), use fewer repetitions or a faster approximation.
- Use at most 2 CPU cores, and do not leave jobs running in the background.

