"""Where each official mark lives: one function per source, each returning candidate rows.

A row names a mark (level, league, entity, program, mark type, variant, validity window) and the URL
to fetch it from. Capture decides which rows are real by fetching and checking the image.
Seasons follow SDV conventions: ending year for NHL / NBA / MBB / WBB / LBA, single or starting year elsewhere.
"""

from __future__ import annotations

import collections
import csv
import datetime as dt
import html
import io
import os
import re
import time
from pathlib import Path
from urllib.parse import quote

import requests

UA = {"User-Agent": "sdv-assets/0.1 (+https://github.com/sportsdataverse/sdv-assets)"}
ESPN = "https://a.espncdn.com/i/teamlogos"

# (sdv league, ESPN sport, ESPN league, program)
ESPN_LEAGUES = [
    ("nfl", "football", "nfl", "pro"),
    ("nba", "basketball", "nba", "pro"),
    ("wnba", "basketball", "wnba", "pro"),
    ("mlb", "baseball", "mlb", "pro"),
    ("nhl", "hockey", "nhl", "pro"),
    ("cfb", "football", "college-football", "football"),
    ("mbb", "basketball", "mens-college-basketball", "mens"),
    ("wbb", "basketball", "womens-college-basketball", "womens"),
    ("ufl", "football", "ufl", "pro"),
    ("nbagl", "basketball", "nba-development", "pro"),
    # ESPN keeps one id per school across its college sports, so these share images with cfb/mbb/wbb where a school plays both
    ("ncaa_baseball", "baseball", "college-baseball", "mens"),
    ("ncaa_softball", "baseball", "college-softball", "womens"),  # ESPN files college softball under baseball
    ("ncaa_mhockey", "hockey", "mens-college-hockey", "mens"),
    ("ncaa_whockey", "hockey", "womens-college-hockey", "womens"),
    ("nbl", "basketball", "nbl", "pro"),
    # national teams, pooled into one league per sport (named for the sdvplot surface key). ESPN reuses a team id across
    # these lists: basketball/fiba lists whichever FIBA World Cup is current (the women's in 2026, with the same ids as
    # womens-olympics-basketball), and the ice hockey lists share one id per country across genders. espn_teams keeps one
    # row per id; program None takes the program of the other lists, and an id with none or several becomes "national".
    ("fiba", "basketball", "fiba", None),
    ("fiba", "basketball", "mens-olympics-basketball", "mens"),
    ("fiba", "basketball", "womens-olympics-basketball", "womens"),
    ("iihf", "hockey", "olympics-mens-ice-hockey", "national"),
    ("iihf", "hockey", "olympics-womens-ice-hockey", "national"),
]

# Division I programs ESPN's teams lists leave out (see sdvplotR data-raw/generate_logo_ref.R)
ESPN_MISSING_IDS = {
    "cfb": [292, 2598],
    "mbb": [2815, 2511, 2598, 88],
    "wbb": [2385, 2598],
    "ncaa_whockey": [48],  # Delaware
}


def row(
    level,
    league,
    entity_id,
    entity_name,
    url,
    source,
    variant="default",
    mark_type="logo",
    program=None,
    valid_from=None,
    valid_to=None,
):
    return {
        "level": level,
        "league": league,
        "entity_id": str(entity_id),
        "entity_name": entity_name,
        "program": program,
        "mark_type": mark_type,
        "variant": variant,
        "valid_from": valid_from,
        "valid_to": valid_to,
        "source": source,
        "url": url,
    }


def _get_json(session, url, use_proxy=True):
    """JSON API calls. ESPN's site API blocks datacenter IPs after heavy traffic, so these few calls can go
    through a proxy (SDV_ASSETS_API_PROXY, a full proxy URL); image downloads never do. The MLB Stats API
    refuses that proxy (403), so its calls pass ``use_proxy=False``."""
    proxy = os.environ.get("SDV_ASSETS_API_PROXY") if use_proxy and not _PROXY_DOWN else None
    try:
        r = session.get(url, headers=UA, timeout=60, proxies={"http": proxy, "https": proxy} if proxy else None)
    except requests.exceptions.ProxyError as e:
        if not proxy:
            raise
        # an unreachable proxy (its account lapsed, 2026-11-01) must not cost the whole capture: go direct for the run
        print(f"proxy unreachable ({type(e).__name__}); API calls go direct for the rest of this run")
        _PROXY_DOWN.append(True)
        r = session.get(url, headers=UA, timeout=60)
    r.raise_for_status()
    return r.json()


_PROXY_DOWN: list[bool] = []  # set once a proxied call fails to connect


def _variant(rel):
    """ESPN rel lists look like ['full', 'dark'] or ['full', 'primary_logo_on_white_color']."""
    rest = [x for x in rel if x != "full"]
    return "_".join(rest) if rest else "default"


def espn_teams(session):
    """Every logo ESPN lists for every team: default, dark, scoreboard and the 12-variant brand sets. One row per
    sdv league, team id and logo, also when the league pools several ESPN lists (see ESPN_LEAGUES)."""
    pooled = {}  # (league, team id) -> [team, {logo href: logo}, {programs}]
    for league, sport, espn_league, program in ESPN_LEAGUES:
        base = f"https://site.web.api.espn.com/apis/site/v2/sports/{sport}/{espn_league}/teams"
        teams = [
            t["team"]
            for t in _get_json(session, base + "?limit=1000")["sports"][0]["leagues"][
                0
            ]["teams"]
        ]
        seen = {str(t["id"]) for t in teams}
        for tid in ESPN_MISSING_IDS.get(league, []):
            if str(tid) not in seen and (league, str(tid)) not in pooled:
                team = _get_json(session, f"{base}/{tid}").get("team")
                if team:
                    teams.append(team)
        for t in teams:
            entry = pooled.setdefault((league, str(t["id"])), [t, {}, set()])
            for logo in t.get("logos", []):
                entry[1].setdefault(logo["href"], logo)
            if program:
                entry[2].add(program)
    out = []
    for (league, _), (t, logos, programs) in pooled.items():
        program = next(iter(programs)) if len(programs) == 1 else "national"
        for logo in logos.values():
            out.append(
                row(
                    "team",
                    league,
                    t["id"],
                    t.get("displayName"),
                    logo["href"],
                    "espn",
                    variant=_variant(logo.get("rel", [])),
                    program=program,
                )
            )
    return out


def espn_groups(session):
    """College conference logos as ESPN's core API links them (current season), plus the numeric CFB files."""
    out = []
    core = "https://sports.core.api.espn.com/v2/sports"
    yr = dt.date.today().year
    # core groups hang off a season; basketball seasons are named for the year they end
    for league, sport, espn_league, roots, seasons in [
        ("cfb", "football", "college-football", [80, 81], [yr, yr - 1]),
        ("mbb", "basketball", "mens-college-basketball", [50], [yr + 1, yr]),
        ("wbb", "basketball", "womens-college-basketball", [50], [yr + 1, yr]),
    ]:
        for root in roots:
            kids = {}
            for season in seasons:
                try:
                    found = _get_json(
                        session,
                        f"{core}/{sport}/leagues/{espn_league}/seasons/{season}/types/2/groups/{root}/children?limit=100",
                    )
                except requests.HTTPError:
                    continue
                if found.get("items"):
                    kids = found
                    break
            if not kids:
                raise RuntimeError(f"ESPN returned no {league} groups under {root} for seasons {seasons}")
            for item in kids.get("items", []):
                g = _get_json(session, item["$ref"].replace("https://", "http://"))
                for logo in g.get("logos", []):
                    out.append(
                        row(
                            "conference",
                            league,
                            g["id"],
                            g.get("name"),
                            logo["href"],
                            "espn",
                            variant=_variant(logo.get("rel", [])),
                        )
                    )
                if league == "cfb":
                    # the numeric files are keyed by the CFB group id and have dark versions
                    for v, folder in [("default", "500"), ("dark", "500-dark")]:
                        out.append(
                            row(
                                "conference",
                                league,
                                g["id"],
                                g.get("name"),
                                f"{ESPN}/ncaa_conf/{folder}/{g['id']}.png",
                                "espn",
                                variant=v,
                            )
                        )
    return out


# Files ESPN still serves that no API links to (07-historical-logos-pro.md, 08-historical-logos-college.md)
ESPN_STATIC = [
    # league marks
    *[
        ("league", lg, lg.upper(), f"leagues/{f}/{lg}.png", v)
        for lg in ["nfl", "nba", "wnba", "mlb", "nhl"]
        for f, v in [("500", "default"), ("500-dark", "dark")]
    ],
    ("league", "ncaa", "NCAA", "https://a.espncdn.com/i/espn/misc_logos/500/ncaa.png", "default"),
    ("league", "ncaa", "NCAA", "https://a.espncdn.com/i/espn/misc_logos/500-dark/ncaa.png", "dark"),
    (
        "league",
        "ncaa",
        "NCAA football",
        "https://a.espncdn.com/i/espn/misc_logos/500/ncaa_football.png",
        "sport_pictogram",
    ),
    (
        "league",
        "ncaa",
        "NCAA baseball",
        "https://a.espncdn.com/i/espn/misc_logos/500/ncaa_baseball.png",
        "sport_pictogram",
    ),
    # conferences and league halves
    *[
        ("conference", "nfl", c.upper(), f"nfl/{f}/{c}.png", v)
        for c in ["afc", "nfc"]
        for f, v in [("500", "default"), ("500-dark", "dark")]
    ],
    *[
        ("conference", "nba", c.title(), f"nba/{f}/{c}.png", v)
        for c in ["east", "west"]
        for f, v in [("500", "default"), ("500-dark", "dark")]
    ],
    *[
        ("conference", "wnba", c.title(), f"wnba/500/{c}.png", "default")
        for c in ["east", "west"]
    ],
    *[
        ("conference", "mlb", c.upper(), f"mlb/{f}/{c}.png", v)
        for c in ["al", "nl"]
        for f, v in [("500", "default"), ("500-dark", "dark")]
    ],
    # college conference slugs, including legacy files no API links to
    *[
        ("conference", "ncaa", s, f"ncaa_conf/500/{s}.png", "default")
        for s in [
            "mid_continent",
            "trans_america",
            "great_west",
            "caa",
            "colonial",
            "wac",
            "maac",
            "fbs_independents",
        ]
    ],
    # relocated franchises: one frozen logo each
    *[
        ("team", "nfl", t.upper(), f"nfl/{f}/{t}.png", v)
        for t in ["stl", "sd", "oak"]
        for f, v in [("500", "default"), ("500-dark", "dark")]
    ],
    *[
        ("team", "wnba", t.upper(), f"wnba/{f}/{t}.png", v)
        for t in ["hou", "sac", "cha", "det", "tul", "sas", "sa"]
        for f, v in [("500", "default"), ("500-dark", "dark")]
    ],
]


def espn_static(session):
    return [
        row(level, league, entity, entity, path if path.startswith("https://") else f"{ESPN}/{path}", "espn", variant=variant)
        for level, league, entity, path, variant in ESPN_STATIC
    ]


def _nhl_season(season_id):
    """NHL season ids read 19171918; SDV keys the NHL by the ending year."""
    return int(str(season_id)[4:]) if season_id else None


def nhl_catalog(session):
    """The NHL's own logo catalog: every NHL club and league mark by season range, light and dark,
    plus All-Star / exhibition marks (the only official NHL division marks)."""
    out = []
    for r in _get_json(session, "https://records.nhl.com/site/api/logo")["data"]:
        url = r.get("secureUrl") or r.get("url")
        if not url or not ("/logos/nhl/" in url or "/logos/exhib/" in url):
            continue
        name = url.rsplit("/", 1)[-1]
        abbr = name.split("_", 1)[0]
        level = (
            "league"
            if abbr == "NHL"
            else ("division" if "/logos/exhib/" in url else "team")
        )
        out.append(
            row(
                level,
                "nhl",
                r.get("teamId") or abbr,
                abbr,
                url,
                "nhl",
                variant="dark" if r.get("background") == "dark" else "default",
                valid_from=_nhl_season(r.get("startSeason")),
                valid_to=_nhl_season(r.get("endSeason")),
            )
        )
    return out


