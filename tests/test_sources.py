import collections
import json

import pytest
import requests

from sdv_assets import sources


def _response(status, body=None):
    r = requests.models.Response()
    r.status_code = status
    r._content = json.dumps(body or {}).encode()
    return r


class FakeSession:
    """Answers each URL from a prefix -> (status, body) table."""

    def __init__(self, table):
        self.table = table

    def get(self, url, **_):
        for prefix, (status, body) in self.table.items():
            if url.startswith(prefix):
                return _response(status, body)
        raise AssertionError(f"unexpected URL {url}")


def _teams(*teams):
    return {"sports": [{"leagues": [{"teams": [{"team": t} for t in teams]}]}]}


def _logo(href, *rel):
    return {"href": href, "rel": ["full", *rel]}


def test_a_competition_without_a_team_list_is_skipped_but_a_block_fails_the_source():
    site = f"{sources.ESPN_SITE}/soccer"
    ok = FakeSession(
        {
            f"{site}/none/": (404, {}),
            f"{site}/eng.1/": (200, _teams({"id": 1, "logos": []})),
        }
    )
    assert sources._espn_site_teams(ok, "soccer", "none") == []
    assert len(sources._espn_site_teams(ok, "soccer", "eng.1")) == 1
    blocked = FakeSession({f"{site}/eng.1/": (403, {})})
    with pytest.raises(requests.HTTPError):
        sources._espn_site_teams(blocked, "soccer", "eng.1")


def test_espn_seasons_uses_league_files_and_gives_a_reused_file_to_its_last_team(monkeypatch):
    monkeypatch.setattr(sources, "ESPN_SEASONS", [("ufl", "football", "ufl", [2024, 2025, 2026])])
    monkeypatch.setattr(sources, "ESPN_SEASON_EXTRA", [("ufl", 2026, 9)])
    core = f"{sources.ESPN_CORE}/football/leagues/ufl/seasons"

    def team(tid, name, abbr):
        guid = f"https://a.espncdn.com/guid/{tid}/logos/default.png"
        return (200, {"id": tid, "displayName": name, "abbreviation": abbr, "logos": [{"href": guid}]})

    session = FakeSession({
        f"{core}/2024/teams?": (200, {"items": [{"$ref": "https://core/rough"}, {"$ref": "https://core/dc"}]}),
        f"{core}/2025/teams?": (200, {"items": [{"$ref": "https://core/rough"}, {"$ref": "https://core/dc"}]}),
        f"{core}/2026/teams?": (200, {"items": [{"$ref": "https://core/gamb"}]}),
        f"{core}/2026/teams/9": team(9, "D.C. Defenders", "DC"),  # left out of 2026's list; respelled, not renamed
        "https://core/rough": team(1, "Houston Roughnecks", "HOU"),
        "https://core/gamb": team(1, "Houston Gamblers", "HOU"),  # same franchise id, rebranded
        "https://core/dc": team(9, "DC Defenders", "DC"),
    })
    rows = {(r["entity_name"], r["variant"], r["url"].split("teamlogos/")[1], r["valid_from"], r["valid_to"])
            for r in sources.espn_seasons(session)}
    assert rows == {
        # hou.png passed from the Roughnecks to the Gamblers (one franchise id, renamed), so today's file is only the Gamblers'
        ("Houston Gamblers", "default", "ufl/500/hou.png", 2026, 2026),
        ("Houston Gamblers", "dark", "ufl/500-dark/hou.png", 2026, 2026),
        ("D.C. Defenders", "default", "ufl/500/dc.png", 2024, 2026),
        ("D.C. Defenders", "dark", "ufl/500-dark/dc.png", 2024, 2026),
    }  # and no guid URL: one guid serves a team's current image for every season


def test_hockey_seasons_are_keyed_by_their_ending_year():
    assert sources._hockey_season("2024-25 Regular Season") == 2025
    assert sources._hockey_season("1999-00 Regular Season") == 2000  # no century slip
    assert sources._hockey_season("2024 Regular Season") == 2024  # PWHL's inaugural, single-year season
    assert sources._hockey_season("All-Star Game") is None
    # junior leagues' spellings (CCHL, OJHL)
    assert sources._hockey_season("CCHL 2026-27") == 2027
    assert sources._hockey_season("OJHL 2019/2020") == 2020
    assert sources._hockey_season("26-27 Regular Season") == 2027
    assert sources._hockey_season("OJHL 22-23") == 2023
    assert sources._hockey_season("CCHL Preseason 2026") is None
    assert sources._hockey_season("19 Tie Break") is None  # a WHL tiebreaker, not a season


