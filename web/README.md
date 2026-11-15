# Summit Lab on the web

A pixel-art lab floor that replays a real run of the lab. Every agent is a person at a desk. People walk when information moves between departments: a librarian carries findings to the director, the director carries a question to a team. Notes travel the corridors to the memory vault, and Team A's and Team B's answers meet only at the hatch in the wall between their rooms. Every movement starts from an event in the run's log.

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

## Data

`python3 build_data.py` turns every run in `results/` into `public/runs/<run>.json`. It reads only a run's `events.jsonl`, `study.json` files and `results.json`, plus `records/exa_calls.jsonl` for the librarians' searches. It never reads `data/` or a run's workspaces, and it drops local paths and session ids.

## Who can read what in the chat

The same rule as the lab's memory, applied at the replay's current moment (`visible_docs` in `server.py`, `visibleDocs` in `src/ask/offline.ts`):

| Asking | Can answer from |
|---|---|
| Lab guide | The whole run, including the grades |
| Director | Every department's notes, every question and challenge. No grades |
| Librarian | Its own notes and its own web searches |
| Team A, B or C | Its own notes, what the director passed to it, its own answers, and which answer fields matched |
| Referee | The two answers labelled X and Y, and its own test |
| Solo agent | Its own sessions only |

Tests: `python3 -m pytest web/test_server.py -q`.

## Keys

Space plays or pauses. Left and right arrows jump between moments. `+`, `-` and `0` zoom. Escape closes a panel.
