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