def test_fox_logos_resolve_to_the_original_file():
    base = "https://b.fssta.com/uploads/application/usfl/team-logos/"
    assert sources._fox_original(base + "Stallions.vresize.200.200.medium.1.png") == base + "Stallions.png"
    assert sources._fox_original(base + "Stallions.png") == base + "Stallions.png"


def test_mlb_stats_api_calls_skip_the_proxy(monkeypatch):
    """The Stats API answers the decodo proxy with 403, which failed the whole monthly capture."""
    monkeypatch.setenv("SDV_ASSETS_API_PROXY", "http://user:pass@proxy:7000")
    seen = []

    class Recorder:
        def get(self, url, **kw):
            seen.append(kw.get("proxies"))
            return _response(200, {"teams": []})

    sources._get_json(Recorder(), "http://statsapi.mlb.com/api/v1/teams?sportId=1", use_proxy=False)
    sources._get_json(Recorder(), "https://site.web.api.espn.com/x")
    assert seen == [None, {"http": "http://user:pass@proxy:7000", "https": "http://user:pass@proxy:7000"}]
    monkeypatch.setattr(sources, "MILB_LEVELS", {11: "aaa"})
    with pytest.raises(RuntimeError):  # an empty Stats API answer fails the source instead of publishing nothing
        sources.milb(Recorder(), first_season=sources.dt.date.today().year)
    assert seen[-1] is None


def test_milb_skips_a_level_missing_from_a_season_but_keeps_the_rest(monkeypatch):
    monkeypatch.setattr(sources, "MILB_LEVELS", {11: "aaa", 15: "short_a"})
    year = sources.dt.date.today().year
    session = FakeSession({
        "http://statsapi.mlb.com/api/v1/teams?sportId=15": (404, {}),
        "http://statsapi.mlb.com/api/v1/teams?sportId=11": (200, {"teams": [{"id": 234, "name": "Durham Bulls"}]}),
    })
    rows = sources.milb(session, first_season=year)
    assert {(r["entity_id"], r["program"]) for r in rows} == {("234", "aaa")}
    assert len(rows) == len(sources.MILB_FOLDERS)


def test_hockeytech_spans_a_file_reused_across_seasons(monkeypatch):
    monkeypatch.setattr(sources, "HOCKEYTECH", [("ohl", "ohl", "k", "https://ht/feed", "junior")])
    base = "https://ht/feed?feed=modulekit&key=k&client_code=ohl&fmt=json"
    seasons = [{"season_id": "2", "season_name": "2024-25 Regular Season", "career": "1", "playoff": "0"},
               {"season_id": "1", "season_name": "2023-24 Regular Season", "career": "1", "playoff": "0"},
               {"season_id": "9", "season_name": "2024 Playoffs", "career": "1", "playoff": "1"}]
    reused = {"id": "7", "name": "Kitchener Rangers", "team_logo_url": "https://ht/logos/7.png"}
    session = FakeSession({
        f"{base}&view=seasons": (200, {"SiteKit": {"Seasons": seasons}}),
        f"{base}&view=teamsbyseason&season_id=1": (200, {"SiteKit": {"Teamsbyseason": [
            reused, {"id": "8", "name": "London Knights", "team_logo_url": "https://ht/logos/8_1.png"}]}}),
        f"{base}&view=teamsbyseason&season_id=2": (200, {"SiteKit": {"Teamsbyseason": [
            reused, {"id": "8", "name": "London Knights", "team_logo_url": "https://ht/logos/8_2.png"}]}}),
    })
    rows = {(r["url"], r["valid_from"], r["valid_to"]) for r in sources.hockeytech(session)}
    assert rows == {("https://ht/logos/7.png", 2024, 2025),  # one file, both seasons
                    ("https://ht/logos/8_1.png", 2024, 2024), ("https://ht/logos/8_2.png", 2025, 2025)}