NHL_BRACKET = "https://api-web.nhle.com/v1/playoff-bracket"
# (bracket series abbreviation, sdv entity id, name). The brackets before 2024 link no conference final mark.
NHL_CONFERENCE_FINALS = [("ECF", "EAS", "Eastern Conference"), ("WCF", "WES", "Western Conference")]


def nhl_playoff_marks(session, first_season=2024):
    """The Eastern and Western Conference Final wordmarks the NHL's playoff bracket links (``assets.nhle.com/logos/
    playoffs/png/ecf-wordmark-en.png``): the only official NHL conference marks, since neither the NHL's logo catalog
    nor ESPN has a standing conference logo. Seasons are the bracket year; a file the bracket links in several seasons
    (``ecf-20232024-wordmark-en.png``, 2024 and 2026) is one row spanning them, as in hockeytech: the manifest has
    one row per image, so the span covers 2025, whose bracket linked ``ecf-wordmark-en.png``."""
    span = {}
    for year in range(first_season, dt.date.today().year + 2):
        try:
            series = _get_json(session, f"{NHL_BRACKET}/{year}").get("series") or []
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                continue  # a season the NHL has no bracket for yet
            raise
        logos = {s.get("seriesAbbrev"): s.get("seriesLogo") for s in series}
        for abbrev, entity, name in NHL_CONFERENCE_FINALS:
            url = logos.get(abbrev)
            if url:
                lo, hi = span.get((entity, name, url), (year, year))
                span[(entity, name, url)] = (min(lo, year), max(hi, year))
    if not span:
        raise RuntimeError("nhl_playoff_marks: no playoff bracket linked a conference final mark")
    return [
        row("conference", "nhl", entity, name, url, "nhl", variant="conference_final", mark_type="wordmark",
            valid_from=lo, valid_to=hi)
        for (entity, name, url), (lo, hi) in span.items()
    ]


MLB_FOLDERS = [
    ("", "logo", "default"),
    ("team-cap-on-light/", "logo", "cap_on_light"),
    ("team-cap-on-dark/", "logo", "cap_on_dark"),
    ("team-primary-on-light/", "logo", "primary_on_light"),
    ("team-primary-on-dark/", "logo", "primary_on_dark"),
    ("team-wordmark-on-light/", "wordmark", "on_light"),
    ("team-wordmark-on-dark/", "wordmark", "on_dark"),
]


def mlbstatic(session):
    """MLB's own CDN: team logos and wordmarks by MLB team id, plus the AL / NL / MLB league marks."""
    # https with a query string answers 406 from datacenter hosts; http works
    teams = _get_json(session, "http://statsapi.mlb.com/api/v1/teams?sportId=1", use_proxy=False)[
        "teams"
    ]
    out = []
    for t in teams:
        for folder, mark_type, variant in MLB_FOLDERS:
            out.append(
                row(
                    "team",
                    "mlb",
                    t["id"],
                    t["name"],
                    f"https://www.mlbstatic.com/team-logos/{folder}{t['id']}.svg",
                    "mlbstatic",
                    variant=variant,
                    mark_type=mark_type,
                )
            )
    for lid, level, name in [
        (103, "conference", "American League"),
        (104, "conference", "National League"),
        (1, "league", "MLB"),
    ]:
        for bg in ["light", "dark"]:
            out.append(
                row(
                    level,
                    "mlb",
                    lid,
                    name,
                    f"https://www.mlbstatic.com/team-logos/league-on-{bg}/{lid}.svg",
                    "mlbstatic",
                    variant=f"on_{bg}",
                )
            )
    return out


# MLB Stats API minor-league levels. 15 (short-season A) ended with the 2021 reorganization; 13 was "Advanced A" before it.
MILB_LEVELS = {11: "aaa", 12: "aa", 13: "high_a", 14: "single_a", 15: "short_a", 16: "rookie"}
# mlbstatic serves only the on-light marks for minor-league teams (the on-dark folders answer 404)
MILB_FOLDERS = [f for f in MLB_FOLDERS if "dark" not in f[0]]


def milb(session, first_season=2005):
    """Minor-league team logos and wordmarks from mlbstatic, for every team id the Stats API lists in any season since
    ``first_season``: teams dropped in the 2021 reorganization still have their last logo there. mlbstatic keeps one
    current mark per team id, so the rows carry no validity window."""
    last = {}
    for season in range(first_season, dt.date.today().year + 1):
        for sport_id, level in MILB_LEVELS.items():
            try:
                teams = _get_json(
                    session, f"http://statsapi.mlb.com/api/v1/teams?sportId={sport_id}&season={season}", use_proxy=False
                )
            except requests.HTTPError as e:
                if e.response is not None and e.response.status_code == 404:
                    continue  # the level did not exist that season (short-season A ended after 2020)
                raise
            for t in teams.get("teams", []):
                last[t["id"]] = (t["name"], level)  # later seasons overwrite: the newest name and level win
    out = []
    for tid, (name, level) in sorted(last.items()):
        for folder, mark_type, variant in MILB_FOLDERS:
            out.append(
                row("team", "milb", tid, name, f"https://www.mlbstatic.com/team-logos/{folder}{tid}.svg", "mlbstatic",
                    variant=variant, mark_type=mark_type, program=level)
            )
    if not out:
        raise RuntimeError("milb: the Stats API listed no minor-league teams")
    return out


def nflverse(session):
    """nflverse's team table: wordmarks, squared logos, and the conference and league marks.
    team_logo_wikipedia is left out: those are non-free Wikipedia files."""
    text = session.get(
        "https://github.com/nflverse/nflverse-data/releases/download/teams/teams_colors_logos.csv",
        headers=UA,
        timeout=60,
    ).text
    out = []
    for t in csv.DictReader(io.StringIO(text)):
        for col, level, mark_type, variant in [
            ("team_wordmark", "team", "wordmark", "default"),
            ("team_logo_squared", "team", "logo", "squared"),
            ("team_conference_logo", "conference", "logo", "default"),
            ("team_league_logo", "league", "logo", "default"),
        ]:
            url = t.get(col)
            if url:
                entity = (
                    t["team_abbr"]
                    if level == "team"
                    else (t["team_conf"] if level == "conference" else "NFL")
                )
                out.append(
                    row(
                        level,
                        "nfl",
                        entity,
                        t.get("team_name") if level == "team" else entity,
                        url,
                        "nflverse",
                        variant=variant,
                        mark_type=mark_type,
                    )
                )
    return out


ESPN_CORE = "https://sports.core.api.espn.com/v2/sports"
ESPN_SITE = "https://site.web.api.espn.com/apis/site/v2/sports"


def _espn_site_league(session, sport, league):
    """A competition's site-API league object (its teams, name and current season); {} when ESPN has no team list
    for it (404)."""
    try:
        data = _get_json(session, f"{ESPN_SITE}/{sport}/{league}/teams?limit=1000")
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            return {}
        raise  # a 403 is the droplet being blocked, not an empty league: fail the source, don't publish a gap
    return ((data.get("sports") or [{}])[0].get("leagues") or [{}])[0]


def _espn_site_teams(session, sport, league):
    """A competition's current teams from ESPN's site API; [] when ESPN has no team list for it (404)."""
    return [t["team"] for t in _espn_site_league(session, sport, league).get("teams", [])]


# Which ESPN soccer competitions each club is listed in, filled by espn_soccer from the team lists it already fetches
# (no extra requests); capture merges it into manifest/soccer_competitions.csv. Values are strings so they compare
# with rows read back from the file.
SOCCER_COMPETITION_FIELDS = ["competition", "season", "team_id", "competition_name"]
soccer_competitions: list[dict] = []


def espn_soccer(session):
    """Every club in every soccer competition ESPN lists (219 on 2026-09-30). A club appears in its league and in
    each cup it plays; capture collapses those repeats to one row per club and logo, and the memberships are kept
    in ``soccer_competitions``."""
    comps = [
        ref["$ref"].split("/leagues/")[1].split("?")[0]
        for ref in _get_json(session, f"{ESPN_CORE}/soccer/leagues?limit=1000")["items"]
    ]
    out, members = [], []
    for comp in comps:
        league = _espn_site_league(session, "soccer", comp)
        season = (league.get("season") or {}).get("year") or league.get("year")
        for t in (x["team"] for x in league.get("teams", [])):
            members.append({"competition": comp, "competition_name": league.get("name"),
                            "season": str(season or ""), "team_id": str(t["id"])})
            for logo in t.get("logos", []):
                out.append(
                    row("team", "soccer", t["id"], t.get("displayName"), logo["href"], "espn",
                        variant=_variant(logo.get("rel", [])))
                )
    if not out:
        raise RuntimeError(f"espn_soccer: {len(comps)} competitions listed but no club logos returned")
    soccer_competitions[:] = members
    return out


# (sdv league, ESPN sport, ESPN league, seasons): per-season team lists from ESPN's core API, for leagues whose
# teams changed from season to season; the site API's current list only shows today's teams.
ESPN_SEASONS = [
    ("xfl", "football", "xfl", [2020, 2023]),
    ("ufl", "football", "ufl", [2024, 2025, 2026]),
]
# Teams a season list leaves out but ESPN still serves by id: (ESPN league, season, team id)
ESPN_SEASON_EXTRA = [("xfl", 2023, 112647)]  # 2023 Arlington Renegades


def _espn_season_files(league, espn_league, tid, name, abbr, y):
    """The per-league files ESPN keeps for a team abbreviation (``xfl/500/dal.png``). The team's ``guid/…/logos``
    URLs are NOT used: one guid serves the team's current image for every season (the 2020 Dallas Renegades guid
    shows the 2024 Arlington logo)."""
    for folder, variant in (("500", "default"), ("500-dark", "dark")):
        yield (league, tid, name, f"{ESPN}/{espn_league}/{folder}/{abbr.lower()}.png", variant, y)


def espn_seasons(session):
    """Each season's team logos from ESPN's per-league abbreviation files, one row per file with the seasons ESPN
    listed that team under it. ESPN overwrites a file in place when an abbreviation passes to a new team
    (``ufl/500/hou.png`` was the Roughnecks in 2024-25 and is the Gamblers from 2026), so a file shared by
    several teams belongs only to the team that used it last."""
    seen = []
    for league, sport, espn_league, seasons in ESPN_SEASONS:
        core = f"{ESPN_CORE}/{sport}/leagues/{espn_league}/seasons"
        for y in seasons:
            refs = [i["$ref"] for i in _get_json(session, f"{core}/{y}/teams?limit=100")["items"]]
            refs += [f"{core}/{y}/teams/{tid}" for lg, sy, tid in ESPN_SEASON_EXTRA if lg == espn_league and sy == y]
            for ref in refs:
                t = _get_json(session, ref)
                if t.get("abbreviation"):
                    seen += _espn_season_files(league, espn_league, str(t["id"]), t.get("displayName"), t["abbreviation"], y)
    # an identity is (team id, name): ESPN keeps one id through a rebrand (the 2024-25 Houston Roughnecks and the 2026
    # Houston Gamblers are one franchise id), and the rebrand overwrote the file just as a new team would
    # (names compare without case or punctuation: ESPN writes "D.C. Defenders" and "DC Defenders", "BattleHawks" and
    # "Battlehawks" for the same identity, while "Seattle Dragons" -> "Seattle Sea Dragons" is a real rebrand)
    span, owner, shown = {}, {}, {}
    for league, tid, name, url, variant, y in seen:
        ident = (tid, re.sub(r"[^a-z0-9]", "", (name or "").lower()))
        lo, hi = span.get((league, ident, url, variant), (y, y))
        span[(league, ident, url, variant)] = (min(lo, y), max(hi, y))
        if y >= owner.get(url, (0, None))[0]:
            owner[url] = (y, ident)
        if y >= shown.get(ident, (0, None))[0]:
            shown[ident] = (y, name)  # label a row with the identity's latest spelling
    return [
        row("team", league, ident[0], shown[ident][1], url, "espn", variant=variant, program="pro",
            valid_from=lo, valid_to=hi)
        for (league, ident, url, variant), (lo, hi) in span.items()
        if owner[url][1] == ident
    ]


