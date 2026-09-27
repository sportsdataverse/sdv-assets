#!/usr/bin/env bash
# Stage 30: if the manifest changed, commit it on a dated branch and open a PR against main.
# Idempotent: no change means no branch; an existing branch or PR for the date is left alone.
set -euo pipefail
cd "$(dirname "$0")/../.."
if git diff --quiet -- manifest/; then
  echo "manifest unchanged; no PR"
  exit 0
fi
branch="data/capture-$(date -u +%F)"
if git ls-remote --exit-code --heads origin "$branch" >/dev/null 2>&1; then
  echo "$branch already exists; leaving it"
  exit 0
fi
added=$(git diff --numstat -- manifest/marks.csv | awk '{print $1}')
git checkout -q -b "$branch"
git add manifest/marks.csv manifest/failures.csv
git commit -q -m "data: monthly capture $(date -u +%F)"
git push -q -u origin "$branch"
gh pr create --base main --head "$branch" --title "data: monthly capture $(date -u +%F)" \
  --body "Monthly run of \`scripts/run_pipeline.sh\`: ${added:-0} manifest lines added or changed. New images are already published to Spaces. Review \`manifest/failures.csv\` for sources that stopped serving a mark."
git checkout -q main
