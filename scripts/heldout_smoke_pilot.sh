#!/bin/bash
# Held-out prep, paid steps on dev tasks only (research/heldout-plan.md steps 4 and 5). Total cap $45.
# Step 4, smoke: 2 BioDSA dev tasks (convention text) and 2 planted dev tasks through every arm.
# Step 5, pilot: all 8 planted dev tasks through the lab and Opus alone (4 attempts).
set -u
cd /Users/nivednandakumar/Downloads/summit-lab
V=runs/loop2/variants/v3-opus
S=runs/heldout-smoke
mkdir -p $S
BD=bd01,bd02
PF=pf03,pf04
LAB="--backend modal --memory past --parallel 4 --config-file $V/config.json --prompt-dir $V/prompts"
run() { name=$1; shift; echo "start $name"; caffeinate -i python3 -m lab.run "$@" > $S/$name.log 2>&1; echo "$name exit $?"; }

# smoke (expected about $10; caps sum to $26)
run lab-biodsa     lab      --benchmark biodsa-convention --studies $BD --run-dir $S/lab-biodsa  $LAB --max-usd 6 &
run lab-planted    lab      --benchmark planted-dev       --studies $PF --run-dir $S/lab-planted $LAB --max-usd 7 &
wait
run vote-biodsa    arm --arm vote --benchmark biodsa-convention --studies $BD --run-dir $S/vote-biodsa  --backend modal --model opus --attempts 3 --parallel 6 --max-usd 2 &
run vote-planted   arm --arm vote --benchmark planted-dev       --studies $PF --run-dir $S/vote-planted --backend modal --model opus --attempts 3 --parallel 1 --max-usd 2 &
run self-biodsa    arm --arm self --benchmark biodsa-convention --studies $BD --run-dir $S/self-biodsa  --backend modal --model opus --parallel 2 --max-usd 2 &
run self-planted   arm --arm self --benchmark planted-dev       --studies $PF --run-dir $S/self-planted --backend modal --model opus --parallel 1 --max-usd 2 &
run opus-biodsa    baseline --benchmark biodsa-convention --studies $BD --run-dir $S/opus-biodsa   --backend modal --model opus  --attempts 1 --parallel 2 --max-usd 1 &
run opus-planted   baseline --benchmark planted-dev       --studies $PF --run-dir $S/opus-planted  --backend modal --model opus  --attempts 1 --parallel 1 --max-usd 2 &
run fable-biodsa   baseline --benchmark biodsa-convention --studies $BD --run-dir $S/fable-biodsa  --backend modal --model fable --attempts 1 --parallel 2 --max-usd 1 &
run fable-planted  baseline --benchmark planted-dev       --studies $PF --run-dir $S/fable-planted --backend modal --model fable --attempts 1 --parallel 1 --max-usd 1 &
wait
for d in $S/*/; do python3 -m lab.run grade --run-dir $d > ${d%/}.grade.txt 2>&1; echo "graded $d exit $?"; done

# pilot (expected about $16; caps sum to $19)
P=runs/heldout-pilot
mkdir -p $P
S=$P
run lab-planted    lab      --benchmark planted-dev --run-dir $P/lab-planted  $LAB --parallel 8 --max-usd 14 &
run opus-planted   baseline --benchmark planted-dev --run-dir $P/opus-planted --backend modal --model opus --attempts 4 --parallel 4 --max-usd 5 &
wait
for d in $P/*/; do python3 -m lab.run grade --run-dir $d > ${d%/}.grade.txt 2>&1; echo "graded $d exit $?"; done