# HockeyTech leagues and the public keys their own sites use (the same registry as fastRhockey
# R/hockeytech_leagues.R and sdv-py sportsdataverse/hockeytech/_leagues.py): (sdv league, client code, key, feed, program)
_HT_LS = "https://lscluster.hockeytech.com/feed/index.php"
HOCKEYTECH = [
    ("pwhl", "pwhl", "446521baf8c38984", _HT_LS, "pro"),
    ("ahl", "ahl", "ccb91f29d6744675", _HT_LS, "pro"),
    ("ohl", "ohl", "f1aa699db3d81487", _HT_LS, "junior"),
    ("whl", "whl", "f1aa699db3d81487", _HT_LS, "junior"),
    ("qmjhl", "lhjmq", "f322673b6bcae299", "https://cluster.leaguestat.com/feed/index.php", "junior"),
    ("ushl", "ushl", "e828f89b243dc43f", _HT_LS, "junior"),
    # sdv-py's ECHL key; it returns the same 21 regular seasons (2006-07 on) as e18cfddba0db3b21, the key the ECHL's
    # own site used to send from the browser
    ("echl", "echl", "2c2b89ea7345cae8", _HT_LS, "pro"),
    ("sphl", "sphl", "8fa10d218c49ec96", _HT_LS, "pro"),
    ("chl", "chl", "ef96ea7d71574f2a", _HT_LS, "pro"),
    ("bchl", "bchl", "f3ed30007ad2124e", _HT_LS, "junior"),
    ("ajhl", "ajhl", "cbe60a1d91c44ade", _HT_LS, "junior"),
    ("sjhl", "sjhl", "2fb5c2e84bf3e4a8", _HT_LS, "junior"),
    ("ojhl", "ojhl", "cce66dd6bebf4790", _HT_LS, "junior"),
    ("cchl", "cchl", "b370f3e6c805baf3", _HT_LS, "junior"),
    ("gojhl", "gojhl", "34b10d4d34d7b59a", _HT_LS, "junior"),
    ("mhl", "mhl", "4a948e7faf5ee58d", _HT_LS, "junior"),
    ("nojhl", "nojhl", "c1375ff55168bd71", _HT_LS, "junior"),
    ("vijhl", "vijhl", "4f1a61df18906b61", _HT_LS, "junior"),
    ("kijhl", "kijhl", "2589e0f644b1bb71", _HT_LS, "junior"),
    ("mjhl", "mjhl", "f894c324fe5fd8f0", _HT_LS, "junior"),
]


def _hockey_season(name):
    """'2024-25 Regular Season' -> 2025 (hockey seasons are keyed by their ending year); '2024 Regular Season' -> 2024.
    Some junior leagues prefix their code and write the years other ways: 'CCHL 2026-27', 'OJHL 2019/2020', '26-27
    Regular Season'. 'CCHL Preseason 2026' and '19 Tie Break' stay None."""
    m = re.match(r"(?:[A-Z]+ )?(?:(\d{4})([-/]\d{2,4})?|\d{2}-(\d{2})\b)", name)
    if not m:
        return None
    if m.group(3):  # two-digit years name the ending year directly
        end = int(m.group(3))
        return end + (1900 if end > 50 else 2000)
    return int(m.group(1)) + (1 if m.group(2) else 0)


def hockeytech(session):
    """Every team's logo in every regular season HockeyTech lists. Most seasons have their own file
    (``logos/{team}_{season}.png``), so this is the logo history, not just today's marks; a file reused across
    seasons becomes one row spanning them."""
    span = {}
    for league, client, key, feed, program in HOCKEYTECH:
        base = f"{feed}?feed=modulekit&key={key}&client_code={client}&fmt=json"
        seasons = _get_json(session, f"{base}&view=seasons")["SiteKit"]["Seasons"]
        regular = [s for s in seasons if s.get("career") == "1" and s.get("playoff") == "0"]
        if not regular:
            raise RuntimeError(f"hockeytech: {league} lists no regular seasons")
        for s in regular:
            year = _hockey_season(s["season_name"])
            teams = _get_json(session, f"{base}&view=teamsbyseason&season_id={s['season_id']}")["SiteKit"]
            for t in teams.get("Teamsbyseason") or []:
                if not t.get("team_logo_url") or year is None:
                    continue
                # some leagues reuse one file across seasons (logos/{team}.png): one row, the full range of seasons
                k = (league, program, str(t["id"]), t["team_logo_url"])
                lo, hi, name = span.get(k, (year, year, t.get("name")))
                span[k] = (min(lo, year), max(hi, year), name)
    return [
        row("team", league, tid, name, url, "hockeytech", program=program, valid_from=lo, valid_to=hi)
        for (league, program, tid, url), (lo, hi, name) in span.items()
    ]


# Fox's public data key (the one foxsports.com uses; sdv-py _fox_layout.DATA_KEY). Override with SDV_ASSETS_FOX_KEY.
FOX_KEY = os.environ.get("SDV_ASSETS_FOX_KEY", "jE7yBJVRNAwdDesMgTzTXUUSx1It41Fq")
# USFL (2022-23, owned by Fox) teams Fox's list no longer carries: (name, logo file stem)
USFL_EXTRA = [("Tampa Bay Bandits", "Bandits")]


def _fox_original(url):
    """Fox logo URLs point at a resized copy (``Stallions.vresize.200.200.medium.1.png``); the original sits beside it."""
    return re.sub(r"\.vresize\.[^/]*(\.png)$", r"\1", url)


def fox_usfl(session):
    """USFL team logos from Fox, which owned and broadcast the league (ESPN never carried it)."""
    data = _get_json(session, f"https://api.foxsports.com/bifrost/v1/usfl/league/teams?apikey={FOX_KEY}&api-version=1.1")
    out = []
    for item in (i for g in data["groups"] for i in g.get("items", [])):
        slug = item["entityLink"]["webUrl"].rstrip("/").split("/")[-1].removesuffix("-team")
        urls = {"default": item.get("logoUrl"), "alternate": item.get("alternateLogoUrl")}
        urls = {v: _fox_original(u) for v, u in urls.items() if u}
        if urls.get("alternate") == urls.get("default"):
            del urls["alternate"]  # Fox repeats the default for teams with no alternate mark
        for variant, url in urls.items():
            out.append(row("team", "usfl", slug, item["title"], url, "fox", variant=variant, program="pro"))
    for name, stem in USFL_EXTRA:
        out.append(row("team", "usfl", name.lower().replace(" ", "-"), name,
                       f"https://b.fssta.com/uploads/application/usfl/team-logos/{stem}.png", "fox", program="pro"))
    return out


CURATED = Path(__file__).resolve().parent.parent / "curated"
CRICINFO_IMG = "https://img1.hscicdn.com/image/upload"


def espn_cricket(session):
    """Cricket team logos ESPN serves by ESPNcricinfo team id (``cricket/500/{id}.png``): national sides and the
    major T20 franchises. ESPN's cricket API and Cricinfo's own API refuse the droplet, so the ids come from
    ``curated/cricinfo_teams.csv``, built from ESPNcricinfo's team index and series pages as the Wayback Machine
    archived them (2026-09-30). Add a row when a new franchise starts."""
    out = []
    with open(CURATED / "cricinfo_teams.csv", newline="") as f:
        for t in csv.DictReader(f):
            program = "womens" if "women" in t["slug"] else "mens"
            out.append(
                row("team", "cricket", t["cricinfo_id"], t["name"], f"{ESPN}/cricket/500/{t['cricinfo_id']}.png", "espn",
                    program=program)
            )
            # ESPNcricinfo's own image of the team's mark (a flag for national sides); it covers franchises ESPN's CDN
            # lacks (all of Major League Cricket)
            if t.get("cricinfo_logo"):
                out.append(
                    row("team", "cricket", t["cricinfo_id"], t["name"], CRICINFO_IMG + t["cricinfo_logo"], "cricinfo",
                        variant="cricinfo", program=program)
                )
    return out


# The commit holding curated/aaf/*.png; raw URLs pinned to it stay valid after later commits
AAF_CROPS_COMMIT = "67cd398edb1f3e6102581367015a7b3fea354e1d"
AAF_STRIP = "https://web.archive.org/web/2019id_/https://aaf.com/images/TeamLogos.png"


def aaf_strip(session):
    """AAF (2019): the only official artwork left is aaf.com's 836x64 strip of all eight logos (see curated/aaf/README.md).
    The untouched strip is captured as the league's row; the eight owner-approved crops of it are marked
    ``source=aaf-strip-crop`` / ``variant=crop_64px`` so they are never mistaken for original files."""
    out = [row("league", "aaf", "aaf", "Alliance of American Football", AAF_STRIP, "aaf.com", variant="team_strip",
               valid_from=2019, valid_to=2019)]
    raw = f"https://raw.githubusercontent.com/sportsdataverse/sdv-assets/{AAF_CROPS_COMMIT}/curated/aaf"
    with open(CURATED / "aaf" / "crops.csv", newline="") as f:
        for t in csv.DictReader(f):
            name = " ".join(w.capitalize() for w in t["slug"].split("-"))
            out.append(
                row("team", "aaf", t["pff_franchise_id"], name, f"{raw}/{t['slug']}.png", "aaf-strip-crop",
                    variant="crop_64px", program="pro", valid_from=2019, valid_to=2019)
            )
    return out


def phf_wayback(session):
    """PHF/NWHL (2016-2023) logos, each the Wayback Machine's copy (``id_`` = the original bytes) because both hosts are
    gone: the league's 2015-16 site nwhl.co (the inaugural 2016 marks) and img.shiftstats.com, its stats vendor (2017 on,
    with the seasons fastRhockey-data's schedule used each file in). See ``curated/phf_logos.csv``."""
    out = []
    with open(CURATED / "phf_logos.csv", newline="") as f:
        for t in csv.DictReader(f):
            out.append(
                row("team", "phf", t["logo_id"], t["name"], t["wayback"], t["source"], variant=t["variant"],
                    mark_type=t["mark_type"], program="pro",
                    valid_from=int(t["valid_from"]) if t["valid_from"] else None,
                    valid_to=int(t["valid_to"]) if t["valid_to"] else None)
            )
    return out


# AP-style state and word abbreviations NCAA.com uses in school names ("Fla. Southern", "Southern N.H.", "American Int'l")
_AP = {"ala": "alabama", "ariz": "arizona", "ark": "arkansas", "calif": "california", "colo": "colorado", "conn": "connecticut",
       "del": "delaware", "fla": "florida", "ga": "georgia", "ill": "illinois", "ind": "indiana", "kan": "kansas", "ky": "kentucky",
       "la": "louisiana", "md": "maryland", "mass": "massachusetts", "me": "maine", "mich": "michigan", "minn": "minnesota",
       "miss": "mississippi", "mo": "missouri", "mont": "montana", "neb": "nebraska", "nev": "nevada", "nh": "newhampshire",
       "nj": "newjersey", "nm": "newmexico", "ny": "newyork", "nc": "northcarolina", "nd": "northdakota", "okla": "oklahoma",
       "ore": "oregon", "pa": "pennsylvania", "ri": "rhodeisland", "sc": "southcarolina", "sd": "southdakota", "tenn": "tennessee",
       "tex": "texas", "vt": "vermont", "va": "virginia", "wash": "washington", "wva": "westvirginia", "wis": "wisconsin",
       "wyo": "wyoming", "intl": "international", "univ": "university", "mt": "mount"}


def _school_key(name):
    """A school name reduced for exact matching: case, punctuation, AP abbreviations and State/St./Saint folded."""
    s = name.lower().replace("&", " and ").replace("'", "").replace("saint ", "st ")
    s = re.sub(r"\b([a-z])\.\s*([a-z])\.", r"\1\2", s)  # "N.H." -> "nh"
    words = [_AP.get(w, w) for w in re.split(r"[^a-z0-9]+", s) if w]
    return "".join("st" if w in ("state", "st", "saint") else w for w in words)


