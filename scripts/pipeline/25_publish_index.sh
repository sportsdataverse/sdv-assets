#!/usr/bin/env bash
# Stage 25: publish sdvplot's team index next to the manifest, for sdvplotR (R reads gzipped CSV, not parquet).
# A version directory is immutable (--ignore-existing); latest.txt, written last, names the current one.
set -euo pipefail
cd "$(dirname "$0")/../.."
OUT="${SDV_ASSETS_INDEX_OUT:-build/index}"
rm -rf "$OUT"
ver=$(uv run --extra index python -m sdv_assets.index "$OUT" | tail -n 1)
current=$(rclone cat sdvspaces:sdv/assets/public/index/latest.txt 2>/dev/null | tr -d '[:space:]' || true)
if [ "$current" = "$ver" ]; then
  echo "index $ver already published"
  exit 0
fi
rclone copy "$OUT/$ver" "sdvspaces:sdv/assets/public/index/$ver" --s3-acl public-read --ignore-existing --stats-one-line
rclone copyto "$OUT/latest.txt" sdvspaces:sdv/assets/public/index/latest.txt --s3-acl public-read
echo "published index $ver"