def test_espn_cricket_reads_every_curated_team_once():
    rows = sources.espn_cricket(None)
    ids = [r["entity_id"] for r in rows if r["variant"] == "default"]
    assert len(ids) == len(set(ids)) >= 100  # one ESPN row per Cricinfo id
    by_id = {r["entity_id"]: r for r in rows if r["variant"] == "default"}
    assert by_id["6"]["url"].endswith("/cricket/500/6.png") and by_id["6"]["program"] == "mens"  # India
    assert by_id["1381353"]["entity_name"] == "Texas Super Kings"  # Major League Cricket
    assert any(r["program"] == "womens" for r in rows)
    cricinfo = {r["entity_name"]: r["url"] for r in rows if r["variant"] == "cricinfo"}
    assert cricinfo["Texas Super Kings"].startswith(sources.CRICINFO_IMG + "/lsci/db/PICTURES/CMS/")


def test_aaf_crops_are_marked_derived_and_keyed_by_pff_franchise_id():
    rows = sources.aaf_strip(None)
    strip, crops = rows[0], rows[1:]
    assert strip["level"] == "league" and strip["url"] == sources.AAF_STRIP
    assert len(crops) == 8 and {r["source"] for r in crops} == {"aaf-strip-crop"}
    assert {r["variant"] for r in crops} == {"crop_64px"}
    assert [r["entity_id"] for r in crops] == [str(i) for i in range(719, 727)]  # PFF ids, strip order
    assert crops[6]["entity_name"] == "San Antonio Commanders"
    assert all(sources.AAF_CROPS_COMMIT in r["url"] for r in crops)


def test_phf_rows_point_at_wayback_originals_with_season_ranges():
    rows = sources.phf_wayback(None)
    assert rows and all(r["url"].startswith("https://web.archive.org/web/") and "id_/" in r["url"] for r in rows)
    pride = [r for r in rows if r["entity_name"] == "Boston Pride"]
    assert sorted((r["valid_from"], r["valid_to"], r["source"]) for r in pride) == [
        (2016, 2016, "nwhl.co"),  # the inaugural mark, from the league's own 2015-16 site
        (2017, 2023, "shiftstats"),
    ]
    assert any(r["mark_type"] == "wordmark" for r in rows)  # the Beauts script
    assert any(r["valid_from"] is None for r in rows)  # files no schedule dates (2020 Toronto Six, Whale alternate)


def test_ncaa_com_matching_is_exact_after_ap_abbreviations():
    index = {}
    for slug, name in [("fla-southern", "Fla. Southern"), ("southern-nh", "Southern N.H."), ("bethany-ks", "Bethany (KS)"),
                       ("bethany-wv", "Bethany (WV)"), ("baker", "Baker")]:
        index.setdefault(sources._school_key(name), set()).add(slug)
    assert sources._ncaa_slug("Florida Southern", index) == "fla-southern"
    assert sources._ncaa_slug("Southern New Hampshire", index) == "southern-nh"
    assert sources._ncaa_slug("Bethany (KS)", index) == "bethany-ks"
    assert sources._ncaa_slug("Bethany", index) is None  # never guesses between the two Bethanys
    assert sources._ncaa_slug("Baker University", index) == "baker"  # trailing University dropped, still unique
    assert sources._ncaa_slug("Arizona Christian", index) is None


def test_wayback_marks_point_at_archived_originals():
    rows = sources.wayback_marks(None)
    dragons = [r for r in rows if r["entity_name"] == "Seattle Dragons"]
    assert dragons and dragons[0]["url"].startswith("https://web.archive.org/web/") and "id_/" in dragons[0]["url"]
    assert (dragons[0]["valid_from"], dragons[0]["valid_to"]) == (2020, 2020)