def _ncaa_slug(location, index):
    """The NCAA.com slug for an ESPN team location, or None. Exact key match only (no fuzzy matching: "Bethany (KS)"
    and "Bethany (WV)" are different schools); a trailing University/College is dropped only if that still matches
    exactly one school."""
    for name in (location, re.sub(r"\s+(university|college)$", "", location.strip(), flags=re.I)):
        hits = index.get(_school_key(name), set())
        if len(hits) == 1:
            return next(iter(hits))
        if hits:
            return None
    return None


# College lists whose logo-less teams NCAA.com can cover: (sdv league, ESPN sport, ESPN league, program)
NCAA_COM_LEAGUES = [
    ("cfb", "football", "college-football", "football"),
    ("ncaa_baseball", "baseball", "college-baseball", "mens"),
    ("ncaa_softball", "baseball", "college-softball", "womens"),
    ("ncaa_mhockey", "hockey", "mens-college-hockey", "mens"),
    ("ncaa_whockey", "hockey", "womens-college-hockey", "womens"),
    ("soccer", "soccer", "usa.ncaa.m.1", None),
    ("soccer", "soccer", "usa.ncaa.w.1", None),
]


def _ncaa_com_overrides():
    """(sdv league, ESPN team id) -> (NCAA.com slug, ESPN name) for schools the exact name match can't place, or that
    ESPN's current team list leaves out (``curated/ncaa_com_slugs.csv``; each slug checked to serve a logo)."""
    with open(CURATED / "ncaa_com_slugs.csv", newline="") as f:
        return {(t["league"], t["espn_id"]): (t["slug"], t["name"]) for t in csv.DictReader(f)}


def _ncaa_com_url(slug):
    return f"https://www.ncaa.com/sites/default/files/images/logos/schools/bgl/{slug}.svg"


def ncaa_com(session):
    """NCAA.com's school logo (``logos/schools/bgl/{slug}.svg``) for college teams ESPN lists with no logo at all, mostly
    NCAA Division II and III programs. Keyed by the ESPN team id so the row sits beside that league's ESPN rows. NAIA,
    junior-college and Canadian teams are not NCAA members and stay uncovered."""
    index, page, last = collections.defaultdict(set), 0, 0
    while page <= last:
        r = session.get(f"https://www.ncaa.com/schools-index/{page}", headers=UA, timeout=60)
        r.raise_for_status()
        last = max([last] + [int(p) for p in re.findall(r"/schools-index/(\d+)", r.text)])
        for slug, name in re.findall(r'href="/schools/([a-z0-9-]+)"[^>]*>([^<]{2,80})<', r.text):
            index[_school_key(name)].add(slug)
        page += 1
    if len(index) < 500:
        raise RuntimeError(f"ncaa_com: the NCAA.com school index returned only {len(index)} schools")
    overrides, out, listed = _ncaa_com_overrides(), [], set()
    for league, sport, espn_league, program in NCAA_COM_LEAGUES:
        for t in _espn_site_teams(session, sport, espn_league):
            listed.add((league, str(t["id"])))
            if t.get("logos"):
                continue
            # a curated slug first, for the schools whose ESPN name the exact match can't place
            slug = overrides.get((league, str(t["id"])), (None,))[0] or _ncaa_slug(
                t.get("location") or t.get("displayName") or "", index)
            if slug:
                out.append(row("team", league, t["id"], t.get("displayName"), _ncaa_com_url(slug), "ncaa.com", program=program))
    # curated teams ESPN's current list leaves out (it still serves them by id, with no logo)
    programs = {league: program for league, _, _, program in NCAA_COM_LEAGUES}
    for (league, tid), (slug, name) in overrides.items():
        if (league, tid) not in listed:
            out.append(row("team", league, tid, name, _ncaa_com_url(slug), "ncaa.com", program=programs[league]))
    return out


def wayback_marks(session):
    """Official files that were overwritten or taken down, from the Wayback Machine's ``id_`` copy of the original URL
    (``curated/wayback_marks.csv``: one row per file, each checked by eye, with why the live URL no longer serves it)."""
    out = []
    with open(CURATED / "wayback_marks.csv", newline="") as f:
        for t in csv.DictReader(f):
            out.append(
                row("team", t["league"], t["entity_id"], t["name"], t["wayback"], "wayback", variant=t["variant"], program="pro",
                    valid_from=int(t["valid_from"]) if t["valid_from"] else None,
                    valid_to=int(t["valid_to"]) if t["valid_to"] else None)
            )
    return out


# CEBL's own stats API (the CEBL Stats Hub's backend). Its key is public, hard-coded in the hub's page bundle; this is
# sdv-py's FALLBACK_KEY (sportsdataverse/basketball/cebl/cebl_runtime.py). Override with SDV_ASSETS_CEBL_KEY.
CEBL_API = "https://api.data.cebl.ca"
CEBL_HUB = "https://cebl-stats-hub.web.app"
CEBL_KEY = os.environ.get("SDV_ASSETS_CEBL_KEY", "800chyzv2hvur3z0ogh39cve2zok0c")


def cebl(session):
    """CEBL team marks from the league's own API, keyed by CEBL's team id (sdv-py ``cebl_teams`` ``team_id``). Every
    franchise it lists, past ones included (a relocated franchise has two ids), with its logo and its icon (variant
    ``icon``). A defunct team whose icon is a current team's file (Fraser Valley shows Vancouver's) keeps only its logo."""
    r = session.get(f"{CEBL_API}/teams/", timeout=60,
                    headers={**UA, "X-Api-Key": CEBL_KEY, "Origin": CEBL_HUB, "Referer": CEBL_HUB + "/"})
    r.raise_for_status()
    teams = r.json()
    active_icons = {t.get("icon_url") for t in teams if t.get("is_active")}
    out = []
    for t in teams:
        if t.get("logo_url"):
            out.append(row("team", "cebl", t["id"], t.get("name_en"), t["logo_url"], "cebl", program="pro"))
        if t.get("icon_url") and (t.get("is_active") or t["icon_url"] not in active_icons):
            out.append(row("team", "cebl", t["id"], t.get("name_en"), t["icon_url"], "cebl", variant="icon", program="pro"))
    if not out:
        raise RuntimeError("cebl: the team list carried no logos")
    return out


# EASL has no open first-party feed. sdv-py reads an unofficial keyless mirror of Sportradar DataCore (organisation
# b1e0z); the logo URLs it carries point at DataCore's own image CDN.
EASL_API = "https://easl.joshricha.com/api/v1"


def easl(session):
    """EASL team logos (DataCore's image of each team), keyed by the DataCore team UUID (sdv-py ``easl_teams``
    ``team_id``). The list holds the current season's teams only."""
    out = [
        row("team", "easl", t["teamId"], t.get("nameFullLocal"), t["logo"], "datacore", program="pro")
        for t in _get_json(session, f"{EASL_API}/teams", use_proxy=False)
        if t.get("logo")
    ]
    if not out:
        raise RuntimeError("easl: the team list carried no logos")
    return out


LBA_API = "https://www.legabasket.it/api/teams/get-teams"
LBA_FIRST = 2020  # starting year of the first season (2020-21) whose teams carry a logo_key
LBA_DELAY = 2.0  # legabasket.it's robots.txt sets Crawl-delay: 2


def lba(session):
    """Lega Basket Serie A logos from the site's own API, every season from 2020-21 on, keyed by the club id (sdv-py
    ``lba_teams`` ``club_id``; its ``team_id`` is re-issued every season, so game rows reach the club through
    ``lba_teams``). The API sends a season's starting year; ``valid_from`` / ``valid_to`` are SDV ending years, and a
    file used in several seasons is one row spanning them."""
    span = {}
    for year in range(LBA_FIRST, dt.date.today().year + 1):
        if year > LBA_FIRST:
            time.sleep(LBA_DELAY)
        data = _get_json(session, f"{LBA_API}?items=50&year={year}", use_proxy=False)
        cdn = data.get("cdn_url") or "https://lba-media.s3.eu-south-1.amazonaws.com"
        for t in data.get("teams") or []:
            # a year the site has no teams for yet may answer with its default season instead
            if t.get("logo_key") and t.get("club_id") and t.get("year") == year:
                k = (str(t["club_id"]), f"{cdn}/{t['logo_key']}")
                lo, _, _ = span.get(k, (year + 1, None, None))
                span[k] = (lo, year + 1, t.get("name"))  # the latest season's name
    if not span:
        raise RuntimeError("lba: no season carried a team logo")
    return [
        row("team", "lba", cid, name, url, "legabasket", program="pro", valid_from=lo, valid_to=hi)
        for (cid, url), (lo, hi, name) in span.items()
    ]


# EuroLeague's Competition Engine API (keyless; without Accept: application/json it answers XML). Every season's club
# list carries each club's CURRENT crest (the same file for a club in E2000 and E2025), so seasons only add clubs.
EUROLEAGUE_API = "https://api-live.euroleague.net/v2/competitions/E"
EUROLEAGUE_DELAY = 2.0


def euroleague(session):
    """EuroLeague club crests from the league's own API, keyed by the club code (sdv-py ``euroleague_clubs`` ``code``,
    the ``local_club_code`` / ``road_club_code`` of its game rows). Every club of every season the API lists, under
    its latest season's name. No robots.txt on the host (404)."""
    hdr = {**UA, "Accept": "application/json"}
    r = session.get(f"{EUROLEAGUE_API}/seasons", headers=hdr, timeout=60)
    r.raise_for_status()
    today = dt.date.today().isoformat()
    seasons = sorted(s["code"] for s in r.json().get("data") or [] if (s.get("activationDate") or "")[:10] <= today)
    clubs = {}
    for code in seasons:  # E2000 ... E2026: ascending, so the latest name wins
        time.sleep(EUROLEAGUE_DELAY)
        r = session.get(f"{EUROLEAGUE_API}/seasons/{code}/clubs", headers=hdr, timeout=60)
        r.raise_for_status()
        for c in r.json().get("data") or []:
            if (c.get("images") or {}).get("crest") and c.get("code"):
                clubs[c["code"]] = (c.get("name"), c["images"]["crest"])
    if not clubs:
        raise RuntimeError("euroleague: no season carried a club crest")
    return [
        row("team", "euroleague", code, name, url, "euroleague", program="pro") for code, (name, url) in clubs.items()
    ]


# BNXT's Sportpress backend. Its X-Authorization key is public (bnxtleague.com's bundle); this is sdv-py's
# FALLBACK_KEY (sportsdataverse/basketball/bnxt/bnxt_runtime.py). robots.txt allows everything.
BNXT_API = "https://bnxt.sportpress.info/api/v1/competition-team/all"
BNXT_KEY = os.environ.get("SDV_ASSETS_BNXT_KEY", "BWSyE7sgg9QAurh2JX9cpjzjGc652BWLuNUS")
# SDV END year -> competition id (one per season; sdv-py sportpress_phases maps them). 2020-21 (2) answers no teams.
# ponytail: add each new season's id here (sportpress_phases(season)); a phase lookup per season if it gets tedious.
BNXT_COMPETITIONS = {2022: 1, 2023: 3, 2024: 12, 2025: 22, 2026: 24, 2027: 25}
BNXT_DELAY = 2.0


def bnxt(session):
    """BNXT League logos from its Sportpress backend, keyed by the stable club key ``uu_team_id`` (sdv-py
    ``sportpress_competition_teams`` ``uu_team_id``; its ``competition_team_id`` is new every season, and a club can
    have a second entry for the playoffs). ``valid_from`` / ``valid_to`` are SDV END years; a file used in several
    seasons is one row spanning them."""
    hdr = {
        **UA,
        "X-Authorization": BNXT_KEY,
        "X-Localization": "en",
        "Accept": "application/json",
    }
    span = {}
    for i, (year, cid) in enumerate(sorted(BNXT_COMPETITIONS.items())):
        if i:
            time.sleep(BNXT_DELAY)
        r = session.get(f"{BNXT_API}?competition_id={cid}", headers=hdr, timeout=60)
        r.raise_for_status()
        for t in r.json().get("data") or []:
            url = (t.get("team_logo") or {}).get("public_url")
            if url and t.get("uu_team_id") and t.get("competition_id") == cid:
                k = (str(t["uu_team_id"]), url)
                lo = span.get(k, (year,))[0]
                span[k] = (lo, year, t.get("name"))
    if not span:
        raise RuntimeError("bnxt: no season carried a team logo")
    return [
        row(
            "team",
            "bnxt",
            uid,
            name,
            url,
            "sportpress",
            program="pro",
            valid_from=lo,
            valid_to=hi,
        )
        for (uid, url), (lo, hi, name) in span.items()
    ]


