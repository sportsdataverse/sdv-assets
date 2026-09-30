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
import re
from pathlib import Path

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


def _get_json(session, url, use_proxy=True):
    """JSON API calls. ESPN's site API blocks datacenter IPs after heavy traffic, so these few calls can go
    through a proxy (SDV_ASSETS_API_PROXY, a full proxy URL); image downloads never do. The MLB Stats API
    refuses that proxy (403), so its calls pass ``use_proxy=False``."""
    proxy = os.environ.get("SDV_ASSETS_API_PROXY") if use_proxy else None
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


def _espn_site_teams(session, sport, league):
    """A competition's current teams from ESPN's site API; [] when ESPN has no team list for it (404)."""
    try:
        data = _get_json(session, f"{ESPN_SITE}/{sport}/{league}/teams?limit=1000")
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            return []
        raise  # a 403 is the droplet being blocked, not an empty league: fail the source, don't publish a gap
    leagues = (data.get("sports") or [{}])[0].get("leagues") or [{}]
    return [t["team"] for t in leagues[0].get("teams", [])]


def espn_soccer(session):
    """Every club in every soccer competition ESPN lists (219 on 2026-09-30). A club appears in its league and in
    each cup it plays; capture collapses those repeats to one row per club and logo."""
    comps = [
        ref["$ref"].split("/leagues/")[1].split("?")[0]
        for ref in _get_json(session, f"{ESPN_CORE}/soccer/leagues?limit=1000")["items"]
    ]
    out = []
    for comp in comps:
        for t in _espn_site_teams(session, "soccer", comp):
            for logo in t.get("logos", []):
                out.append(
                    row("team", "soccer", t["id"], t.get("displayName"), logo["href"], "espn",
                        variant=_variant(logo.get("rel", [])))
                )
    if not out:
        raise RuntimeError(f"espn_soccer: {len(comps)} competitions listed but no club logos returned")
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
]


def _hockey_season(name):
    """'2024-25 Regular Season' -> 2025 (hockey seasons are keyed by their ending year); '2024 Regular Season' -> 2024."""
    m = re.match(r"(\d{4})(-\d{2,4})?", name)
    if not m:
        return None
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


def echl_site(session):
    """ECHL team logos from the league's own teams page. The ECHL runs on HockeyTech, but its site renders on the
    server and its API key is not public, so this reads the current season's files (``logos/{team}_{season}.png``)
    that the page links; history waits for a key."""
    r = session.get("https://echl.com/teams", headers=UA, timeout=60)
    r.raise_for_status()
    pat = r'<img[^>]*?(?:alt="([^"]+)"[^>]*?src="(https://assets\.leaguestat\.com/echl/logos/(\d+)[^"]*)"'
    pat += r'|src="(https://assets\.leaguestat\.com/echl/logos/(\d+)[^"]*)"[^>]*?alt="([^"]+)")'
    out = {}
    for m in re.finditer(pat, r.text):
        name, url, tid = (m.group(1), m.group(2), m.group(3)) if m.group(2) else (m.group(6), m.group(4), m.group(5))
        out[url] = row("team", "echl", tid, name, url, "echl", program="pro")
    if not out:
        raise RuntimeError("echl_site: the teams page links no team logos")
    return list(out.values())


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


SOURCES = [
    espn_teams, espn_groups, espn_static, nhl_catalog, mlbstatic, nflverse, espn_soccer, espn_seasons, hockeytech, milb, fox_usfl, echl_site, espn_cricket, aaf_strip,
]
