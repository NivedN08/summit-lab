#!/bin/bash
# The held-out batch (prereg/heldout-biodsa.json, heldout-biodsa-plain.json, heldout-planted.json).
# Runs every registered command, at most 4 at once. A command that halts on a usage limit, a spending cap or
# a crash is resumed with the same command, up to 3 more times a minute apart (the specs' stop rules);
# a command stopped from outside (killed by a signal) is left stopped.
# Grading starts only after every command has exited, then measures, then the frozen analysis.
set -u
cd "$(dirname "$0")/.."
LOGS=runs/heldout/logs
mkdir -p "$LOGS"

python3 - > "$LOGS/commands.txt" <<'EOF'
import json
for name in ("heldout-biodsa", "heldout-planted", "heldout-biodsa-plain"):
    for arm in json.load(open(f"prereg/{name}.json"))["arms"]:
        print(arm.split(": ", 1)[1])
EOF

run_one() {
  cmd="$1"
  dir=$(echo "$cmd" | sed -E 's/.*--run-dir ([^ ]+).*/\1/')
  log="$LOGS/$(echo "$dir" | sed 's#runs/heldout/##; s#/#-#g').log"
  for try in 1 2 3 4; do
    caffeinate -i $cmd >> "$log" 2>&1
    code=$?
    echo "$(date -u +%H:%M:%S) $dir try $try exit $code"
    [ $code -eq 0 ] && return 0
    [ $code -gt 128 ] && return $code      # stopped from outside (a signal): not restarted without the owner
    grep -q "pre-registration check failed" "$log" && return $code    # never retried: the command is wrong
    sleep 60
  done
  return $code
}
export -f run_one
export LOGS

tr '\n' '\0' < "$LOGS/commands.txt" | xargs -0 -P 4 -I{} bash -c 'run_one "$1"' _ {}
echo "$(date -u +%H:%M:%S) every command has exited"

for d in runs/heldout/biodsa/* runs/heldout/planted/*; do
  [ -d "$d" ] && [ "$(basename "$d")" != logs ] || continue
  python3 -m lab.run grade --run-dir "$d" --ledger > "$d.grade.txt" 2>&1; echo "graded $d exit $?"
  python3 -m lab.run measures --run-dir "$d" > "$d.measures.txt" 2>&1
done
python3 -m lab.heldout runs/heldout --json runs/heldout/analysis.json > "$LOGS/analysis.txt" 2>&1
echo "analysis exit $?"