PAGE_RETRY_DELAY = 10.0


def _page(session, url):
    """One HTML page of a site with no team-list API, or None when it has no such page (404) or keeps failing (5xx).
    A dropped connection or a 5xx is tried once more after a pause; a 403 / 429 raises, so the source stops."""
    for attempt in (0, 1):
        try:
            r = session.get(url, headers=UA, timeout=60)
        except requests.ConnectionError:
            if attempt:
                raise
        else:
            if r.status_code == 404 or (attempt and r.status_code >= 500):
                return None
            if r.status_code < 500:
                r.raise_for_status()
                return r.text
        time.sleep(PAGE_RETRY_DELAY)
    return None


# aba-liga.com has no team-list API: sdv-py's aba_standings reads /standings/{sez}/1/ (sez = END year - 2001). The
# club id is global (every season, both leagues) and its crest is /images/club/100x100/{id}.png, the largest size the
# site's pages use (50x50 too). robots.txt allows /standings/ and /images/ and sets Crawl-delay: 2.
ABA_SITE = "https://www.aba-liga.com"
ABA_FIRST = 2009  # END year of the earliest standings page probed (2008-09); the league began in 2001-02
ABA_DELAY = 2.0


def aba(session):
    """ABA League crests, keyed by the ABA club id (sdv-py ``aba_standings`` / ``aba_box_teams`` ``team_id``). Every
    club on a standings page since 2008-09, under its latest season's name. The crest is the club's current one (no
    season in the file name), so rows carry no validity years."""
    clubs = {}
    for year in range(ABA_FIRST, dt.date.today().year + 2):
        if year > ABA_FIRST:
            time.sleep(ABA_DELAY)
        sez = year - 2001
        page = _page(session, f"{ABA_SITE}/standings/{sez}/1/")
        for tid, name in re.findall(rf'/team/(\d+)/{sez}/1/0/[\w-]+/">\s*([^<]+?)\s*<', page or ""):
            clubs[tid] = name
    if not clubs:
        raise RuntimeError("aba: no standings page listed a club")
    return [
        row(
            "team",
            "aba",
            tid,
            name,
            f"{ABA_SITE}/images/club/100x100/{tid}.png",
            "abaliga",
            program="pro",
        )
        for tid, name in clubs.items()
    ]


# Israel Basketball Association's ASP.NET web service (sdv-py ``ibba_*``); robots.txt only disallows /SiteNew/,
# /mgmt/ and /digital library/. ``cYear`` is the season END year. sdv-py spaces requests 1 s apart.
IBBA_API = "https://basket.co.il/ws/ws.asmx/Teams"
IBBA_SITE = "https://basket.co.il"
IBBA_FIRST = 2015  # END year of the oldest season probed (2014-15: 12 of 12 teams carry team_icon2)
IBBA_DELAY = 2.0


def ibba(session):
    """Israeli Premier League (Winner League) logos from the IBBA web service, every season from 2014-15 on, keyed
    by the club id (sdv-py ``ibba_teams`` ``team_uid``; its ``team_id`` is a per-season registration, re-issued every
    season, so game rows reach the club through ``ibba_teams``). ``valid_from`` / ``valid_to`` are END years (the
    host's own ``cYear``); a file used in several seasons is one row spanning them. Logo paths are absolute or
    site-relative and mix ``www.basket.co.il`` (which answers 301) with the bare host; both are normalised."""
    span = {}
    for year in range(IBBA_FIRST, _end_year_now() + 1):
        if year > IBBA_FIRST:
            time.sleep(IBBA_DELAY)
        for t in _get_json(session, f"{IBBA_API}?cYear={year}&board_id=5", use_proxy=False).get("teams") or []:
            icon = (t.get("team_icon2") or "").strip()
            # a season the host has no teams for yet must not answer with another season's rows
            if icon and t.get("TeamUID") and t.get("year_id") == str(year):
                url = re.sub(
                    r"^https?://(www\.)?basket\.co\.il",
                    IBBA_SITE,
                    icon if "://" in icon else IBBA_SITE + icon,
                )
                k = (str(int(t["TeamUID"])), url)
                lo, _, _ = span.get(k, (year, None, None))
                span[k] = (
                    lo,
                    year,
                    html.unescape(t.get("teamEng") or "") or None,
                )  # the latest season's name
    if not span:
        raise RuntimeError("ibba: no season carried a team logo")
    return [
        row(
            "team",
            "ibba",
            uid,
            name,
            url,
            "ibba",
            program="pro",
            valid_from=lo,
            valid_to=hi,
        )
        for (uid, url), (lo, hi, name) in span.items()
    ]


# Basketball Federation of Slovenia's public API (sdv-py ``kzs_*``; api.kzs.si/robots.txt answers 401, no rules).
# A team carries a ``logoUuid``; www.kzs.si's team-logo component (Nuxt chunk, FMS_STORAGE) serves it from
# ``{FMS_STORAGE}/public/images/{uuid}``. Season ids are opaque: id = END year - 2000 for 2001-2030.
KZS_API = "https://api.kzs.si/api/v1/public"
KZS_IMAGES = "https://api.kzs.si/public/images"
KZS_FIRST = 2027  # END year of the season probed (2026/2027 = season id 27)
KZS_DELAY = 2.0


def kzs(session):
    """Slovenian men's first league (Liga OTP banka / Liga Nova KBM) logos, keyed by the KZS club id (sdv-py
    ``kzs_teams`` ``club_id``; its ``team_id`` is one club team in one season). Each season's league is the
    competition typed LEAGUE / ABSOLUTE / FIRST / MALE (its sponsor name changes). ``valid_from`` / ``valid_to`` are
    END years; a file used in several seasons is one row spanning them."""
    span = {}
    league = ("LEAGUE", "ABSOLUTE", "FIRST", "MALE")
    for year in range(KZS_FIRST, min(_end_year_now(), 2030) + 1):  # season ids past 2030 wrap to the 1990s
        listed = _kzs_data(session, f"/competitions/?seasonId={year - 2000}&limit=10000") or {}
        comps = [
            c["id"]
            for c in listed.get("items") or []
            if (c.get("type"), c.get("category"), c.get("rank"), c.get("gender")) == league
        ]
        for cid in comps:
            for t in (_kzs_data(session, f"/teams/?limit=1000&competitionId={cid}") or {}).get("items") or []:
                app = t.get("teamApplication") or {}
                club = (app.get("club") or {}).get("id")
                if t.get("logoUuid") and club:
                    k = (str(int(club)), f"{KZS_IMAGES}/{t['logoUuid']}")
                    lo, _, _ = span.get(k, (year, None, None))
                    span[k] = (lo, year, app.get("name"))
    if not span:
        raise RuntimeError("kzs: no season's first league carried a team logo")
    return [
        row(
            "team",
            "kzs",
            club,
            name,
            url,
            "kzs",
            program="pro",
            valid_from=lo,
            valid_to=hi,
        )
        for (club, url), (lo, hi, name) in span.items()
    ]


def _end_year_now():
    """END year of the season under way (a season starts in August)."""
    today = dt.date.today()
    return today.year + (today.month >= 8)


def _kzs_data(session, path):
    """One KZS call (every answer is ``{"status": "OK", "data": ...}``), spaced like sdv-py's runtime."""
    time.sleep(KZS_DELAY)
    body = _get_json(session, KZS_API + path, use_proxy=False)
    if body.get("status") != "OK":
        raise RuntimeError(f"kzs: {path} answered status {body.get('status')!r}")
    return body.get("data")


# lkl.lt has no team-list API. Its results page (robots.txt: ``Disallow:`` empty) carries the team filter select,
# whose values are lkl.lt's team ids (6 = Rytas, the id the game page's teams-info gives), and every result row's
# team link with the club's logo. Current teams only.
LKL_PAGE = "https://lkl.lt/rezultatai"
LKL_SITE = "https://lkl.lt"


def lkl(session):
    """LKL (Lithuania) logos of the current season's teams, keyed by lkl.lt's club id (sdv-py ``lkl_*`` ``team_id``,
    stable across seasons and renamings). The id comes from the results page's LKL team select and the logo from the
    page's team links, joined on the team name both print."""
    r = session.get(LKL_PAGE, headers=UA, timeout=60)
    r.raise_for_status()
    page = r.text
    select = re.search(r'<select[^>]*id="team-lkl"[^>]*>(.*?)</select>', page, re.S)
    ids = (
        {
            html.unescape(n).strip(): v
            for v, n in re.findall(r'<option[^>]*value="(\d+)"[^>]*>([^<]+)<', select.group(1))
        }
        if select
        else {}
    )
    logos = {}
    for body in re.findall(r'<a href="https://lkl\.lt/komandos/[^"]+"[^>]*>(.*?)</a>', page, re.S):
        img = re.search(r'<img src="([^"]+)" alt="([^"]+)"', body)
        if img:
            logos.setdefault(html.unescape(img.group(2)).strip(), img.group(1))
    out = [
        row(
            "team",
            "lkl",
            tid,
            name,
            logos[name] if "://" in logos[name] else LKL_SITE + logos[name],
            "lkl",
            program="pro",
        )
        for name, tid in ids.items()
        if name in logos
    ]
    if not out:
        raise RuntimeError("lkl: the results page carried no team logos")
    return out


# Basketligaen (Denmark) on the Sportality platform (sdv-py family ``sportality``, home ``basketball/dbl``; the
# host serves its SPA for /robots.txt, so it has no rules). Standings carry each team's Genius team id and its logo
# on Sportality's CDN, one file per team instance (``team-logos/{instance}.svg``). Current season only: the CDN keeps
# one file per instance, not a history.
DBL_API = "https://www.basketligaen.dk/api"
DBL_LEAGUE_INSTANCE = "dbl1_dbl"  # teams without their own instance share the league's; its file is not a team mark


def dbl(session):
    """Basketligaen logos of the active regular season's teams, keyed by the Genius team id (sdv-py
    ``sportality_standings`` ``team_id``; joins the ``genius`` family). The ssgt is read from layout-info."""
    layout = _get_json(session, f"{DBL_API}/statistics-v2/layout-info", use_proxy=False)
    ssgts = [
        s
        for s in (layout[0] if isinstance(layout, list) else layout).get("originalSeasonSeriesGameTypeTeams") or []
        if (s.get("gameType") or {}).get("code") == "regular" and str((s.get("season") or {}).get("code")).isdigit()
    ]
    active = [s for s in ssgts if s.get("state") == "active"] or ssgts
    if not active:
        raise RuntimeError("dbl: layout-info listed no regular-season ssgt")
    ssgt = max(active, key=lambda s: int(s["season"]["code"]))
    time.sleep(2.0)
    out = []
    for r in (
        _get_json(
            session,
            f"{DBL_API}/gameday/team-standings/{ssgt['ssgtUuid']}",
            use_proxy=False,
        ).get("leagueStandings")
        or []
    ):
        info = r.get("info") or {}
        team = info.get("teamInfo") or {}
        if team.get("teamMedia") and info.get("teamId") and team.get("teamOwnerInstanceId") != DBL_LEAGUE_INSTANCE:
            out.append(
                row(
                    "team",
                    "dbl",
                    int(info["teamId"]),
                    (team.get("teamNames") or {}).get("full"),
                    team["teamMedia"],
                    "sportality",
                    program="pro",
                )
            )
    if not out:
        raise RuntimeError("dbl: the standings carried no team logos")
    return out


