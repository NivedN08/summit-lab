# Hypothesis

Current or former smokers have a lower FEV1 percent than never smokers.

# Your task

Decide whether the data support the hypothesis ("True"), contradict it ("False") or cannot settle it
("Not verifiable"). Write `answer.json`: {"answer": {"label": "<label>"}, "reasoning": "..."}


The data files are in `<DATA>` (read-only). You have no web access. Write your final answer to `answer.json` in your working folder, exactly as the task asks. Then write `result.json` containing {"answer_file": "answer.json"} and stop.

One way to approach it: Start from the model the task implies and test its assumptions.

## Working limits

- You have 30 minutes in total. When they run out you are stopped, and only what is in `answer.json` at that moment counts.
- Write a first `answer.json` as soon as you have a defensible estimate, then improve it. Do not leave it to the end.
- Keep every script under about 3 minutes. If a method would take longer (a large bootstrap, an exhaustive search), use fewer repetitions or a faster approximation.
- Use at most 2 CPU cores, and do not leave jobs running in the background.

