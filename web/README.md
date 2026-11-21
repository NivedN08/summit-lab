# Summit Lab on the web

A pixel-art lab floor that replays a real run of the lab. Every agent is a person at a desk. People walk when information moves between departments: a librarian carries findings to the director, the director carries a question to a team. Notes travel the corridors to the memory vault, and Team A's and Team B's answers meet only at the hatch in the wall between their rooms. Every movement starts from an event in the run's log.

In runs made with department heads (`--heads`), each head sits at a wooden desk at the back of the room it runs, walks to the scientist whose work it reviews, and sends its notes as envelopes; a note the lab holds back shows a red cross over the head.

Click anyone to see what they are doing, what they are reading, and the notes they have written. Ask them a question, and they answer only from what they could read at that moment of the run. Ask Team B what Team A found, and it will tell you it cannot see that.

## Run it

```bash
cd web
npm install
npm run build
python3 server.py            # http://127.0.0.1:8790
```

The server is standard-library Python. It serves the built page, lists runs from `results/` and `runs/` (so a run still in progress shows up live), and answers questions through Claude. It uses `ANTHROPIC_API_KEY` if that is set, and otherwise the Claude Code command-line tool on your own login, with no tools and none of your settings. Set `SUMMIT_ASK_MODEL` to change the model (default `claude-sonnet-5-5`).

Without the server, the built page still works from any static host: it replays the runs baked into `public/runs/` and answers questions from the run log without a model.

For development, run `npm run dev` (port 5173) next to `python3 server.py`; Vite passes `/api` to the server.

## The demo: 5 recorded minutes

The page opens on a 5-minute clip of a real run, recorded event by event, and plays it on a loop at 4x. "Whole run" in the top bar leaves the clip; "Demo" goes back. The clip is chosen by `make_demo.py` and stored in `public/runs/demo.json`.

Today's clip is the busiest 5 minutes of all the runs: the GeneBench-Pro test run from 3:12 to 3:17 PM Pacific (Nov 14), when the director sent 67 questions, the hatch compared answers 11 times, two referees ruled and four studies finished.

When the research ends, if its results are what we present, switch the demo to the last 5 minutes of the final run, which end on the grading and a results card:

```bash
python3 web/make_demo.py --run results/<final run> --last    # or runs/<final run> if it is not in results/ yet
cd web && npm run build
```

Other choices: `python3 web/make_demo.py` (the busiest 5 minutes across every baked run), `--run <name>` (the busiest 5 minutes of one run), `--minutes 3`, `--speed 12`. The busiest window is scored by what a viewer sees move: walks, notes and answers in flight, comparisons, verdicts, results, with a bonus for each kind of interaction. Tests: `python3 -m pytest web/test_demo.py -q`.

## Data

`python3 build_data.py` turns every run in `results/` into `public/runs/<run>.json`. It reads only a run's `events.jsonl`, `study.json` files and `results.json`, plus `records/exa_calls.jsonl` for the librarians' searches. It never reads `data/` or a run's workspaces, and it drops local paths and session ids.

## Who can read what in the chat

The same rule as the lab's memory, applied at the replay's current moment (`visible_docs` in `server.py`, `visibleDocs` in `src/ask/offline.ts`):

| Asking | Can answer from |
|---|---|
| Lab guide | The whole run, including the grades |
| Director | Every department's notes, every question and challenge. No grades |
| Department head (runs made with `--heads`) | The same as the director: every department, the other heads' notes. No grades |
| Librarian | Its own notes and its own web searches |
| Team A, B or C | Its own notes, what the director passed to it, its own answers, and which answer fields matched |
| Referee | The two answers labelled X and Y, and its own test |
| Solo agent | Its own sessions only |

Tests: `python3 -m pytest web/test_server.py -q`.

## Keys

Space plays or pauses. Left and right arrows jump between moments. `+`, `-` and `0` zoom. Escape closes a panel.