# NBL / WNBL team directories from Rosetta, the JSON proxy behind nbl.com.au. Not a key but a referer gate: without
# Origin / Referer = nbl.com.au every route answers 403 MISSING_API_KEY (sdv-py nbl_rosetta_runtime.GATE_HEADERS).
NBL_ROSETTA = "https://prod.rosetta.nbl.com.au/get"
NBL_ROSETTA_GATE = {
    "Origin": "https://www.nbl.com.au",
    "Referer": "https://www.nbl.com.au/",
}
# (Rosetta org, sdv-assets league). "nbl" is taken by ESPN's feed (ESPN ids), so Rosetta's NBL is "nbl_au".
# ponytail: org nbl1 left out: sdv-py's nbl1 key is the atrium family; add ("nbl1", "nbl1") once its team_id is
# confirmed to be the same DataCore UUID.
NBL_ROSETTA_ORGS = [("nbl", "nbl_au"), ("wnbl", "wnbl")]
NBL_ROSETTA_DELAY = 2.0
DATACORE_IMG = "images.dc.connect.sportradar.com"


def _datacore_original(url):
    """DataCore serves the original file at ``?size=RAW`` (the EASL rows use it); Rosetta asks for 100-800 px webp."""
    return url.split("?", 1)[0] + "?size=RAW" if DATACORE_IMG in url else url


def nbl_rosetta(session):
    """NBL and WNBL team logos from Rosetta's team directory, keyed by the DataCore team id (sdv-py
    ``nbl_rosetta_teams`` ``team_id``, the body's ``external_id``). The directory holds every side the org has
    listed (defunct, exhibition and visiting clubs too); most records carry no logo. ``team_logo`` is the site's own
    file, ``external_team_logo`` DataCore's; the second is kept as variant ``datacore`` when it is another image."""
    out = []
    for i, (org, league) in enumerate(NBL_ROSETTA_ORGS):
        if i:
            time.sleep(NBL_ROSETTA_DELAY)
        r = session.get(
            f"{NBL_ROSETTA}/{org}/teams?limit=5000",
            headers={**UA, **NBL_ROSETTA_GATE},
            timeout=60,
        )
        r.raise_for_status()
        for t in r.json().get("data") or []:
            urls = [_datacore_original(u) for u in (t.get("team_logo"), t.get("external_team_logo")) if u]
            if not urls or not t.get("external_id"):
                continue
            out.append(
                row(
                    "team",
                    league,
                    t["external_id"],
                    t.get("name"),
                    urls[0],
                    "nbl_rosetta",
                    program="pro",
                )
            )
            if urls[-1] != urls[0]:
                out.append(
                    row(
                        "team",
                        league,
                        t["external_id"],
                        t.get("name"),
                        urls[-1],
                        "nbl_rosetta",
                        variant="datacore",
                        program="pro",
                    )
                )
    if not out:
        raise RuntimeError("nbl_rosetta: the team directories carried no logos")
    return out


# KBL (Korea): api.kbl.or.kr, the data API behind www.kbl.or.kr. It answers 400 unless Channel and TeamCode are sent
# (any value; sdv-py kbl_runtime.HEADERS). The team list names each club's CSS logo class (teamLogoClass), and the
# site's stylesheet (/assets/css/style.css) draws class X from /assets/img/ico/logo/ic-X.svg.
KBL_API = "https://api.kbl.or.kr"
KBL_HEADERS = {"Channel": "WEB", "TeamCode": "XX", "Accept": "application/json"}
KBL_LOGOS = "https://www.kbl.or.kr/assets/img/ico/logo"


def kbl(session):
    """KBL logos of the current clubs, keyed by the two-digit team code (sdv-py ``kbl_teams`` ``team_code``, a string
    with its leading zero; it is not stable across ownership changes). The list holds the current season's clubs."""
    r = session.get(f"{KBL_API}/common/teamList", headers={**UA, **KBL_HEADERS}, timeout=60)
    r.raise_for_status()
    # ponytail: the ic-X-dark.svg files (one per class, kgc's is ic-kgc-dark-line.svg) are left out until a variant
    # name for them is settled
    out = [
        row(
            "team",
            "kbl",
            t["teamCode"],
            t.get("teamNameFullEn") or t.get("teamNameFull"),
            f"{KBL_LOGOS}/ic-{t['teamLogoClass']}.svg",
            "kbl",
            program="pro",
        )
        for t in r.json()
        if t.get("teamCode") and t.get("teamLogoClass")
    ]
    if not out:
        raise RuntimeError("kbl: the team list carried no logo class")
    return out


# B.LEAGUE (Japan): the standings page (www.bleague.jp/standings/?year=START&tab=1|2|3) links every club to
# club_detail/?TeamID=N beside its logo, filed per season (.../img/logo/{START year}/s/{code}.png). No JSON team list
# exists; sdv-py reads the same page (bleague_standings) and the schedule page's club filter.
BLEAGUE_SITE = "https://www.bleague.jp"
BLEAGUE_FIRST = 2016  # START year of the first season (2016-17)
BLEAGUE_TABS = (1, 2, 3)  # B1, B2, B3
BLEAGUE_DELAY = 2.0  # robots.txt sets a Crawl-delay only for msnbot (10) and bingbot (30)
_BLEAGUE_ROW = re.compile(r'club_detail/\?TeamID=(\d+)">\s*<div class="team-logo">\s*<img src="([^"]+)" alt="([^"]*)"')


def bleague(session):
    """B.LEAGUE club logos from the standings pages of every season and division, keyed by the club id (sdv-py
    ``bleague_standings`` / ``bleague_schedule_clubs`` ``team_id``, stable across seasons and renames). The site files
    a logo per season; ``valid_from`` / ``valid_to`` are SDV ending years, and a file reused across seasons is one row
    spanning them (the site re-files every logo each season, so in practice a row is one season)."""
    span = {}
    first = True
    for year in range(BLEAGUE_FIRST, dt.date.today().year + 1):
        for tab in BLEAGUE_TABS:
            if not first:
                time.sleep(BLEAGUE_DELAY)
            first = False
            page = _page(session, f"{BLEAGUE_SITE}/standings/?year={year}&tab={tab}")
            for tid, src, name in _BLEAGUE_ROW.findall(page or ""):
                if f"/img/logo/{year}/" not in src:  # a season the site lacks may answer with another season's page
                    continue
                k = (
                    tid,
                    re.sub(r"/v=\d+/", "/", src),
                )  # drop the cache-buster: the file answers without it
                lo, _, _ = span.get(k, (year + 1, None, None))
                span[k] = (lo, year + 1, name)
    if not span:
        raise RuntimeError("bleague: no standings page carried a club logo")
    return [
        row(
            "team",
            "bleague",
            tid,
            name,
            url,
            "bleague",
            program="pro",
            valid_from=lo,
            valid_to=hi,
        )
        for (tid, url), (lo, hi, name) in span.items()
    ]


# The SHL site's own API (Sportality platform, stats by StatNet). No robots.txt (the path answers the site's HTML
# error page). Team marks sit on Sportality's CDN, one SVG per team named after its Sportality site id.
SHL_API = "https://www.shl.se/api"
SHL_DELAY = 2.0  # sdv-py's European hockey politeness ruling is >=1 s per host; sdv-assets uses 2


def shl(session):
    """SHL team logos, keyed by the StatNet team code (sdv-py's SHL ``team_id`` / ``team_code``, e.g. ``FHC``; codes
    can be mixed case or non-ASCII: ``MoDo``, ``ÖRE``). ``layout-info`` lists every SHL season's teams since 1975-76
    with their Sportality site id; ``all-sites`` of the active season gives each current team's icon. A past team it
    does not list gets the CDN file named after its site id (``team-logos/<site id>.svg``: every listed icon is named
    that way, in the 1985-86 answer as in the current one), which capture confirms or drops."""
    layout = _get_json(session, f"{SHL_API}/statistics-v2/layout-info", use_proxy=False)
    layout = layout[0] if isinstance(layout, list) and layout else layout
    teams = {}  # site id -> (team code, name)
    for ssgt in layout.get("originalSeasonSeriesGameTypeTeams") or []:
        for t in ssgt.get("teams") or []:
            code = (t.get("name") or {}).get("code")
            if code and t.get("instanceId"):
                teams[t["instanceId"]] = (
                    code,
                    (t.get("teamNames") or {}).get("long") or t["name"].get("medium"),
                )
    active = (layout.get("ssgtDefaultValue") or {}).get("ssgtUuid")
    if not teams or not active:
        raise RuntimeError("shl: layout-info carried no teams or no active season")
    time.sleep(SHL_DELAY)
    icons = {
        s["id"]: s["icon"]
        for s in _get_json(session, f"{SHL_API}/sports-v2/all-sites/{active}", use_proxy=False)
        if s.get("id") and s.get("icon")
    }
    if not icons:
        raise RuntimeError("shl: all-sites carried no team icons")
    cdn = next(iter(icons.values())).rsplit("/", 1)[0]
    return [
        row(
            "team",
            "shl",
            code,
            name,
            icons.get(site, f"{cdn}/{site}.svg"),
            "shl",
            program="pro",
        )
        for site, (code, name) in teams.items()
    ]


# HockeyAllsvenskan's own Next.js API (keyless, POST bodies; no robots.txt). Every stats body carries the season's
# ``teams`` list with the logo in five sizes on ha-media.hadigital.se. Each season serves the CURRENT file of each team
# (AIK's 2009-10 and 2026-27 entries are the same file), so a row carries no validity window.
HA_API = "https://hockeyallsvenskan.se/api/all-time-goalkeepers-stats"
HA_FIRST = 2006  # END year of the first season with stats (2005-06), sdv-py ha_runtime.FIRST_SEASON
HA_DELAY = 2.0
HA_BODY = {
    "league": "HA",
    "phase": "HA",
    "team_side": "all",
    "team": "all",
    "against_team": "all",
    "all_results": "all",
    "game_status": "all",
    "decided_in": "all",
    "categories": "summary",
    "page": 1,
    "page_size": 1000,
}


def ha(session):
    """HockeyAllsvenskan team logos (the ``xlarge`` size), keyed by the StatNet team id (sdv-py ``ha_teams``
    ``team_statnet_id``, e.g. ``MIK``; HA frames have no ``team_id`` column, and ``team_code`` is the display code,
    ``MORA``). Every season from 2005-06 on, so teams no longer in the league keep their mark; the latest season's
    file and name win. The site keys a season by its START year; ``HA_FIRST`` is an SDV END year."""
    today = dt.date.today()
    last = today.year + (today.month >= 7)  # END year of the season in progress
    marks = {}
    for end in range(HA_FIRST, last + 1):
        if end > HA_FIRST:
            time.sleep(HA_DELAY)
        r = session.post(
            HA_API,
            json={**HA_BODY, "season": str(end - 1)},
            headers={**UA, "Accept": "application/json"},
            timeout=60,
        )
        r.raise_for_status()
        for t in r.json().get("teams") or []:
            url = (t.get("logos") or {}).get("xlarge")
            if t.get("statNetId") and url:
                marks[t["statNetId"]] = (t.get("fullName") or t.get("name"), url)
    if not marks:
        raise RuntimeError("ha: no season carried a team logo")
    return [row("team", "ha", tid, name, url, "hockeyallsvenskan", program="pro") for tid, (name, url) in marks.items()]


# The French federation's backend behind liguemagnus.com (WordPress admin-ajax; robots.txt allows it). Logos are on
# the federation's hockeynet.fr storage.
MAGNUS_AJAX = "https://liguemagnus.com/wp-admin/admin-ajax.php"
MAGNUS_DELAY = 2.0


def _magnus(session, query):
    body = _get_json(session, f"{MAGNUS_AJAX}?{query}", use_proxy=False)
    if not body.get("success"):
        raise RuntimeError(f"magnus: {query} answered success=false")
    data = body.get("data")
    return data.get("data") if isinstance(data, dict) else data


def magnus(session):
    """Ligue Magnus team logos, keyed by the federation's team id (sdv-py ``magnus_teams`` ``team_id``, stable across
    seasons). One ``get_equipes`` call without a phase lists every team of every competition the federation runs; a
    team is kept when it entered a Ligue Magnus competition (one per season from 2020-21, from
    ``get_competition_by_type``). A club can field several team ids over the years (Mulhouse has three); each keeps
    the logo the federation shows for it now, so a row carries no validity window."""
    comps = {c["id"] for c in _magnus(session, "action=get_competition_by_type&id_type=1") or [] if c.get("id")}
    time.sleep(MAGNUS_DELAY)
    out = [
        row(
            "team",
            "magnus",
            t["id"],
            t.get("libelle_complet"),
            t["logo"],
            "hockeyfrance",
            program="pro",
        )
        for t in _magnus(session, "action=get_equipes") or []
        if t.get("id") and t.get("logo") and comps & set(t.get("id_compet") or [])
    ]
    if not out:
        raise RuntimeError("magnus: no Ligue Magnus team carried a logo")
    return out


