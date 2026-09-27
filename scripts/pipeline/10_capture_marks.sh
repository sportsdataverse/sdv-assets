#!/usr/bin/env bash
# Stage 10: fetch every known official mark, validate it, store it by sha256, update manifest/*.csv.
# Idempotent: images are content-addressed, the manifest merge carries first_seen forward, and images
# an earlier run validated are not decoded again.
set -euo pipefail
cd "$(dirname "$0")/../.."
# ESPN's site API blocks datacenter IPs after heavy traffic; its few JSON calls go through the decodo
# proxy, whose credentials live in ~/.Renviron (read here, never echoed)
if [ -z "${SDV_ASSETS_API_PROXY:-}" ] && [ -f "$HOME/.Renviron" ]; then
  user=$(sed -n 's/^DECODO_USER_NAME=//p' "$HOME/.Renviron" | tr -d "\"'")
  pass=$(sed -n 's/^DECODO_PASSWORD=//p' "$HOME/.Renviron" | tr -d "\"'")
  if [ -n "$user" ] && [ -n "$pass" ]; then
    export SDV_ASSETS_API_PROXY="http://${user}:${pass}@gate.decodo.com:7000"
  fi
fi
export UV_CACHE_DIR="${UV_CACHE_DIR:-/mnt/sdv_repos/.uv-cache}"
uv run python -m sdv_assets.capture "$@"
