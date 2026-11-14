# Your job: the lab's library

Two analysis teams are about to work on the task below, separately and without seeing each other. Before they start, find out how an analysis of this kind should be done and, above all, what usually goes wrong in it.

You are the only department with web search. Use it through:
- `python3 {tools}/research.py search "QUERY" --objective "what you want and why"`
- `python3 {tools}/research.py contents URL [URL ...] --objective "why"`

Search for methods, standard diagnostics, known biases and pitfalls for this kind of data and this kind of estimate: methods papers, tool documentation, tutorials, statistical guidance. You may make up to {max_searches} calls.

Do not search for this task itself, for its dataset, or for any benchmark or worked solution. You are looking for how experts do this kind of analysis, not for anyone's answer to this one.

Do not analyse the data yourself and do not propose an answer. You may list the data files and read their headers to learn what kind of data it is.

For each pitfall you find, write one fact to memory with `memory.py write`, phrased so an analyst can act on it:
"PITFALL: <what goes wrong>. CHECK: <the concrete check to run on the data>. IF FOUND: <how the method should change>. SOURCE: <link>."

Aim for the 5 to 10 pitfalls most likely to change the answer. Prefer a pitfall that changes which method is right over one that only cleans the data.

## The task the teams will work on

{statement}

The data files are in `{data_dir}` (read-only).

## result.json

{{"pitfalls": [{{"title": "...", "check": "...", "if_found": "...", "sources": ["https://..."]}}], "searches_made": 0, "notes": "anything the director should know"}}
