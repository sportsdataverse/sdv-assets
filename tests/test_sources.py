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


def test_espn_seasons_spans_a_reused_logo_and_splits_a_changed_one(monkeypatch):
    monkeypatch.setattr(
        sources, "ESPN_SEASONS", [("ufl", "football", "ufl", [2024, 2025])]
    )
    core = f"{sources.ESPN_CORE}/football/leagues/ufl/seasons"
    session = FakeSession(
        {
            f"{core}/2024/": (200, {"items": [{"$ref": "https://core/t1-2024"}]}),
            f"{core}/2025/": (200, {"items": [{"$ref": "https://core/t1-2025"}]}),
            "https://core/t1-2024": (
                200,
                {
                    "id": 1,
                    "displayName": "Stallions",
                    "logos": [
                        _logo("https://x/a.png"),
                        _logo("https://x/old-dark.png", "dark"),
                    ],
                },
            ),
            "https://core/t1-2025": (
                200,
                {
                    "id": 1,
                    "displayName": "Stallions",
                    "logos": [
                        _logo("https://x/a.png"),
                        _logo("https://x/new-dark.png", "dark"),
                    ],
                },
            ),
        }
    )
    rows = {
        (r["url"], r["valid_from"], r["valid_to"])
        for r in sources.espn_seasons(session)
    }
    assert rows == {
        (
            "https://x/a.png",
            2024,
            2025,
        ),  # the same default logo both seasons: one row, one range
        ("https://x/old-dark.png", 2024, 2024),
        ("https://x/new-dark.png", 2025, 2025),
    }


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

