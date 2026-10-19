#!/usr/bin/env bash
# Reproduce every number and figure in docs/FINAL_REPORT.md and README.md.
#
#   bash scripts/reproduce.sh            # all steps, in order
#   bash scripts/reproduce.sh <step>     # one step: data | pilot | laya | jev | latency | report
#
# Resumable: every runner call appends to results/runs/<run>/predictions.jsonl and skips rows that
# already exist, so an interrupted step can simply be run again. Run names match the published study.
set -euo pipefail
cd "$(dirname "$0")/.."

PY=${PY:-$HOME/.venvs/de-jev/bin/python}
DEVICE=${DEVICE:-cuda}
LAYA=laya_routed,laya_en_fixed,laya_multi_fixed
STEP=${1:-all}

log() { echo "== $(date +%T) $*"; }
run() { log "runner $*"; "$PY" -m debench.runner "$@"; }
remote_enabled() {
  "$PY" -c "from debench.config import load_settings as s; raise SystemExit(0 if s().remote_enabled else 1)"
}

step_data() {
  "$PY" -m debench.doctor > /dev/null && log "environment written to results/hardware.json"
  for ctx in common_fit natural_long; do
    for c in E01 E02 E03 E04 E05 E06; do
      log "build $c $ctx"
      "$PY" -m debench.dataset.build "$c" --mode db --context "$ctx"
    done
  done
  "$PY" -m pytest -q tests/test_pair_equivalence.py tests/test_gold_is_not_in_prompt.py tests/test_db_ground_truth.py
  # The rebuilt data and the question sets must match the frozen study byte for byte.
  "$PY" -c "
from debench.freeze import CASES, check_frozen
for c in CASES:
    for ctx in ('common_fit', 'natural_long'):
        check_frozen(c, ctx)
print('frozen prompts and datasets match configs/prompts/FROZEN.json')"
}

step_pilot() {
  "$PY" -m debench.runner --case all --tracks rules_only --protocol primary --split dev --n 20 --run-name pilot_rules
  run --case all --tracks "$LAYA" --protocol primary --split dev --n 20 --device "$DEVICE" --warmup 30 --run-name pilot_laya
  if remote_enabled; then
    run --case all --tracks jev_114 --protocol primary --split dev --n 20 --concurrency 4 --run-name pilot_jev
  else
    log "Jev pilot skipped: set ALLOW_REMOTE_API=1 and TYPESAFE_API_KEY in .env to run it"
  fi
}

step_laya() {
  run --case all --tracks rules_only --protocol primary --split test --run-name rules_test
  run --case all --tracks rules_only --protocol primary --split calibration --run-name rules_calibration
  run --case all --tracks "$LAYA" --protocol primary --split test --device "$DEVICE" --warmup 30 --run-name test_primary_laya
  run --case all --tracks "$LAYA" --protocol primary --split calibration --device "$DEVICE" --warmup 30 --run-name calibration_primary_laya
  run --case all --tracks "$LAYA" --protocol P4_cross_language --split test --device "$DEVICE" --warmup 30 --run-name test_p4_laya
  run --case all --tracks laya_routed --protocol P5_order --split test --device "$DEVICE" --warmup 30 --run-name test_p5_laya
  run --case all --tracks "$LAYA" --protocol primary --split test --context natural_long --device "$DEVICE" --warmup 30 --run-name test_p7_long_laya
}

step_jev() {
  if ! remote_enabled; then
    log "Jev skipped: remote calls are disabled (ALLOW_REMOTE_API=1 and TYPESAFE_API_KEY are required)"
    return 0
  fi
  # About 11,000 paid requests (~USD 0.50 at USD 0.042 per million input tokens); JEV_MAX_* caps apply.
  run --case all --tracks jev_114 --protocol primary --split test --concurrency 1 --run-name test_primary_jev
  run --case all --tracks jev_114 --protocol primary --split calibration --concurrency 4 --run-name calibration_primary_jev
  run --case all --tracks jev_114 --protocol P4_cross_language --split test --concurrency 4 --run-name test_p4_jev
  run --case all --tracks jev_114 --protocol primary --split test --context natural_long --concurrency 4 --run-name test_p7_long_jev
  run --case all --tracks jev_114 --protocol P5_order --split test --concurrency 4 --perm-seeds 1,2 --run-name test_p5_jev
}

step_latency() {
  # Run alone: other CPU/GPU work on the host distorts these numbers.
  "$PY" -m debench.latency_bench --track laya_routed --device "$DEVICE" --pairs 100
  "$PY" -m debench.latency_bench --track laya_routed --device cpu --pairs 10 --concurrency 1,4 --repeats 2 --warmup 10
  if remote_enabled; then
    "$PY" -m debench.latency_bench --track jev_114 --pairs 5 --repeats 1 --warmup 0
  fi
}

step_report() {
  local runs=test_primary_laya,rules_test,test_p4_laya,test_p5_laya,test_p7_long_laya
  local cal=calibration_primary_laya
  if [ -f results/runs/test_primary_jev/predictions.jsonl ]; then
    "$PY" -m debench.renormalize --runs test_primary_jev,calibration_primary_jev,test_p4_jev,test_p5_jev,test_p7_long_jev
    runs=$runs,test_primary_jev,test_p4_jev,test_p5_jev,test_p7_long_jev
    cal=$cal,calibration_primary_jev
  fi
  "$PY" -m debench.analyze --runs "$runs" --calibration-runs "$cal" --out results/analysis/test --local-hourly-usd 0.5,1.0
  "$PY" -m debench.report --analysis results/analysis/test
  "$PY" -m debench.figures --analysis results/analysis/test
  for f in summary.csv summary.parquet predictions.parquet paired_deltas.csv; do
    cp "results/analysis/test/$f" "results/$f"
  done
  cp results/analysis/test/calibration_p6.csv results/calibration.csv
  log "done: docs/FINAL_REPORT.md, docs/figures/, results/summary.csv"
}

case "$STEP" in
  all) step_data; step_pilot; step_laya; step_jev; step_latency; step_report ;;
  data|pilot|laya|jev|latency|report) "step_$STEP" ;;
  *) echo "unknown step: $STEP (use all|data|pilot|laya|jev|latency|report)" >&2; exit 2 ;;
esac