# Not viable, so no source here: DEL (`del_`), ICE Hockey League (`icehl`), EIHL (`eihl`), Metal Ligaen (`metal`).
# The four eSports hokejovyzapis buckets name a team only as {id, name, shortcut} (league-matches `home` / `guest`,
# tables `clubs` / `tableData.table`, game-header `teamInfo`), and the game-center widget's `homeTeamLogoUrl` /
# `images.homeTeamLogo` are null both in sdv-py's fixtures and live (ICEHL match 8479, Metal match 622, 2026-11-25).
# The only upload paths in any payload hold hashed file names (`icehl.hokejovyzapis.cz/upload/television/logo/_<hash>_
# <ts>_<name>.jpg`, `esports.ice.hockey/upload/player/photo/<id>_<hash>_<ts>_<name>.png`), so a team id gives no URL.


# Czech Extraliga. sdv-py's `elh_scoreboard` (the eSports scoreboard, league 101) names each day's teams in both id
# spaces; hokej.cz's public, listable S3 bucket keeps one PNG per hokej.cz team id in logos/{30,76,120,200,original}/.
ELH_SCOREBOARD = "https://json.esports.cz/hokejcz/scoreboard"
ELH_LOGOS = "https://s3-eu-west-1.amazonaws.com/hokej.cz/logos/200"
ELH_LEAGUE = "101"  # Tipsport extraliga's key in the scoreboard
ELH_DAYS = (
    14  # the scoreboard keeps a rolling window of recent days (today answers 404); a national-team break is shorter
)
ELH_DELAY = 2.0  # one request at a time to the eSports host (sdv-py euro_hockey_runtime asks >= 1 s)


def elh(session):
    """Czech Extraliga logos from hokej.cz's bucket, keyed by the hokej.cz team id (sdv-py ``elh_scoreboard``
    ``hokejcz_home_team_id`` / ``hokejcz_away_team_id``, ``elh_shots`` ``hokejcz_team_id``; not the onlajny id the
    play-by-play uses). The teams are those that played in the scoreboard's last ``ELH_DAYS`` days, so the source finds
    none in the off-season; each file is the team's current mark, overwritten in place, so rows carry no season."""
    teams = {}
    today = dt.date.today()
    for back in range(1, ELH_DAYS + 1):
        if back > 1:
            time.sleep(ELH_DELAY)
        r = session.get(
            f"{ELH_SCOREBOARD}/{today - dt.timedelta(days=back)}.json",
            headers=UA,
            timeout=60,
        )
        if r.status_code == 404:  # a day outside the window
            continue
        r.raise_for_status()
        for g in (r.json().get(ELH_LEAGUE) or {}).get("matches") or []:
            for side in ("home", "visitor"):
                t = g.get(side) or {}
                if t.get("hokejcz_id"):
                    teams[str(t["hokejcz_id"])] = t.get("name")
    if not teams:
        raise RuntimeError(f"elh: no Extraliga game in the scoreboard's last {ELH_DAYS} days")
    return [
        row(
            "team",
            "elh",
            tid,
            name,
            f"{ELH_LOGOS}/{tid}.png",
            "hokej.cz",
            program="pro",
        )
        for tid, name in sorted(teams.items(), key=lambda kv: int(kv[0]))
    ]


# api2.acb.com, the .NET API behind acb.com. Its X-APIKEY is public (acb.com's bundle); this is sdv-py's DEFAULT_KEY
# (sportsdataverse/basketball/acb/acb_runtime.py). The API keys a season by editionId = END year - 1936.
ACB_API = "https://api2.acb.com/api/seasondata/TeamProfile/teams"
ACB_KEY = os.environ.get("SDV_ASSETS_ACB_KEY", "0dd94928-6f57-4c08-a3bd-b1b2f092976e")
# END year of the first season (2019-20, edition 84) whose every team carries a logo; 2010-11 has 2 of 18
ACB_FIRST = 2020
ACB_DELAY = 2.0


def acb(session):
    """Liga Endesa logos from the ACB's own API, every season from 2019-20 on, keyed by the club id (sdv-py
    ``acb_teams`` ``club_id``; its ``team_id`` is a season entry, new every season). ``secondaryLogo`` (the negative
    mark, for dark backgrounds) is variant ``dark`` when it is another file. ``valid_from`` / ``valid_to`` are SDV END
    years; a file used in several seasons is one row spanning them."""
    hdr = {**UA, "X-APIKEY": ACB_KEY, "Accept": "application/json"}

    def edition(ed=None):
        q = "competitionId=1" + (f"&editionId={ed}" if ed else "")
        r = session.get(f"{ACB_API}?{q}", headers=hdr, timeout=60)
        r.raise_for_status()
        return r.json()

    current = edition()  # no editionId: the current season
    now = current["selectedFilters"]["season"]
    span = {}
    for ed in range(ACB_FIRST - 1936, now + 1):
        if ed != now:
            time.sleep(ACB_DELAY)
        body = current if ed == now else edition(ed)
        year = ed + 1936
        for t in body.get("teams") or []:
            t = t.get("selected") or {}
            if t.get("editionId") != ed or not t.get("clubId"):
                continue  # another season's answer
            for variant, url in (
                ("default", t.get("logo")),
                ("dark", t.get("secondaryLogo")),
            ):
                if url and (variant == "default" or url != t.get("logo")):
                    k = (str(t["clubId"]), variant, url)
                    lo = span.get(k, (year,))[0]
                    span[k] = (lo, year, t.get("fullName"))
    if not span:
        raise RuntimeError("acb: no season carried a team logo")
    return [
        row(
            "team",
            "acb",
            cid,
            name,
            url,
            "acb",
            variant=variant,
            program="pro",
            valid_from=lo,
            valid_to=hi,
        )
        for (cid, variant, url), (lo, hi, name) in span.items()
    ]


# FEB has no team-list route: sdv-py's feb_schedule reads the calendario page of baloncestoenvivo.feb.es (robots.txt
# allows it). Each team links Equipo.aspx?i=<team_id> and shows imagenes.feb.es/Imagen.aspx?i=<team_id>&ti=1.
FEB_SITE = "https://baloncestoenvivo.feb.es"
FEB_LOGO = "https://imagenes.feb.es/imagen.aspx?i={}&ti=1"
FEB_GROUPS = (
    1,
    2,
    4,
)  # the site's menu groups: Primera FEB, Segunda FEB, Liga Femenina Endesa
FEB_FIRST = 2021  # END year of the LiveStats API's first season (2020-21); the pages go back further (2011-12 seen)
FEB_DELAY = 2.0
_FEB_TEAM = re.compile(r'Equipo\.aspx\?i=(\d+)">([^<]+)</a>')
_FEB_IMG = re.compile(r"[Ii]magen\.aspx\?i=(\d+)&amp;ti=1")


def feb(session):
    """FEB team logos (Primera FEB, Segunda FEB, Liga Femenina Endesa), keyed by the FEB team id (sdv-py
    ``feb_schedule`` ``home_team_id`` / ``away_team_id``, the API's ``team_id``). That id is a TEAM-SEASON registration,
    new every season, so each row is one season (``valid_from`` = ``valid_to`` = the SDV END year). The site keys a
    season by its START year and serves another season for one it lacks, so the page's selected season must be the
    one asked for."""
    out = {}
    first = True
    for year in range(FEB_FIRST, dt.date.today().year + 2):
        for g in FEB_GROUPS:
            if not first:
                time.sleep(FEB_DELAY)
            first = False
            r = session.get(f"{FEB_SITE}/calendario.aspx?g={g}&t={year - 1}", headers=UA, timeout=60)
            if r.status_code == 404:
                continue
            r.raise_for_status()
            # a season the site does not hold answers 200, at the season's own URL, with its default season's page
            if f'<option selected="selected" value="{year - 1}">{year - 1}/{year}<' not in r.text:
                continue
            logos = set(_FEB_IMG.findall(r.text))
            for tid, name in _FEB_TEAM.findall(r.text):
                if tid in logos:
                    out[tid] = row(
                        "team",
                        "feb",
                        tid,
                        name.strip(),
                        FEB_LOGO.format(tid),
                        "feb",
                        program="pro",
                        valid_from=year,
                        valid_to=year,
                    )
    if not out:
        raise RuntimeError("feb: no calendar page carried a team logo")
    return list(out.values())


# The SCB API behind easycredit-bbl.de (BBL) and toyota-dbbl.de (DBBL): x-api-key publicWebUser plus an x-api-secret
# that rotates; it is the "key" embedded in an easycredit-bbl.de page, reversed, ':' -> '-' (sdv-py scb_runtime).
# The secret is never printed. Logo files need no header and are per (season, team): /images/teams/logo/{season}/{id}.
SCB_HOSTS = {
    "bbl": "https://api.basketball-bundesliga.de",
    "dbbl": "https://api-1.dbbl.scb.world",
}
SCB_KEY_PAGE = "https://www.easycredit-bbl.de/"
SCB_DELAY = 2.0


def scb(session):
    """BBL and DBBL team logos from the SCB API, keyed by the SCB team id (sdv-py ``scb_season_teams`` ``team_id``;
    ``season_team_id`` equals it). League ``bbl`` or ``dbbl`` (sdv-py's ``league`` argument): the two hosts share
    one API and their ids are separate key spaces. Every season ``/seasons`` lists (BBL from 2002-03; DBBL answers no
    teams before its first covered season); ``valid_from`` = ``valid_to`` = the SDV END year (seasonId + 1)."""
    page = session.get(SCB_KEY_PAGE, headers={**UA, "Accept": "text/html"}, timeout=60)
    page.raise_for_status()
    key = next(
        (k for k in re.findall(r'"key":"([^"]+)"', page.text) if re.fullmatch(r"[A-Za-z0-9:_-]{32,256}", k)),
        None,
    )
    if key is None:
        raise RuntimeError("scb: no API key on the easycredit-bbl.de page (layout changed)")
    hdr = {
        **UA,
        "x-api-key": "publicWebUser",
        "x-api-secret": key[::-1].replace(":", "-"),
        "Accept": "application/json",
    }

    def get(url):
        time.sleep(SCB_DELAY)
        r = session.get(url, headers=hdr, timeout=60)
        r.raise_for_status()
        return r.json()

    out = []
    for league, host in SCB_HOSTS.items():
        for sid in sorted(s["id"] for s in get(f"{host}/seasons")):
            for t in get(f"{host}/seasons/{sid}").get("teams") or []:
                if t.get("logoUrl") and t.get("teamId"):
                    out.append(
                        row(
                            "team",
                            league,
                            t["teamId"],
                            t.get("name"),
                            t["logoUrl"],
                            "scb",
                            program="pro",
                            valid_from=sid + 1,
                            valid_to=sid + 1,
                        )
                    )
    if not out:
        raise RuntimeError("scb: no season carried a team logo")
    return out


# Infobasket, the Russian federation's stats platform behind vtb-league.com (sdv-py ``vtb_infobasket_*``; robots.txt
# is a 404). The calendar of a season lists each game's two TeamIDs with their logo file. sdv-py flags the league as
# sanctions-sensitive; the owner signed off on archiving its marks (2026-11-25).
VTB_API = "https://org.infobasket.su"
VTB_FIRST = 2027  # END year of the season probed
VTB_DELAY = 2.0