def test_espn_teams_pools_several_espn_lists_into_one_row_per_team_id(monkeypatch):
    monkeypatch.setattr(sources, "ESPN_LEAGUES", [
        ("fiba", "basketball", "fiba", None),
        ("fiba", "basketball", "mens-olympics-basketball", "mens"),
        ("fiba", "basketball", "womens-olympics-basketball", "womens"),
        ("iihf", "hockey", "olympics-mens-ice-hockey", "national"),
        ("iihf", "hockey", "olympics-womens-ice-hockey", "national"),
        ("ncaa_whockey", "hockey", "womens-college-hockey", "womens"),
    ])
    monkeypatch.setattr(sources, "ESPN_MISSING_IDS", {"ncaa_whockey": [48]})
    site = "https://site.web.api.espn.com/apis/site/v2/sports"

    def flag(tid, name, iso):
        return {"id": tid, "displayName": name, "logos": [_logo(f"https://a.espncdn.com/i/teamlogos/countries/500/{iso}.png")]}

    aus_w, aus_m, mli = flag(17472, "Australia", "aus"), flag(104882, "Australia", "aus"), flag(83469, "Mali", "mli")
    usa = flag(79, "United States", "usa")
    session = FakeSession({
        # basketball/fiba lists every team twice, and today it is the women's World Cup (Australia's women's id)
        f"{site}/basketball/fiba/teams?": (200, _teams(aus_w, mli, aus_w, mli)),
        f"{site}/basketball/mens-olympics-basketball/teams?": (200, _teams(aus_m)),
        f"{site}/basketball/womens-olympics-basketball/teams?": (200, _teams(aus_w)),
        f"{site}/hockey/olympics-mens-ice-hockey/teams?": (200, _teams(usa)),
        f"{site}/hockey/olympics-womens-ice-hockey/teams?": (200, _teams(usa)),  # one id across genders
        f"{site}/hockey/womens-college-hockey/teams?": (200, _teams()),
        f"{site}/hockey/womens-college-hockey/teams/48": (200, {"team": {
            "id": 48, "displayName": "Delaware Blue Hens", "logos": [_logo(f"{sources.ESPN}/ncaa/500/48.png")]}}),
    })
    rows = sources.espn_teams(session)
    assert sorted((r["league"], r["entity_id"], r["program"]) for r in rows) == [
        ("fiba", "104882", "mens"),
        ("fiba", "17472", "womens"),  # listed by fiba and womens-olympics: one row, the women's program
        ("fiba", "83469", "national"),  # only in basketball/fiba, which names no program
        ("iihf", "79", "national"),
        ("ncaa_whockey", "48", "womens"),  # Delaware, left out of ESPN's list and fetched by id
    ]


def test_nhl_playoff_marks_are_the_conference_final_wordmarks_by_bracket_year(monkeypatch):
    png = "https://assets.nhle.com/logos/playoffs/png"

    def bracket(ecf, wcf):
        return (200, {"series": [
            {"seriesAbbrev": "R1", "seriesLogo": None},
            {"seriesAbbrev": "ECF", "seriesLogo": f"{png}/{ecf}"},
            {"seriesAbbrev": "WCF", "seriesLogo": f"{png}/{wcf}"},
            {"seriesAbbrev": "SCF", "seriesLogo": f"{png}/SCP Logo.png"},
        ]})

    old = ("ecf-20232024-wordmark-en.png", "wcf-20232024-wordmark-en.png")
    session = FakeSession({
        f"{sources.NHL_BRACKET}/2024": bracket(*old),
        f"{sources.NHL_BRACKET}/2025": bracket("ecf-wordmark-en.png", "wcf-wordmark-en.png"),
        f"{sources.NHL_BRACKET}/2026": bracket(*old),  # the 2024 files linked again
        f"{sources.NHL_BRACKET}/2027": (200, {"series": []}),  # a season whose playoffs haven't started
        f"{sources.NHL_BRACKET}/": (404, {}),
    })
    rows = sources.nhl_playoff_marks(session)
    assert {(r["level"], r["league"], r["entity_id"], r["entity_name"], r["mark_type"], r["variant"]) for r in rows} == {
        ("conference", "nhl", "EAS", "Eastern Conference", "wordmark", "conference_final"),
        ("conference", "nhl", "WES", "Western Conference", "wordmark", "conference_final"),
    }
    assert sorted((r["entity_id"], r["url"].rsplit("/", 1)[1], r["valid_from"], r["valid_to"]) for r in rows) == [
        ("EAS", "ecf-20232024-wordmark-en.png", 2024, 2026),  # one row per image: first to last season linked, spanning 2025
        ("EAS", "ecf-wordmark-en.png", 2025, 2025),
        ("WES", "wcf-20232024-wordmark-en.png", 2024, 2026),
        ("WES", "wcf-wordmark-en.png", 2025, 2025),
    ]
    with pytest.raises(RuntimeError):  # no linked mark at all fails the source instead of publishing nothing
        sources.nhl_playoff_marks(FakeSession({f"{sources.NHL_BRACKET}/": (200, {"series": []})}))


