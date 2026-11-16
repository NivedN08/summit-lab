# Your job: director, challenging one analyst's answer

One analyst worked on the task below alone and reached an answer. The answer is not reported yet. An analyst who checks only its own work can share its own blind spot, so one analysis alone is weak evidence. Your job is to try to break the answer before it goes out.

The analyst's code and its notes are in `<TEAMS>`. Its answer is not there, and you must not try to find it.

You may look at how the data is built: list the files in `<DATA>`, read their headers, any data dictionary, and a few rows. Do not estimate the answer yourself. You have no web access.

Look for four things.

1. **Untested assumptions.** Something the analyst assumed and did not test: a model form, independence between records, who or what counts as in scope, a unit or a scale.
2. **Unused evidence.** A file, a column or a flag in the data that the analyst's method did not use. Data is put in a study for a reason. For each unused piece, ask what it is for and whether ignoring it could change the answer.
3. **Unresolved weak points.** The part of the analysis that is least secure. Did the analyst resolve it, or pass over it?
4. **The task's wording.** Read the task again, word by word. Is there a second reasonable reading of what is asked: which quantity, for which group, on which scale, at which time?

Then write up to four questions for the analyst in `result.json`. Each question names one concrete check to run on the data and asks whether its result changes the method. Start each with "QUESTION FROM THE DIRECTOR:". Do not tell the analyst which method to use, and do not suggest what the answer should be. If you find nothing worth asking, return an empty list and say why in `verdict`.

The lab gives your questions to the analyst and records them. Write `result.json` in your working folder, in exactly the shape below, then stop.

## The task

# Hypothesis

Current or former smokers have a lower FEV1 percent than never smokers.

# Your task

Decide whether the data support the hypothesis ("True"), contradict it ("False") or cannot settle it
("Not verifiable"). Write `answer.json`: {"answer": {"label": "<label>"}, "reasoning": "..."}


## result.json

{"untested_assumptions": ["..."], "unused_evidence": ["..."], "unresolved_weak_points": ["..."], "second_readings": ["..."], "questions": [{"question": "QUESTION FROM THE DIRECTOR: ..."}], "verdict": "one or two sentences: how much you trust the answer, and why"}
