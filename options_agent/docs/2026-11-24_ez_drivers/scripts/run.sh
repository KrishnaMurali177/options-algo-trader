#!/usr/bin/env bash
cd ~/projects/options_algo/options-algo-trader
W=$PWD/.worktree-live-main/options_agent
O=options_agent/data_cache/ez_study
A="--cascade-size-low 1 --cascade-size-mid 1 --cascade-size-high 1 --skip-failed-bounce"
for s in SPY QQQ; do
  docker-compose --profile live run --rm --no-deps -v $W/scripts:/app/scripts:ro -v $W/src:/app/src:ro agent-spy \
    python scripts/replay_sweet_spot.py --symbol $s --slippage 0.035 --vix-lag 1 --days 4000 --start-date 2016-01-04 --end-date 2026-11-21 \
    --no-real-options $A --export-triggers /app/data_cache/ez_study/${s}_trig.json > $O/${s}_replay.txt 2>&1 &
done
wait; echo ALLDONE > $O/done
