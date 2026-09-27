#!/usr/bin/env bash
# Stage 20: upload the captured images and the manifest to DigitalOcean Spaces (rclone remote `sdvspaces`, bucket `sdv`).
# Images are content-addressed, so --ignore-existing never re-uploads one; the public tier is public-read.
set -euo pipefail
STORE="${SDV_ASSETS_STORE:-/mnt/sdv_repos/sdv-assets-store}"
cd "$(dirname "$0")/../.."
rclone copy "$STORE/sha256" sdvspaces:sdv/assets/public/sha256 --s3-acl public-read --ignore-existing --transfers 8 --stats-one-line --stats 30s
rclone copy manifest sdvspaces:sdv/assets/public/manifest --s3-acl public-read --stats-one-line
echo "published $(find "$STORE/sha256" -type f | wc -l) images and the manifest"
