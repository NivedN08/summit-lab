# Your job: re-check a Not verifiable answer

You already analysed the task below and your earlier work is in this folder (your code, `{answer_file}`, `result.json`). You answered "Not verifiable": that the data hold no variables with which to test the claim.

Before the lab accepts that, check it once more. A variable that tests a claim can sit in a table you did not open, or go by another name than the claim's words. The lab listed every data file and its columns, by code, and marked what your code and notes never mention. The full list is in `recheck_inventory.json` in this folder; the part most likely to matter is here:

{inventory}

Do this:
1. Name the variables that would test the claim: the groups, exposure or condition it names, and the outcome it names. Look in every file, including those you never opened.
2. If you find them, run the test, with code you save in this folder.
3. Write `test_result.json` in this folder (the shape is below).
4. Write `{answer_file}` again, in the task's format, with the label your test supports. Keep your old one as `answer_before_recheck.json`.

If the data still hold nothing that can test the claim, keep "Not verifiable", write `test_result.json` with what you looked for, and say why in `result.json`. Do not change your answer just because you were asked to look again.

The lab decides by code whether a new label replaces "Not verifiable". It does so only for "True" or "False" with a complete `test_result.json` written in this round and a test on at least {min_n} rows (`n`); for "True" the test's direction must support the claim and its p-value must be {max_p} or less, and for "False" its direction must contradict the claim.{evidence_note}

## test_result.json

{{"test": "the test you ran", "variables": ["file: column", "..."], "statistic": 0.0, "p_value": 0.0, "n": 0, "direction": "supports"}}

`direction` is "supports" when the result shows what the claim says, and "contradicts" when it shows the opposite, or no effect where the claim says there is one.

## Working limits

- You have {time_limit_minutes} minutes in total. When they run out you are stopped, and only what is in `{answer_file}` at that moment counts.
- Keep every script under about 3 minutes. If a method would take longer (a large bootstrap, an exhaustive search), use fewer repetitions or a faster approximation.
- Use at most 2 CPU cores, and do not leave jobs running in the background.

## The task

{statement}

The data files are in `{data_dir}` (read-only).

## result.json

{{"answer_file": "{answer_file}", "variables_found": ["file: column", "..."], "test_file": "test_result.json", "changed": true, "method": "what you tested and what it showed, in a few sentences", "confidence": 0.0}}
