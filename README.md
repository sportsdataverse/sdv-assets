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
| `league` | `nfl`, `nba`, `wnba`, `mlb`, `nhl`, `cfb`, `mbb`, `wbb`, `ncaa`, `soccer`, `ufl`, `xfl`, `usfl`, `pwhl`, `ahl`, `ohl`, `whl`, `qmjhl`, `ushl`, `echl`, `milb`, `cricket`, `aaf`, `nbagl`, `ncaa_baseball`, `ncaa_softball`, `ncaa_mhockey`, `ncaa_whockey`, `phf`, `nbl`, `fiba`, `iihf`, `sphl`, `chl`, `bchl`, `ajhl`, `sjhl`, `ojhl`, `cchl`, `gojhl`, `mhl`, `nojhl`, `vijhl`, `kijhl`, `mjhl`, `f1`, `cebl`, `easl`, `lba`, `cfl`, `euroleague`, `bnxt`, `aba`, `ibba`, `kzs`, `lkl`, `dbl`, `nbl_au`, `wnbl`, `kbl`, `bleague`, `shl`, `ha`, `magnus`, `elh`, `acb`, `feb`, `bbl`, `dbbl`, `vtb`, `wjbl` |
| `entity_id`, `entity_name` | The source's id and name for the team, group or league |
| `program` | `pro`, `football`, `mens`, `womens` (Tennessee's Lady Vols mark is a `womens` row), `junior` (OHL/WHL/QMJHL/USHL and the junior A leagues), `national` (a `fiba` / `iihf` national team ESPN lists under one id for both genders, or with no gender), and the MiLB level (`aaa`, `aa`, `high_a`, `single_a`, `short_a`, `rookie`) |
| `mark_type`, `variant` | `logo` / `wordmark`; `default`, `dark`, the ESPN brand-set variants, `on_light` / `on_dark`, … |
| `valid_from`, `valid_to` | Seasons the source says the mark was used, where it says so (the NHL catalog, HockeyTech, the NHL playoff bracket, ESPN's XFL/UFL season lists, Lega Basket, BNXT, the IBBA, KZS, B.LEAGUE, ACB, FEB, the SCB API and VTB do); blank otherwise. One row per image, so a file used again after a gap spans the gap |
| `source`, `url` | Where it was captured from |
| `sha256`, `ext`, `bytes`, `width`, `height` | The image (SVGs have no pixel size) |
| `archive_url` | Where the archived copy is served |
| `first_seen`, `last_seen` | Capture dates. When a URL starts serving new bytes, a new row appears and the old row keeps its `last_seen` |

Seasons follow SportsDataverse conventions: the ending year for NHL, NBA, college basketball, LBA, BNXT, IBBA, KZS, B.LEAGUE, ACB, FEB, BBL/DBBL and VTB (as sdv-py keys them), and the single or
starting year elsewhere.

## Soccer competitions

Soccer marks sit in one pooled `soccer` league keyed by ESPN club id. [`manifest/soccer_competitions.csv`](manifest/soccer_competitions.csv)
records which ESPN competitions each club is listed in: one row per competition, season and club. `espn_soccer` reads it
from the same team lists it captures logos from, so it costs no extra requests. Each run adds its rows and keeps the
earlier seasons, so the file builds a history the way `marks.csv` does. A run that skips `espn_soccer` leaves it alone.

| Column | Meaning |
|---|---|
| `competition` | ESPN's league slug: a domestic league (`eng.1`, `usa.1`), a cup (`eng.fa`, `uefa.champions`) or a national-team tournament (`fifa.world`, `fifa.wwc`) |
| `season` | ESPN's season year for that competition when the list was read (`season.year` in the site API; the starting year for an August-May league). Blank if ESPN gives none |
| `team_id` | ESPN club or national-team id, the `entity_id` of the club's `soccer` rows in `marks.csv` |
| `competition_name` | ESPN's name for the competition (`English Premier League`) |

ESPN lists only each competition's current teams, so a season's rows are the clubs listed while that season was
current, not a full membership history.

## Sources

Official sources only:
- ESPN's CDN: team logos with every variant ESPN lists, conference logos including legacy files, league marks, and relocated franchises.
- The NHL's logo catalog: every club and league mark by season range, 1917-18 on.
- The NHL's playoff bracket: the Eastern / Western Conference Final wordmarks (2024 on), filed as the `EAS` / `WES`
  conference marks with variant `conference_final`, the only official NHL conference marks published.
- MLB's CDN: team logos and wordmarks, American and National League marks.
- nflverse's team table: wordmarks and conference/league marks.
- ESPN soccer: every club in every competition ESPN lists (219 on 2026-09-30), with each club's competitions in `manifest/soccer_competitions.csv`.
- ESPN's NBA G League, college baseball, college softball (filed under baseball) and men's/women's college hockey team lists.
  ESPN keeps one id per school across its college sports, so these share images with `cfb`/`mbb`/`wbb`.
- ESPN's NBL (Australia) and national teams, pooled into one league per sport: `fiba` (ESPN's FIBA World Cup and men's /
  women's Olympic basketball lists) and `iihf` (men's and women's Olympic ice hockey). One row per ESPN team id and logo.
- ESPN's XFL (2020, 2023) and UFL (2024 on) team lists by season, captured from ESPN's per-league files (`xfl/500/dal.png`).
  ESPN overwrites a file when a team rebrands or its abbreviation passes to another team, so a file's season range covers
  only the identity that last used it (a logo replaced under the same name and abbreviation cannot be detected).
- Fox: USFL (2022-23) team logos. Fox owned the league.
- HockeyTech, the stats platform the leagues' own sites use: PWHL, AHL, ECHL, SPHL, CHL, OHL, WHL, QMJHL, USHL and the
  junior A leagues (BCHL, AJHL, SJHL, OJHL, CCHL, GOJHL, MHL, NOJHL, VIJHL, KIJHL, MJHL) logos for every regular season.
- International basketball, from the team lists sdv-py's families read and keyed by the same ids:
  - CEBL: the league's own stats API (`api.data.cebl.ca/teams/`), every franchise past and present by CEBL team id,
    with its logo and its icon (variant `icon`).
  - EASL: Sportradar DataCore's team images, keyed by the DataCore team UUID, for the current season's teams. The
    list comes from the unofficial keyless DataCore mirror sdv-py's `easl` family reads; the images are DataCore's.
  - LBA (Lega Basket Serie A): legabasket.it's own API and media bucket, every season from 2020-21 on, keyed by the
    stable `club_id` (LBA's `team_id` changes every season; join it to the club through sdv-py's `lba_teams`).
  - EuroLeague: the league's own API, every club of every season since 2000-01 by club `code`, with its current crest.
  - BNXT: its Sportpress backend, every season from 2021-22, by the stable `uu_team_id`. The season-to-competition
    map (`BNXT_COMPETITIONS`) needs each new season's id.
  - ABA League: the crest file aba-liga.com keeps per club id, for every club on a standings page since 2008-09.
  - Israel (IBBA): basket.co.il's web service, every season from 2014-15, by the club's `team_uid`.
  - Slovenia (KZS): api.kzs.si, the men's first league of the current season, by `club_id`.
  - LKL: the current teams on lkl.lt's results page, by lkl.lt team id.
  - Basketligaen (`dbl`): the Sportality standings of the active season, by Genius team id.
  - NBL (`nbl_au`) and WNBL: the Rosetta team directory behind nbl.com.au, by DataCore team UUID, with DataCore's
    file as variant `datacore` when it differs. ESPN's NBL stays league `nbl` with ESPN ids.
  - KBL: the current clubs by `team_code`, with the SVG kbl.or.kr's stylesheet draws for each club.
  - B.LEAGUE: the standings pages of every season and division since 2016-17, by club `TeamID`.
  - ACB (Liga Endesa): api2.acb.com, every season from 2019-20, by `club_id`, with the negative mark as variant `dark`.
  - FEB (Primera FEB, Segunda FEB, Liga Femenina): the calendar pages of baloncestoenvivo.feb.es from 2020-21, by the
    FEB team id, which is a team-season id (one row per season).
  - BBL and DBBL: the SCB API behind easycredit-bbl.de and toyota-dbbl.de, every season it lists, by SCB team id. Its
    `x-api-secret` is derived on each run from a key the easycredit-bbl.de page embeds, and is never logged.
  - VTB United League: Infobasket's calendar of the current season, by Infobasket `TeamID`.
  - W League (`wjbl`): the latest edition's schedule on api.wjbl.01core.app, by franchise `team_id`.
  - ACB, FEB, BBL/DBBL and W League sites carry terms on reuse, and sdv-py flags VTB as sanctions-sensitive; the owner
    signed off on archiving their marks (2026-11-25).
- European hockey, from the sources sdv-py's families read and keyed by the same ids:
  - SHL: every team since 1975-76 by StatNet team code, with its Sportality icon.
  - HockeyAllsvenskan (`ha`): every season since 2005-06 by `team_statnet_id`.
  - Ligue Magnus: the French federation's team list, every team that entered a Ligue Magnus competition since
    2020-21, by federation team id.
  - Czech Extraliga (`elh`): hokej.cz's logo bucket by hokej.cz team id, for the teams of the last two weeks'
    games (none in the off-season, when the source fails and its rows stay as they are).
- Cricket: ESPN's CDN by ESPNcricinfo team id, plus ESPNcricinfo's own image of each team's mark (variant `cricinfo`),
  for the teams in `curated/cricinfo_teams.csv` (national sides and T20 franchises; ESPN's and Cricinfo's APIs refuse
  the droplet, so the list is curated from Cricinfo pages archived by the Wayback Machine).
- NCAA.com's school logos for college teams ESPN lists with no logo (mostly NCAA Division II/III), matched to NCAA.com's
  school index by exact normalized name, or by a slug pinned in `curated/ncaa_com_slugs.csv`.
- Overwritten official files from their Wayback Machine copies (`curated/wayback_marks.csv`, e.g. the 2020 Seattle Dragons).
- PHF/NWHL (2016-23), from the Wayback Machine's copies because both hosts are gone: the inaugural 2016 marks from the
  league's own 2015-16 site (nwhl.co), and the ShiftStats files for 2017 on, with the seasons fastRhockey-data's schedule
  used each in (`curated/phf_logos.csv`).
- AAF (2019): aaf.com's archived strip of all eight logos, plus eight crops of it marked `source=aaf-strip-crop`,
  `variant=crop_64px` (owner-approved; no separate official files survive; see `curated/aaf/README.md`).
- MLB's CDN for minor-league teams: every team the Stats API lists at any level since 2005 (current marks only).
- formula1.com's media CDN: F1 constructor logos for every season's grid since 2019, keyed by the Jolpica `constructor_id`
  through `curated/f1_constructors.csv` (season, constructor, formula1.com slug; slugs change with rebrands). 2024 on:
  the SVG originals (`default`, white `dark`, black `on_light`); 2019-2023: one 96 px PNG per team. Each row covers its season.
- F1 constructor logos before 2019 (`curated/f1_history.csv`, same Jolpica ids): 2015-2018 from formula1.com's old team
  pages (the logo each season's page drew, a Wayback Machine `id_` copy captured that season, one row per season); 2002-2015
  from Wikimedia Commons files tagged public domain (PD-textlogo / PD-ineligible; each row names its file page and license
  and spans the seasons its description or the team's entry name dates it to). formula1.com's 2003-2014 site had no team logos.
- CFL: the league's own logo service (`content.cfl.ca/logos/{abbr}-{primary,dark}.svg`, the files cfl.ca draws), keyed by
  ESPN team id (79-87; ESPN lists the teams with no logo). SVGs only: the service's PNGs are different artwork. Plus the
  CFL league mark and its French (LCF) version.
- NCAA Division II / III football conference logos (ESPN and NCAA.com serve none), each the conference's own site mark
  pinned to a Wayback Machine `id_` copy and keyed by ESPN's college-football group id (`curated/conference_marks.csv`;
  source `wayback`, league `cfb`, as `espn_groups` keys the FBS / FCS conferences). The same file holds the hockey-only
  college conferences (leagues `ncaa_mhockey` / `ncaa_whockey`), keyed by the sdv-reference-data group id
  (`ncaa_mhockey:hockey-east`).

Non-free Wikipedia files and third-party logo sites (sportslogos.net, Sports Reference) are never captured here.
One named exception: cfbplotR's college wordmarks, most of them sportslogos.net files, are kept here so the marks
cfbplotR already served publicly survive its retirement. `curated/cfbplotr_wordmarks.csv` lists all 366 files and keys 362;
those are captured from a pinned cfbplotR commit and every row carries `source = cfbplotr-legacy`, so the set can be
pulled at once. The 4 unkeyed files (hockey-only schools cfbplotR never drew) are listed but not captured.
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
