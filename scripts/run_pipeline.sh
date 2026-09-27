#!/usr/bin/env bash
# The whole capture, in order. Cron runs this monthly; see RUNBOOK.md.
set -uo pipefail
cd "$(dirname "$0")/.."
echo "=== run_pipeline $(date -u +%FT%TZ)"
# start from an up-to-date, clean main; never overwrite local work
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "working tree has local changes; stopping. EXIT=1"; exit 1
fi
git checkout -q main && git pull -q --ff-only || { echo "could not update main. EXIT=1"; exit 1; }
for stage in scripts/pipeline/[0-9][0-9]_*.sh; do
  SECONDS=0
  bash "$stage"; rc=$?
  echo "STAGE=$(basename "$stage" .sh) DURATION=${SECONDS}s EXIT=$rc"
  [ "$rc" -eq 0 ] || { echo "EXIT=$rc"; exit "$rc"; }
done
echo "EXIT=0"
