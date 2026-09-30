# sdv-assets

An archive of the official logos and wordmarks used across SportsDataverse leagues: teams, conferences,
divisions, and the leagues themselves.

Official providers overwrite their image files in place (ESPN, NCAA.com and league CDNs all serve only today's mark).
So this archive captures what they serve and keeps every version it has seen, which makes it the only record of a
mark after its source changes.

- **Images** live in DigitalOcean Spaces, content-addressed by sha256:
  `https://sdv.nyc3.cdn.digitaloceanspaces.com/assets/public/sha256/{ab}/{sha256}.{ext}`
- **The manifest**, [`manifest/marks.csv`](manifest/marks.csv), has one row per mark and image.
- **Rejected candidates** are in [`manifest/failures.csv`](manifest/failures.csv): URLs that didn't return a real image.

## Manifest columns

| Column | Meaning |
|---|---|
| `level` | `team`, `division`, `conference`, `league` |
| `league` | `nfl`, `nba`, `wnba`, `mlb`, `nhl`, `cfb`, `mbb`, `wbb`, `ncaa`, `soccer`, `ufl`, `xfl`, `usfl`, `pwhl`, `ahl`, `ohl`, `whl`, `qmjhl`, `milb` |
| `entity_id`, `entity_name` | The source's id and name for the team, group or league |
| `program` | `pro`, `football`, `mens`, `womens` (Tennessee's Lady Vols mark is a `womens` row), `junior` (OHL/WHL/QMJHL), and the MiLB level (`aaa`, `aa`, `high_a`, `single_a`, `short_a`, `rookie`) |
| `mark_type`, `variant` | `logo` / `wordmark`; `default`, `dark`, the ESPN brand-set variants, `on_light` / `on_dark`, … |
| `valid_from`, `valid_to` | Seasons the source says the mark was used, where it says so (the NHL catalog, HockeyTech and ESPN's XFL/UFL season lists do); blank otherwise |
| `source`, `url` | Where it was captured from |
| `sha256`, `ext`, `bytes`, `width`, `height` | The image (SVGs have no pixel size) |
| `archive_url` | Where the archived copy is served |
| `first_seen`, `last_seen` | Capture dates. When a URL starts serving new bytes, a new row appears and the old row keeps its `last_seen` |

Seasons follow SportsDataverse conventions: the ending year for NHL, NBA and college basketball, and the single or
starting year elsewhere.

## Sources

Official sources only:
- ESPN's CDN: team logos with every variant ESPN lists, conference logos including legacy files, league marks, and relocated franchises.
- The NHL's logo catalog: every club and league mark by season range, 1917-18 on.
- MLB's CDN: team logos and wordmarks, American and National League marks.
- nflverse's team table: wordmarks and conference/league marks.
- ESPN soccer: every club in every competition ESPN lists (219 on 2026-09-30).
- ESPN's XFL (2020, 2023) and UFL (2024 on) team lists by season.
- Fox: USFL (2022-23) team logos. Fox owned the league.
- HockeyTech, the stats platform the leagues' own sites use: PWHL, AHL, OHL, WHL and QMJHL logos for every regular season.
- MLB's CDN for minor-league teams: every team the Stats API lists at any level since 2005 (current marks only).

Non-free Wikipedia files and third-party logo sites (sportslogos.net, Sports Reference) are never captured here.
Third-party reference captures, kept only as evidence of when a mark changed, live in the private tier
(`sportsdataverse/sdv-assets-private`) and are never served.

The marks belong to their owners. They are archived for nominative, analytical use in SportsDataverse's plotting
packages, as those packages already display them.

## Running

```sh
uv run python -m sdv_assets.capture                 # every source
uv run python -m sdv_assets.capture nhl_catalog      # one source
scripts/run_pipeline.sh                              # capture, publish, open a manifest PR (see RUNBOOK.md)
uv run pytest
```

Images are written to `$SDV_ASSETS_STORE` (default `/mnt/sdv_repos/sdv-assets-store`) before publishing.
The capture validates each image by its content, never by HTTP status alone: ESPN answers a missing logo with a 1-byte body,
and some URLs serve a different mark than their name suggests.