def test_hockeytech_carries_the_junior_and_minor_leagues_and_sdv_pys_echl_key():
    by_league = {league: (client, key, program) for league, client, key, _, program in sources.HOCKEYTECH}
    assert len(by_league) == len(sources.HOCKEYTECH) == 20
    assert by_league["echl"] == ("echl", "2c2b89ea7345cae8", "pro")
    assert {lg for lg, (*_, p) in by_league.items() if p == "pro"} == {"pwhl", "ahl", "echl", "sphl", "chl"}
    for league in ["bchl", "ajhl", "sjhl", "ojhl", "cchl", "gojhl", "mhl", "nojhl", "vijhl", "kijhl", "mjhl"]:
        assert by_league[league][0] == league and by_league[league][2] == "junior"


def test_ncaa_com_uses_curated_slugs_before_the_name_match():
    assert sources._ncaa_com_overrides()[("ncaa_mhockey", "132633")][0] == "maryville-mo"  # not cfb 2373 Maryville (TN)
    schools = "".join(f'<a href="/schools/school-{i}">School {i}</a>' for i in range(600))

    class Html:
        def get(self, url, **_):
            r = requests.models.Response()
            r.status_code = 200
            if url.startswith("https://www.ncaa.com/schools-index/"):
                r._content = schools.encode()
            elif "/hockey/mens-college-hockey/" in url:  # Morrisville listed with no logo; Maryville left out
                r._content = json.dumps(_teams({"id": 126813, "location": "SUNY Morrisville",
                                                "displayName": "SUNY Morrisville Mustangs", "logos": []})).encode()
            else:
                r._content = json.dumps(_teams()).encode()
            return r

    rows = sources.ncaa_com(Html())
    assert sorted((r["league"], r["entity_id"], r["entity_name"], r["url"].rsplit("/", 1)[1], r["program"]) for r in rows) == [
        ("ncaa_mhockey", "126813", "SUNY Morrisville Mustangs", "morrisville-st.svg", "mens"),
        ("ncaa_mhockey", "132633", "Maryville (Mo) Saints", "maryville-mo.svg", "mens"),
    ]


def test_f1_media_rows_are_keyed_by_jolpica_constructor_id_and_season():
    rows = sources.f1_media(None)
    assert {(r["level"], r["league"], r["program"], r["source"]) for r in rows} == {("team", "f1", "pro", "formula1.com")}
    assert all(r["valid_from"] == r["valid_to"] for r in rows)
    by = {(r["entity_id"], r["valid_from"], r["variant"]): r["url"] for r in rows}
    assert len(by) == len(rows)  # one row per constructor, season and variant
    svg = "https://media.formula1.com/image/upload/common/f1"
    assert by[("ferrari", 2025, "default")] == f"{svg}/2025/ferrari/2025ferrarilogo"  # no extension: the SVG original
    assert by[("ferrari", 2025, "dark")] == f"{svg}/2025/ferrari/2025ferrarilogowhite"
    assert by[("ferrari", 2025, "on_light")] == f"{svg}/2025/ferrari/2025ferrarilogoblack"
    png = "https://media.formula1.com/content/dam/fom-website/teams"
    assert by[("red_bull", 2019, "default")] == f"{png}/2019/red-bull-racing-logo.png"
    assert ("red_bull", 2019, "dark") not in by  # the pre-2024 path has one PNG per team
    assert ("ferrari", 2024, "dark") in by and ("ferrari", 2024, "on_light") not in by  # 2024 has no logoblack
    assert not [u for u in by.values() if "/2024/" in u and u.endswith("logoblack")]  # every one answers 404
    assert by[("sauber", 2024, "default")].endswith("/2024/kicksauber/2024kicksauberlogo")  # slugs follow rebrands
    assert ("sauber", 2026, "default") not in by and ("audi", 2026, "default") in by
    seasons = collections.Counter(r["valid_from"] for r in rows if r["variant"] == "default")
    assert seasons == {y: 11 if y == 2026 else 10 for y in range(2019, 2027)}  # every grid, 2019-2026
