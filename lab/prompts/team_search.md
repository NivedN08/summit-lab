## Web search

Unlike the general rule above, you may search the web while you work, through the lab's gateway, with these two commands only:
- `python3 {tools}/research.py search "QUERY" --objective "what you want and why"`
- `python3 {tools}/research.py contents URL [URL ...] --objective "why"`

You have {team_searches} calls in this round; both commands count, and the gateway refuses calls over the limit.

- Use it when you are unsure how a method is defined, what a tool does by default, or which convention applies.
- For anything your answer will depend on, read the page with `contents`; do not rely on a snippet.
- Never search for this task, its dataset, a benchmark or a worked solution.
- Record each thing you learn with `memory.py write`, label "LEARNED", in this form:
  "LEARNED: <what>. USED FOR: <which step>. SOURCE: <link>."
