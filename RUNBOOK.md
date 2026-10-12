# Runbook

`scripts/run_pipeline.sh` runs every stage in order. The droplet's crontab runs it monthly (1st, 03:30 UTC),
appending to `logs/pipeline.log`; each stage logs `STAGE=... DURATION=... EXIT=...`, and the run ends with `EXIT=`.

| Stage | Script | Frequency | Idempotency | Typical duration |
|---|---|---|---|---|
| 10 | `scripts/pipeline/10_capture_marks.sh [source ...]` | every run | content-addressed store; manifest merge keeps `first_seen`; validated images aren't decoded again | ~20 min (all sources) |
| 20 | `scripts/pipeline/20_publish_images.sh` | every run | `--ignore-existing` on sha256 paths; manifest overwritten | ~1 min when few images are new |
| 25 | `scripts/pipeline/25_publish_index.sh` | every run | version dirs `--ignore-existing`; unchanged `INDEX_VERSION` skips the upload | seconds |
| 30 | `scripts/pipeline/30_open_manifest_pr.sh` | every run | no manifest change → no PR; an existing dated branch is left alone | seconds |

A failing stage does not stop the run: later stages still execute, each logs its own `EXIT=`, and the run's final `EXIT=` is the first failure's code.

Run one source by hand: `scripts/pipeline/10_capture_marks.sh nhl_catalog`.
Images are written to `$SDV_ASSETS_STORE` (default `/mnt/sdv_repos/sdv-assets-store`) before stage 20 uploads them.
