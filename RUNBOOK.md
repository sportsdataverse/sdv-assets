# Runbook

`scripts/run_pipeline.sh` runs every stage in order. The droplet's crontab runs it monthly (1st, 03:30 UTC),
appending to `logs/pipeline.log`; each stage logs `STAGE=... DURATION=... EXIT=...`, and the run ends with `EXIT=`.

| Stage | Script | Frequency | Idempotency | Typical duration |
|---|---|---|---|---|
| 10 | `scripts/pipeline/10_capture_marks.sh [source ...]` | every run | content-addressed store; manifest merge keeps `first_seen`; validated images aren't decoded again | ~20 min (all sources) |
| 20 | `scripts/pipeline/20_publish_images.sh` | every run | `--ignore-existing` on sha256 paths; manifest overwritten | ~1 min when few images are new |
| 30 | `scripts/pipeline/30_open_manifest_pr.sh` | every run | no manifest change → no PR; an existing dated branch is left alone | seconds |

Run one source by hand: `scripts/pipeline/10_capture_marks.sh nhl_catalog`.
Images are written to `$SDV_ASSETS_STORE` (default `/mnt/sdv_repos/sdv-assets-store`) before stage 20 uploads them.
