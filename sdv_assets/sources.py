"""Where each official mark lives: one function per source, each returning candidate rows.

A row names a mark (level, league, entity, program, mark type, variant, validity window) and the URL
to fetch it from. Capture decides which rows are real by fetching and checking the image.
Seasons follow SDV conventions: ending year for NHL / NBA / MBB / WBB, single or starting year elsewhere.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import os

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
]

# Division I programs ESPN's teams lists leave out (see sdvplotR data-raw/generate_logo_ref.R)
ESPN_MISSING_IDS = {
    "cfb": [292, 2598],
    "mbb": [2815, 2511, 2598, 88],
    "wbb": [2385, 2598],
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


def _get_json(session, url):
    """JSON API calls. ESPN's site API blocks datacenter IPs after heavy traffic, so these few calls can go
    through a proxy (SDV_ASSETS_API_PROXY, a full proxy URL); image downloads never do."""
    proxy = os.environ.get("SDV_ASSETS_API_PROXY")
    r = session.get(url, headers=UA, timeout=60, proxies={"http": proxy, "https": proxy} if proxy else None)
    r.raise_for_status()
    return r.json()


def _variant(rel):
    """ESPN rel lists look like ['full', 'dark'] or ['full', 'primary_logo_on_white_color']."""
    rest = [x for x in rel if x != "full"]
    return "_".join(rest) if rest else "default"


def espn_teams(session):
    """Every logo ESPN lists for every team: default, dark, scoreboard and the 12-variant brand sets."""
    out = []
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
            if str(tid) not in seen:
                team = _get_json(session, f"{base}/{tid}").get("team")
                if team:
                    teams.append(team)
        for t in teams:
            for logo in t.get("logos", []):
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
    teams = _get_json(session, "http://statsapi.mlb.com/api/v1/teams?sportId=1")[
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


SOURCES = [espn_teams, espn_groups, espn_static, nhl_catalog, mlbstatic, nflverse]