def vtb(session):
    """VTB United League logos from Infobasket's season calendars, keyed by the Infobasket ``TeamID`` (sdv-py
    ``vtb_infobasket_*`` and ``vtb_league_*`` ``team_id``, stable across seasons). ``valid_from`` / ``valid_to`` are
    END years; a file used in several seasons is one row spanning them."""
    comps = {
        int(s["SeasonYear"]): s["CompID"]
        for s in _get_json(
            session,
            f"{VTB_API}/Comp/GetSeasonsForTag?format=json&tag=vtb",
            use_proxy=False,
        )
    }
    span = {}
    for year in sorted(y for y in comps if y >= VTB_FIRST):
        time.sleep(VTB_DELAY)
        for g in _get_json(
            session,
            f"{VTB_API}/Comp/GetCalendar/?comps={comps[year]}&format=json",
            use_proxy=False,
        ):
            for side in "AB":
                tid, url = g.get(f"Team{side}id"), g.get(f"TeamLogo{side}")
                if tid and url:
                    k = (str(int(tid)), url)
                    lo, _, _ = span.get(k, (year, None, None))
                    span[k] = (lo, year, g.get(f"CompTeamName{side}en"))
    if not span:
        raise RuntimeError("vtb: no season calendar carried a team logo")
    return [
        row(
            "team",
            "vtb",
            tid,
            name,
            url,
            "infobasket",
            program="pro",
            valid_from=lo,
            valid_to=hi,
        )
        for (tid, url), (lo, hi, name) in span.items()
    ]


# W League (Japan, women): the keyless JSON backend of www.wjbl.org. Its team list (/teams/base) has no logo; the
# schedule's home / away sides carry one, as a path on the league site. The API pages the schedule by month.
WJBL_API = "https://api.wjbl.01core.app/api"
WJBL_SITE = "https://www.wjbl.org"
WJBL_DELAY = 2.0
# a league edition ("25-26 Wリーグ"); all-star games and cups have their own
WJBL_EDITION = re.compile(r"\d\d-\d\d Wリーグ")


def wjbl(session):
    """W League team logos from the latest league edition's schedule, keyed by the franchise id (sdv-py
    ``wjbl_teams`` ``team_id``, stable across seasons and sponsor renames). Every team that played in the edition,
    Premier and Future divisions; the newest month's file wins if a team's logo changed in-season."""
    editions = sorted(
        (
            x
            for x in _get_json(session, f"{WJBL_API}/leagues", use_proxy=False)
            if WJBL_EDITION.fullmatch(x.get("league_title") or "")
        ),
        key=lambda x: -int(x["league_id"]),
    )
    seen = {}
    for edition in editions:  # newest first; an edition without games yet has no months
        time.sleep(WJBL_DELAY)
        q = f"league_id={edition['league_id']}"
        months = _get_json(session, f"{WJBL_API}/play-schedule-months?{q}", use_proxy=False).get("game_months") or []
        for m in months:
            time.sleep(WJBL_DELAY)
            body = _get_json(
                session,
                f"{WJBL_API}/play-schedules?{q}&game_month={m['game_month']}",
                use_proxy=False,
            )
            for phase in (body.get("seasons") or {}).values():
                for g in phase.get("play_schedules") or []:
                    for side in ("home", "away"):
                        tid, logo = (
                            g.get(f"{side}_team_id"),
                            g.get(f"{side}_team_logo_image"),
                        )
                        if tid and logo:
                            seen.setdefault(str(tid), (g.get(f"{side}_season_team_name"), logo))
        if months:
            break
    if not seen:
        raise RuntimeError("wjbl: no schedule side carried a team logo")
    return [
        row(
            "team",
            "wjbl",
            tid,
            name,
            WJBL_SITE + logo if logo.startswith("/") else logo,
            "wjbl",
            program="pro",
        )
        for tid, (name, logo) in seen.items()
    ]


F1_MEDIA = "https://media.formula1.com"
# formula1.com's file names for the 2024+ SVGs -> variant: the white mark is for dark backgrounds, the black one for light
F1_SVG_VARIANTS = [("logo", "default"), ("logowhite", "dark"), ("logoblack", "on_light")]
# (season, file name) formula1.com never published: every 2024 team's logoblack answers 404 (checked 2026-11-02)
F1_SVG_MISSING = {(2024, "logoblack")}


def f1_media(session):
    """F1 constructor logos from formula1.com's media CDN, keyed by the Jolpica ``constructor_id`` sdv-py's F1 family uses.
    Both paths are season- and slug-keyed, and slugs change with rebrands (Sauber -> ``kicksauber`` -> ``audi``), so the
    season -> constructor -> slug crosswalk is curated (``curated/f1_constructors.csv``; never matched by name). 2024 on:
    ``image/upload/common/f1/{year}/{slug}/{year}{slug}{logo,logowhite,logoblack}``, the original SVG (2024 has no
    ``logoblack``: ``F1_SVG_MISSING``). 2019-2023: ``content/dam/fom-website/teams/{year}/{slug}-logo.png`` (96 px; the
    2021-23 folders repeat the 2020 files, and each row still carries only its folder's season). Add next season's rows
    when formula1.com's /en/teams page changes."""
    out = []
    with open(CURATED / "f1_constructors.csv", newline="") as f:
        for t in csv.DictReader(f):
            y, slug = int(t["season"]), t["f1_slug"]
            if y >= 2024:
                urls = [(f"{F1_MEDIA}/image/upload/common/f1/{y}/{slug}/{y}{slug}{name}", v) for name, v in F1_SVG_VARIANTS
                        if (y, name) not in F1_SVG_MISSING]
            else:
                urls = [(f"{F1_MEDIA}/content/dam/fom-website/teams/{y}/{slug}-logo.png", "default")]
            for url, variant in urls:
                out.append(row("team", "f1", t["constructor_id"], t["name"], url, "formula1.com", variant=variant,
                               program="pro", valid_from=y, valid_to=y))
    return out


def f1_history(session):
    """F1 constructor logos for the seasons before ``f1_media`` (2019), keyed by the same Jolpica ``constructor_id``
    (``curated/f1_history.csv``, one row per file, each checked by eye). 2015-2018, source ``formula1.com``: the logo the
    site's team pages drew that season (``content/fom-website/en/championship/teams/{Team}/_jcr_content/logo.img.*``,
    gone from the live site), as a Wayback Machine ``id_`` copy captured in that season, one row per season. formula1.com's
    2003-2014 site showed no team logos. 2002-2015, source ``wikimedia-commons``: Commons files tagged public domain
    (PD-textlogo / PD-ineligible), each row naming its file page and license and covering the seasons the file's
    description or the team's entry name dates it to. capture keys a manifest row by URL, so a file used for several
    seasons is one row with a season range."""
    with open(CURATED / "f1_history.csv", newline="") as f:
        return [
            row("team", "f1", t["constructor_id"], t["name"], t["url"], t["source"], program="pro",
                valid_from=int(t["valid_from"]), valid_to=int(t["valid_to"]))
            for t in csv.DictReader(f)
        ]


CFL_LOGOS = "https://content.cfl.ca/logos"
# (ESPN team id, ESPN name, the CFL's own abbreviation, the variants its logo service has an SVG for). ESPN lists the nine
# teams with no logo, and sdv-py reads the CFL from ESPN, so the rows are keyed by ESPN id. Checked 2026-11-02; a
# missing variant answers a JSON 404 ("No SVG asset found for this variant").
CFL_TEAMS = [
    (79, "BC Lions", "bc", ["primary"]),
    (80, "Calgary Stampeders", "cgy", ["primary"]),
    (81, "Edmonton Elks", "edm", ["dark"]),  # its primary is raster-only (see cfl)
    (82, "Hamilton Tiger-Cats", "ham", ["primary"]),
    (83, "Montreal Alouettes", "mtl", ["primary"]),
    (84, "Saskatchewan Roughriders", "ssk", ["primary"]),
    (85, "Toronto Argonauts", "tor", ["primary"]),
    (86, "Winnipeg Blue Bombers", "wpg", ["primary", "dark"]),
    (87, "Ottawa Red Blacks", "ott", ["primary", "dark"]),
]


def cfl(session):
    """CFL team and league marks from the league's own logo service (``content.cfl.ca/logos/{abbr}-{primary,dark}.svg``,
    the files cfl.ca's pages draw). Only the SVGs: the service's rasters (``bc-primary.png``, ``edm-dark.png``) are
    different artwork, not renderings of the SVG, so they are left out. ``cfl-dark.svg`` is the same file as
    ``cfl-primary.svg``; ``lcf-primary.svg`` is the French (LCF) mark."""
    out = [
        row("team", "cfl", tid, name, f"{CFL_LOGOS}/{abbr}-{v}.svg", "cfl", variant="default" if v == "primary" else v,
            program="pro")
        for tid, name, abbr, variants in CFL_TEAMS
        for v in variants
    ]
    out += [
        row("league", "cfl", "CFL", "Canadian Football League", f"{CFL_LOGOS}/cfl-primary.svg", "cfl"),
        row("league", "cfl", "CFL", "Canadian Football League", f"{CFL_LOGOS}/lcf-primary.svg", "cfl", variant="french"),
    ]
    return out


def curated_conference_marks(session):
    """NCAA Division II / III football conference logos, which neither ESPN (its D-II / D-III groups carry no logos) nor
    NCAA.com serves: each is the conference's own site mark (mostly SIDEARM's ``images/logos/site/site.png``) as the
    Wayback Machine archived it (``id_``), checked by eye (``curated/conference_marks.csv``). A conference site has no API
    to re-validate against, so the row is pinned to a snapshot. Keyed by ESPN's college-football group id, the id
    ``espn_groups`` keys the FBS / FCS conferences by. Also the hockey-only college conferences (``ncaa_mhockey`` /
    ``ncaa_whockey``; ESPN's hockey groups carry no logos and its women's list has none), keyed by the
    sdv-reference-data group id (``ncaa_mhockey:hockey-east``), the ``conference_id`` sdvplot's team rows carry."""
    with open(CURATED / "conference_marks.csv", newline="") as f:
        return [
            row("conference", t["league"], t["entity_id"], t["name"], t["wayback"], "wayback", variant=t["variant"])
            for t in csv.DictReader(f)
        ]


# The cfbplotR commit whose data-raw/wordmarks/ the rows point at; raw URLs pinned to it outlive the retired repo's main
CFBPLOTR_COMMIT = "bbe7483a9c2947802efa91b71f8bad33ebdae68e"
CFBPLOTR_WORDMARKS = f"https://raw.githubusercontent.com/sportsdataverse/cfbplotR/{CFBPLOTR_COMMIT}/data-raw/wordmarks"


def cfbplotr_wordmarks(session):
    """The 366 college wordmarks cfbplotR served (``data-raw/wordmarks/``), kept so its retirement loses none of them.
    An owner-approved exception to the official-sources rule: most are sportslogos.net files, so every row is
    ``source=cfbplotr-legacy`` and can be pulled as a set. ``curated/cfbplotr_wordmarks.csv`` keys each file by the ESPN id
    of the logo cfbplotR's ``logo_ref.csv`` paired it with (league ``cfb`` for a football team, ``mbb`` for a
    basketball-only school), and the conferences by the group id ``espn_groups`` uses. A row with no entity_id is a file
    no archive team matches; it stays listed and is skipped. ``valid_from`` is left blank: the year in a file name is
    sportslogos.net's dating, not cfbplotR's, and cfbplotR drew each mark for every season."""
    with open(CURATED / "cfbplotr_wordmarks.csv", newline="", encoding="utf-8") as f:
        return [
            row(t["level"], t["league"], t["entity_id"], t["name"], f"{CFBPLOTR_WORDMARKS}/{quote(t['file'])}",
                "cfbplotr-legacy", variant=t["variant"], mark_type="wordmark",
                program={"cfb": "football", "mbb": "mens"}.get(t["league"]) if t["level"] == "team" else None)
            for t in csv.DictReader(f)
            if t["entity_id"]
        ]


SOURCES = [
    espn_teams, espn_groups, espn_static, nhl_catalog, nhl_playoff_marks, mlbstatic, nflverse, espn_soccer, espn_seasons, hockeytech, milb, fox_usfl, espn_cricket, aaf_strip, phf_wayback, ncaa_com, wayback_marks,
    f1_media, f1_history, cebl, easl, lba,
    euroleague, bnxt, aba, ibba, kzs, lkl, dbl, nbl_rosetta, kbl, bleague, shl, ha, magnus, elh,
    acb, feb, scb, vtb, wjbl,
    cfl,
    curated_conference_marks,
    cfbplotr_wordmarks,
]
