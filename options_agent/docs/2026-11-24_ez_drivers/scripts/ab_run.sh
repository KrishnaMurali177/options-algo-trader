#!/usr/bin/env bash
# post-stag block A/B — 2 symbols x 2 windows x 4 variants, 8 parallel. Runs from .worktree-ez (research/ez-drivers).
cd ~/projects/options_algo/options-algo-trader
export W=$PWD/.worktree-ez/options_agent O=options_agent/data_cache/ez_ab
one() {  # sym win var
  case $2 in real) WIN="--start-date 2024-02-01 --end-date 2026-11-21 --real-options";; syn) WIN="--start-date 2020-07-27 --end-date 2024-01-31 --no-real-options";; esac
  case $3 in off) V=0;; b6) V=6;; b12) V=12;; halt) V=-1;; esac
  docker-compose --profile live run --rm --no-deps -v $W/scripts:/app/scripts:ro -v $W/src:/app/src:ro agent-spy \
    python scripts/replay_sweet_spot.py --symbol $1 --slippage 0.035 --vix-lag 1 --days 4000 $WIN \
    --cascade-size-low 1 --cascade-size-mid 1 --cascade-size-high 1 --skip-failed-bounce --post-stag-block-bars $V \
    --export-triggers /app/data_cache/ez_ab/$1_$2_$3.json > $O/$1_$2_$3.txt 2>&1
  echo "$(date +%T) $1_$2_$3 $(grep -E "Total Option P&L \(per" $O/$1_$2_$3.txt)" >> $O/progress.log
}
export -f one
for s in SPY QQQ; do for w in real syn; do for v in off b6 b12 halt; do echo "$s $w $v"; done; done; done | xargs -P 8 -L 1 bash -c "one \$0 \$1 \$2"
echo ALLDONE >> $O/progress.log
