"""International basketball and European hockey sources. Payloads are trimmed from the 2026-11-25 probes."""

import json

import pytest
import requests

from sdv_assets import sources


def _response(status, body=None, url="", text=None):
    r = requests.models.Response()
    r.status_code = status
    r.url = url
    r._content = (text if text is not None else json.dumps(body or {})).encode()
    r.encoding = "utf-8"
    return r


def _html(status, text):
    return _response(status, text=text)


def _nosleep(monkeypatch):
    monkeypatch.setattr(sources.time, "sleep", lambda _s: None)


def _answers(table):
    """A session answering each URL from an exact url -> body table; records the URLs asked."""

    class Session:
        asked = []

        def get(self, url, **_):
            Session.asked.append(url)
            if url not in table:
                raise AssertionError(f"unexpected URL {url}")
            return _response(200, table[url])

    return Session()


class FakeSession:
    """Answers each URL from a prefix -> (status, body) table."""

    def __init__(self, table):
        self.table = table

    def get(self, url, **_):
        for prefix, (status, body) in self.table.items():
            if url.startswith(prefix):
                return _response(status, body)
        raise AssertionError(f"unexpected URL {url}")



def test_euroleague_keys_by_club_code_across_activated_seasons_with_the_latest_name(
    monkeypatch,
):
    _nosleep(monkeypatch)
    crest = "https://media-cdn.cortextech.io/1dU3kpCqReRp93/1BSdBWIjCgOCxM/a844756c-a58d-4666-93b8-48f7337bc79d.png"
    milan = "https://media-cdn.cortextech.io/1dU3kpCqReRp93/1BSdBWIjChWbHH/5c2f0ab6-f86a-4df0-b2f3-f9ab92f7267b.png"
    seasons = {
        "data": [
            {"code": "E2026", "activationDate": "2026-07-01T00:00:00"},
            {"code": "E2016", "activationDate": "2016-07-01T00:00:00"},
            {"code": "E2099", "activationDate": "2099-07-01T00:00:00"},
        ]
    }  # not active yet: never asked
    clubs = {
        "E2016": {
            "data": [
                {"code": "IST", "name": "Anadolu Efes", "images": {"crest": crest}},
                {"code": "BAS", "name": "Baskonia", "images": {"crest": None}},
            ]
        },
        "E2026": {
            "data": [
                {
                    "code": "IST",
                    "name": "Anadolu Efes Istanbul",
                    "images": {"crest": crest},
                },
                {
                    "code": "MIL",
                    "name": "Armani Olimpia Milan",
                    "images": {"crest": milan},
                },
            ]
        },
    }
    sent = []

    class Session:
        def get(self, url, headers=None, **_):
            sent.append(headers["Accept"])
            if url == f"{sources.EUROLEAGUE_API}/seasons":
                return _response(200, seasons)
            return _response(200, clubs[url.split("/seasons/")[1].split("/")[0]])

    rows = sources.euroleague(Session())
    assert set(sent) == {"application/json"}
    assert sorted((r["entity_id"], r["entity_name"], r["url"]) for r in rows) == [
        ("IST", "Anadolu Efes Istanbul", crest),
        ("MIL", "Armani Olimpia Milan", milan),
    ]
    assert {(r["league"], r["source"], r["valid_from"]) for r in rows} == {("euroleague", "euroleague", None)}


def test_bnxt_keys_by_the_stable_uu_team_id_and_spans_a_reused_file(monkeypatch):
    _nosleep(monkeypatch)
    monkeypatch.setattr(sources, "BNXT_COMPETITIONS", {2026: 24, 2027: 25})
    store = "https://bnxt.sportpress.info/storage"
    spirou = f"{store}/3/G/3GsvRCSQNq5oeb3QinpVy3p4H1785845088.png"
    data = {
        24: [
            {
                "id": 503,
                "competition_id": 24,
                "uu_team_id": 1,
                "name": "Spirou",
                "team_logo": {"public_url": spirou},
            },
            {
                "id": 505,
                "competition_id": 24,
                "uu_team_id": 1,
                "name": "Spirou playoff",
                "team_logo": {"public_url": spirou},
            },  # the club's playoff entry: same file, same row
            {
                "id": 504,
                "competition_id": 24,
                "uu_team_id": 2,
                "name": "Filou Oostende",
                "team_logo": None,
            },
        ],
        25: [
            {
                "id": 537,
                "competition_id": 25,
                "uu_team_id": 1,
                "name": "Val-Dieu Spirou Basket",
                "team_logo": {"public_url": spirou},
            },
            {
                "id": 539,
                "competition_id": 25,
                "uu_team_id": 2,
                "name": "Coretec Oostende",
                "team_logo": {"public_url": f"{store}/W/e/WeBMRLIogJuvtJmZrFZMsvxhj1786710770.png"},
            },
        ],
    }

    class Session:
        def get(self, url, headers=None, **_):
            assert headers["X-Authorization"] == sources.BNXT_KEY and url.startswith(sources.BNXT_API)
            return _response(200, {"data": data[int(url.rsplit("=", 1)[1])]})

    rows = sources.bnxt(Session())
    assert sorted((r["entity_id"], r["entity_name"], r["valid_from"], r["valid_to"], r["source"]) for r in rows) == [
        ("1", "Val-Dieu Spirou Basket", 2026, 2027, "sportpress"),
        ("2", "Coretec Oostende", 2027, 2027, "sportpress"),
    ]


def test_aba_unions_clubs_over_standings_pages_under_the_latest_name(monkeypatch):
    _nosleep(monkeypatch)
    y = sources.dt.date.today().year
    monkeypatch.setattr(sources, "ABA_FIRST", y - 1)

    def page(sez, *teams):
        return "".join(
            f'<a href="https://www.aba-liga.com/team/{i}/{sez}/1/0/{slug}/">{name}</a>' for i, slug, name in teams
        )

    pages = {
        y - 2002: page(y - 2002, (43, "borac", "Borac"), (8, "old", "Defunct Club")),
        y - 2001: page(y - 2001, (43, "borac-mozzart", "Borac Mozzart")),
        y - 2000: page(y - 2001, (99, "x", "a page for another season")),  # the site's default answer: no match
    }

    class Session:
        def get(self, url, **_):
            return _response(200, text=pages[int(url.split("/standings/")[1].split("/")[0])])

    rows = sources.aba(Session())
    assert sorted((r["entity_id"], r["entity_name"], r["url"]) for r in rows) == [
        ("43", "Borac Mozzart", "https://www.aba-liga.com/images/club/100x100/43.png"),
        ("8", "Defunct Club", "https://www.aba-liga.com/images/club/100x100/8.png"),
    ]


def test_ibba_keys_by_team_uid_in_end_years_and_normalises_the_logo_host(monkeypatch):
    monkeypatch.setattr(sources, "IBBA_FIRST", 2026)
    monkeypatch.setattr(sources, "IBBA_DELAY", 0)
    monkeypatch.setattr(sources, "_end_year_now", lambda: 2027)
    maccabi = "https://www.basket.co.il/pics/2007/logos/logo_maccabi.png"
    ashdod = "/pics/2026-2027/%D7%A9%D7%95%D7%A0%D7%95%D7%AA/IMG_0026%20(1).PNG"
    session = _answers(
        {
            f"{sources.IBBA_API}?cYear=2026&board_id=5": {
                "teams": [
                    {
                        "TeamUID": "10",
                        "team_year_id": "1109",
                        "year_id": "2026",
                        "teamEng": "Maccabi Playtika Tel Aviv",
                        "team_icon2": maccabi,
                    },
                    {
                        "TeamUID": "8",
                        "team_year_id": "1110",
                        "year_id": "2025",
                        "teamEng": "Hapoel Jerusalem",  # another season
                        "team_icon2": "https://www.basket.co.il/pics/2007/logos/logo_yam.png",
                    },
                ]
            },
            f"{sources.IBBA_API}?cYear=2027&board_id=5": {
                "teams": [
                    {
                        "TeamUID": "62",
                        "team_year_id": "2123",
                        "year_id": "2027",
                        "teamEng": "Maccabi Rotshtein Ashdod",
                        "team_icon2": ashdod,
                    },
                    {
                        "TeamUID": "10",
                        "team_year_id": "2111",
                        "year_id": "2027",
                        "teamEng": "Maccabi Rapyd Tel Aviv",
                        "team_icon2": maccabi,
                    },
                    {
                        "TeamUID": "61",
                        "team_year_id": "2117",
                        "year_id": "2027",
                        "teamEng": "Hapoel Haemek",
                        "team_icon2": "",
                    },
                ]
            },
        }
    )
    rows = sources.ibba(session)
    assert sorted((r["entity_id"], r["entity_name"], r["url"], r["valid_from"], r["valid_to"]) for r in rows) == [
        (
            "10",
            "Maccabi Rapyd Tel Aviv",
            "https://basket.co.il/pics/2007/logos/logo_maccabi.png",
            2026,
            2027,
        ),
        ("62", "Maccabi Rotshtein Ashdod", "https://basket.co.il" + ashdod, 2027, 2027),
    ]
    assert {(r["league"], r["program"], r["source"]) for r in rows} == {("ibba", "pro", "ibba")}


def test_kzs_reads_the_mens_first_league_and_keys_by_club_id(monkeypatch):
    monkeypatch.setattr(sources, "KZS_DELAY", 0)
    monkeypatch.setattr(sources, "_end_year_now", lambda: 2027)
    session = _answers(
        {
            f"{sources.KZS_API}/competitions/?seasonId=27&limit=10000": {
                "status": "OK",
                "data": {
                    "items": [
                        {
                            "id": 608,
                            "name": "Liga OTP banka",
                            "gender": "MALE",
                            "category": "ABSOLUTE",
                            "rank": "FIRST",
                            "type": "LEAGUE",
                        },
                        {
                            "id": 592,
                            "name": "Pripravljalne tekme člani",
                            "gender": "MALE",
                            "category": "ABSOLUTE",
                            "rank": "FIRST",
                            "type": "NATIONAL",
                        },
                        {
                            "id": 612,
                            "name": "1. Ž SKL",
                            "gender": "FEMALE",
                            "category": "ABSOLUTE",
                            "rank": "FIRST",
                            "type": "LEAGUE",
                        },
                    ]
                },
            },
            f"{sources.KZS_API}/teams/?limit=1000&competitionId=608": {
                "status": "OK",
                "data": {
                    "items": [
                        {
                            "id": 18655,
                            "teamApplication": {
                                "id": 47809,
                                "name": "Thermana Laško",
                                "club": {"id": 44},
                            },
                            "logoUuid": "e2d136a7-5b41-4f75-825c-01356ceef2ab",
                        },
                        {
                            "id": 18648,
                            "teamApplication": {
                                "id": 47623,
                                "name": "Šentjur",
                                "club": {"id": 56},
                            },
                            "logoUuid": "",
                        },
                    ]
                },
            },
        }
    )
    rows = sources.kzs(session)
    assert [(r["entity_id"], r["entity_name"], r["url"], r["valid_from"]) for r in rows] == [
        (
            "44",
            "Thermana Laško",
            "https://api.kzs.si/public/images/e2d136a7-5b41-4f75-825c-01356ceef2ab",
            2027,
        )
    ]


LKL_PAGE = """
<select class="form-select" id="team-lkl" name="team"><option value="-">Visos</option>
<option value="6">Rytas</option><option value="41">Hipocredit</option><option value="3">Žalgiris</option></select>
<select id="team-kmt" name="team"><option value="62">Jurbarkas-Karys-Manvesta</option></select>
<div class="battle-row xl sm:w-1/2 justify-center"> <a href="https://lkl.lt/komandos/rytas" class="flex items-center gap-2">
<strong class="sm:w-[35px] text-right"> RYT </strong>
<img src="/media/teams/svg/9HeCOlpWX1ZZmvNBSKRV9od4akkVpxGhqUJANCwR.svg" alt="Rytas"> </a>
<a href="https://lkl.lt/komandos/hipocredit" class="flex items-center gap-2">
<img src="/media/teams/svg/cExP0PVSuiRrENAHoUtVmnfyRPfb3mp4x2hJzu7h.svg" alt="Hipocredit"> <strong> HIP </strong></a>
<a href="https://lkl.lt/komandos/jurbarkas-karys-manvesta" class="flex items-center gap-2">
<img src="/media/teams/svg/LMDiMPFURSoSGf9Lvj63jStRb8qhmrimjtlHQNNx.svg" alt="Jurbarkas-Karys-Manvesta"></a></div>
"""


def test_lkl_joins_the_team_select_ids_to_the_result_row_logos():
    class Session:
        def get(self, url, **_):
            assert url == sources.LKL_PAGE
            return _response(200, text=LKL_PAGE)

    rows = sources.lkl(Session())
    # Žalgiris has no result row on the page; the cup-only team is not in the LKL select
    assert sorted((r["entity_id"], r["entity_name"], r["url"]) for r in rows) == [
        (
            "41",
            "Hipocredit",
            "https://lkl.lt/media/teams/svg/cExP0PVSuiRrENAHoUtVmnfyRPfb3mp4x2hJzu7h.svg",
        ),
        (
            "6",
            "Rytas",
            "https://lkl.lt/media/teams/svg/9HeCOlpWX1ZZmvNBSKRV9od4akkVpxGhqUJANCwR.svg",
        ),
    ]
    assert {(r["league"], r["program"], r["source"]) for r in rows} == {("lkl", "pro", "lkl")}


def test_dbl_reads_the_active_regular_ssgt_and_keys_by_genius_team_id(monkeypatch):
    monkeypatch.setattr(sources.time, "sleep", lambda _: None)

    def ssgt(uuid, state, code, gtype="regular"):
        return {
            "state": state,
            "ssgtUuid": uuid,
            "gameType": {"code": gtype},
            "season": {"code": code},
        }

    def team(tid, instance, name):
        return {
            "info": {
                "teamId": tid,
                "teamInfo": {
                    "teamId": tid,
                    "teamOwnerInstanceId": instance,
                    "teamMedia": f"https://sportality.cdn.s8y.se/team-logos/{instance}.svg",
                    "teamNames": {"full": name},
                },
            }
        }

    session = _answers(
        {
            f"{sources.DBL_API}/statistics-v2/layout-info": [
                {
                    "originalSeasonSeriesGameTypeTeams": [
                        ssgt("al4a0u2go5", "closed", "2025", "finals"),
                        ssgt("qzw34da6jg", "closed", "2025"),
                        ssgt("xs07bd3fmp", "active", "2026"),
                    ]
                }
            ],
            f"{sources.DBL_API}/gameday/team-standings/xs07bd3fmp": {
                "leagueStandings": [
                    team(22248, "bak1_bak", "Bakken Bears"),
                    team(153747, "hol2_hol", "Holbæk-Stenhus"),
                    team(99999, "dbl1_dbl", "Shares the league file"),
                ]
            },
        }
    )
    rows = sources.dbl(session)
    assert [(r["entity_id"], r["entity_name"], r["url"]) for r in rows] == [
        (
            "22248",
            "Bakken Bears",
            "https://sportality.cdn.s8y.se/team-logos/bak1_bak.svg",
        ),
        (
            "153747",
            "Holbæk-Stenhus",
            "https://sportality.cdn.s8y.se/team-logos/hol2_hol.svg",
        ),
    ]
    assert {(r["league"], r["program"], r["source"]) for r in rows} == {("dbl", "pro", "sportality")}


def test_nbl_rosetta_sends_the_gate_keys_by_datacore_id_and_asks_datacore_for_the_original(
    monkeypatch,
):
    monkeypatch.setattr(sources, "NBL_ROSETTA_DELAY", 0)
    dc = "https://images.dc.connect.sportradar.com"
    bodies = {
        "nbl": {
            "type": "teams",
            "data": [
                {
                    "id": "4e2c...",
                    "external_id": "bf1f0e6a-410c-11f0-9d1b-1b3c55488bbb",
                    "name": "Adelaide 36ers",
                    "team_code": "ADL",
                    "team_logo": "https://cdn.nbl.com.au/app_assets/ADL.png",
                    "external_team_logo": f"{dc}/b1sjj/5c1c?size=200&format=webp",
                },
                {
                    "id": "8d26...",
                    "external_id": "bf0e988e-410c-11f0-a62f-1b3c55488bbb",
                    "name": "Adelaide City Eagles",
                    "team_logo": None,
                    "external_team_logo": None,
                },
            ],
        },
        "wnbl": {
            "type": "teams",
            "data": [
                # the site's and DataCore's file are the same image at two sizes: one row
                {
                    "id": "x",
                    "external_id": "e7e7e83d-45e2-11f0-9e55-c5864f4b75a8",
                    "name": "Bendigo Spirit",
                    "team_logo": f"{dc}/b1jrd/fd49?size=800&format=webp",
                    "external_team_logo": f"{dc}/b1jrd/fd49?size=200&format=webp",
                },
            ],
        },
    }
    sent = []

    class Session:
        def get(self, url, headers=None, **_):
            assert url.startswith(sources.NBL_ROSETTA) and url.endswith("/teams?limit=5000")
            sent.append(headers)
            return _response(200, bodies[url.split("/get/")[1].split("/")[0]])

    rows = sources.nbl_rosetta(Session())
    assert all(h["Origin"] == "https://www.nbl.com.au" and h["Referer"] == "https://www.nbl.com.au/" for h in sent)
    assert [(r["league"], r["entity_id"], r["variant"], r["url"]) for r in rows] == [
        (
            "nbl_au",
            "bf1f0e6a-410c-11f0-9d1b-1b3c55488bbb",
            "default",
            "https://cdn.nbl.com.au/app_assets/ADL.png",
        ),
        (
            "nbl_au",
            "bf1f0e6a-410c-11f0-9d1b-1b3c55488bbb",
            "datacore",
            f"{dc}/b1sjj/5c1c?size=RAW",
        ),
        (
            "wnbl",
            "e7e7e83d-45e2-11f0-9e55-c5864f4b75a8",
            "default",
            f"{dc}/b1jrd/fd49?size=RAW",
        ),
    ]
    assert {(r["program"], r["source"]) for r in rows} == {("pro", "nbl_rosetta")}


def test_kbl_sends_the_two_required_headers_and_keeps_the_leading_zero_of_the_team_code():
    teams = [
        {
            "teamCode": "06",
            "teamCodeD": "18",
            "teamName": "수원KT",
            "teamNameFull": "수원 KT 소닉붐",
            "teamNameEn": "KT",
            "teamNameFullEn": "SUWON KT SONICBOOM",
            "teamLogoClass": "kt",
        },
        {
            "teamCode": "64",
            "teamCodeD": "68",
            "teamName": "대구한국가스공사",
            "teamNameEn": "KOREA GAS CORPORATION",
            "teamNameFullEn": "DAEGU KOREAGASCORPORATION PEGASUS",
            "teamLogoClass": "pega",
        },
        {"teamCode": "99", "teamNameFullEn": "NO CLASS", "teamLogoClass": None},
    ]
    sent = {}

    class Session:
        def get(self, url, headers=None, **_):
            assert url == f"{sources.KBL_API}/common/teamList"
            sent.update(headers)
            return _response(200, teams)

    rows = sources.kbl(Session())
    assert sent["Channel"] and sent["TeamCode"]
    assert [(r["league"], r["entity_id"], r["entity_name"], r["url"]) for r in rows] == [
        (
            "kbl",
            "06",
            "SUWON KT SONICBOOM",
            "https://www.kbl.or.kr/assets/img/ico/logo/ic-kt.svg",
        ),
        (
            "kbl",
            "64",
            "DAEGU KOREAGASCORPORATION PEGASUS",
            "https://www.kbl.or.kr/assets/img/ico/logo/ic-pega.svg",
        ),
    ]


def _bleague_row(tid, version, year, code, name):
    return (
        f'<a class="team" href="https://www.bleague.jp/club_detail/?TeamID={tid}">\n'
        f'  <div class="team-logo">\n'
        f'      <img src="https://bleague.bl.kuroco-img.app/v={version}/files/user/common/img/logo/{year}/s/{code}.png"'
        f' alt="{name}">\n  </div>'
    )



def test_bleague_keys_by_club_id_per_season_and_skips_another_seasons_page(monkeypatch):
    first = sources.dt.date.today().year - 1
    monkeypatch.setattr(sources, "BLEAGUE_FIRST", first)
    monkeypatch.setattr(sources, "BLEAGUE_TABS", (1, 3))
    monkeypatch.setattr(sources, "BLEAGUE_DELAY", 0)
    pages = {
        (first, 1): _bleague_row(692, 1783544480, first, "se", "仙台")
        + _bleague_row(692, 1783544480, first, "se", "仙台"),
        (first, 3): None,  # 404
        (first + 1, 1): _bleague_row(692, 1530353291, first, "se", "仙台"),  # not published yet: last season's page
        (first + 1, 3): _bleague_row(2728, 1782836665, first + 1, "su", "静岡"),
    }

    class Session:
        def get(self, url, **_):
            q = dict(p.split("=") for p in url.split("?", 1)[1].split("&"))
            page = pages[(int(q["year"]), int(q["tab"]))]
            return _html(404, "") if page is None else _html(200, page)

    rows = sources.bleague(Session())
    cdn = "https://bleague.bl.kuroco-img.app/files/user/common/img/logo"
    assert sorted((r["entity_id"], r["entity_name"], r["url"], r["valid_from"], r["valid_to"]) for r in rows) == [
        ("2728", "静岡", f"{cdn}/{first + 1}/s/su.png", first + 2, first + 2),
        ("692", "仙台", f"{cdn}/{first}/s/se.png", first + 1, first + 1),
    ]
    assert {(r["league"], r["source"]) for r in rows} == {("bleague", "bleague")}


def test_shl_keys_by_statnet_code_and_names_a_past_teams_file_after_its_site_id(
    monkeypatch,
):
    monkeypatch.setattr(sources, "SHL_DELAY", 0)
    cdn = "https://sportality.cdn.s8y.se/team-logos"

    def team(site, code, long):
        return {
            "uuid": "x",
            "instanceId": site,
            "name": {"code": code, "medium": long},
            "teamNames": {"code": code, "long": long},
        }

    layout = [
        {
            "ssgtDefaultValue": {"ssgtUuid": "qa98unlbd6"},
            "originalSeasonSeriesGameTypeTeams": [
                {
                    "ssgtUuid": "qd0-5CQa3HY64",
                    "season": {"code": "1985"},
                    "teams": [
                        team("aik1_aik", "AIK", "AIK"),
                        team("bif1_bif", "BIF", "Brynäs IF"),
                    ],
                },
                {
                    "ssgtUuid": "qa98unlbd6",
                    "season": {"code": "2026"},
                    "teams": [
                        team("bif1_bif", "BIF", "Brynäs IF"),
                        team("ohk1_ohk", "ÖRE", "Örebro HK"),
                    ],
                },
            ],
        }
    ]
    sites = [
        {
            "id": "bif1_bif",
            "instanceId": "bif1_bif",
            "name": "Brynäs IF",
            "icon": f"{cdn}/bif1_bif.svg",
        },
        {
            "id": "ohk1_ohk",
            "instanceId": "ohk1_ohk",
            "name": "Örebro Hockey",
            "icon": f"{cdn}/ohk1_ohk.svg",
        },
    ]
    session = FakeSession(
        {
            f"{sources.SHL_API}/statistics-v2/layout-info": (200, layout),
            f"{sources.SHL_API}/sports-v2/all-sites/qa98unlbd6": (200, sites),
        }
    )
    rows = sources.shl(session)
    # AIK is not in the active season: its file is the CDN's <site id>.svg
    assert sorted((r["entity_id"], r["entity_name"], r["url"]) for r in rows) == [
        ("AIK", "AIK", f"{cdn}/aik1_aik.svg"),
        ("BIF", "Brynäs IF", f"{cdn}/bif1_bif.svg"),
        ("ÖRE", "Örebro HK", f"{cdn}/ohk1_ohk.svg"),
    ]
    assert {(r["league"], r["program"], r["source"], r["valid_from"]) for r in rows} == {("shl", "pro", "shl", None)}


def test_ha_keys_by_statnet_id_sends_start_years_and_keeps_the_latest_file(monkeypatch):
    today = sources.dt.date.today()
    last = today.year + (today.month >= 7)
    monkeypatch.setattr(sources, "HA_FIRST", last - 1)
    monkeypatch.setattr(sources, "HA_DELAY", 0)
    media = "https://ha-media.hadigital.se/ha-league/statnet-logos"

    def team(sid, code, name, ver):
        return {
            "statNetId": sid,
            "teamCode": code,
            "name": name,
            "fullName": name,
            "logos": {
                "icon": f"{media}/{sid.lower()}_icon.webp?ver={ver}",
                "xlarge": f"{media}/{sid.lower()}_xlarge.webp?ver={ver}",
            },
        }

    answers = {
        str(last - 2): {
            "results": [],
            "teams": [
                team("AIK", "AIK", "AIK", "202609230706"),
                team("TRO", "TRO", "Troja", "202609230706"),
            ],
        },
        str(last - 1): {
            "results": [],
            "teams": [
                team("AIK", "AIK", "AIK", "202611250902"),
                team("MIK", "MORA", "Mora", "202609230706"),
                {"statNetId": "VIS", "teamCode": "VIS", "name": "Visby", "logos": {}},
            ],
        },
    }
    sent = []

    class Session:
        def post(self, url, json=None, **_):
            assert url == sources.HA_API and json["phase"] == "HA"
            sent.append(json["season"])
            return _response(200, answers[json["season"]])

    rows = sources.ha(Session())
    assert sent == [
        str(last - 2),
        str(last - 1),
    ]  # END years last-1, last on the wire as START years
    assert sorted((r["entity_id"], r["url"].rsplit("/", 1)[1]) for r in rows) == [
        ("AIK", "aik_xlarge.webp?ver=202611250902"),
        ("MIK", "mik_xlarge.webp?ver=202609230706"),
        ("TRO", "tro_xlarge.webp?ver=202609230706"),
    ]
    assert {(r["league"], r["source"], r["valid_from"], r["valid_to"]) for r in rows} == {
        ("ha", "hockeyallsvenskan", None, None)
    }


def test_magnus_keeps_teams_that_entered_a_ligue_magnus_competition(monkeypatch):
    monkeypatch.setattr(sources, "MAGNUS_DELAY", 0)
    store = "https://hockeynet.fr/storage"
    comps = {
        "success": True,
        "data": {"data": [{"id": 1, "saison": 2021}, {"id": 240, "saison": 2027}]},
    }
    teams = {
        "success": True,
        "data": {
            "data": [
                {
                    "id": 75,
                    "libelle_complet": "Anglet Hormadi",
                    "logo": f"{store}/logos_equipes/385/sEJ00Dul.png",
                    "id_compet": [1, 19, 240],
                },
                {
                    "id": 423,
                    "libelle_complet": "Scorpions de Mulhouse",
                    "logo": f"{store}/logos_structures/mul.png",
                    "id_compet": [1],
                },
                {
                    "id": 1,
                    "libelle_complet": "Remparts de Tours",
                    "logo": f"{store}/logos_structures/2r16NH0q.png",
                    "id_compet": [2, 19],
                },
                {
                    "id": 3409,
                    "libelle_complet": "Diables Rouges de Briançon",
                    "logo": None,
                    "id_compet": [240],
                },
            ]
        },
    }
    session = FakeSession(
        {
            f"{sources.MAGNUS_AJAX}?action=get_competition_by_type&id_type=1": (
                200,
                comps,
            ),
            f"{sources.MAGNUS_AJAX}?action=get_equipes": (200, teams),
        }
    )
    rows = sources.magnus(session)
    # Tours never played the Ligue Magnus; Briançon has no logo
    assert [(r["league"], r["entity_id"], r["entity_name"], r["url"]) for r in rows] == [
        ("magnus", "75", "Anglet Hormadi", f"{store}/logos_equipes/385/sEJ00Dul.png"),
        ("magnus", "423", "Scorpions de Mulhouse", f"{store}/logos_structures/mul.png"),
    ]
    assert {(r["program"], r["source"]) for r in rows} == {("pro", "hockeyfrance")}


# json.esports.cz/hokejcz/scoreboard/2026-11-24.json, trimmed to two Extraliga games and the Maxa liga key
SCOREBOARD = {
    "101": {
        "league_name": "Tipsport extraliga",
        "matches": [
            {
                "hokejcz_id": 2928402,
                "onlajny_id": 533068,
                "home": {
                    "hokejcz_id": 22,
                    "onlajny_id": 163,
                    "name": "HC KOMETA BRNO",
                    "short_name": "Kometa",
                    "shortcut": "KOM",
                },
                "visitor": {
                    "hokejcz_id": 8,
                    "onlajny_id": 156,
                    "name": "HC Sparta Praha",
                    "short_name": "Sparta",
                    "shortcut": "SPA",
                },
            },
            {
                "hokejcz_id": 2928403,
                "onlajny_id": 533062,
                "home": {
                    "hokejcz_id": 89,
                    "onlajny_id": 158,
                    "name": "BK Mladá Boleslav",
                    "short_name": "Mladá Boleslav",
                    "shortcut": "MBL",
                },
                "visitor": {
                    "hokejcz_id": 828,
                    "onlajny_id": 162,
                    "name": "Rytíři Kladno",
                    "short_name": "Kladno",
                    "shortcut": "KLA",
                },
            },
        ],
    },
    "82": {
        "league_name": "Maxa liga",
        "matches": [
            {
                "home": {
                    "hokejcz_id": 9,
                    "onlajny_id": 167,
                    "name": "HC Slavia Praha",
                    "shortcut": "SLA",
                },  # Maxa liga: not Extraliga
                "visitor": {},
            }
        ],
    },
}


def test_elh_keys_by_hokejcz_team_id_and_skips_days_outside_the_window(monkeypatch):
    monkeypatch.setattr(sources, "ELH_DELAY", 0)
    monkeypatch.setattr(sources, "ELH_DAYS", 3)
    yesterday = str(sources.dt.date.today() - sources.dt.timedelta(days=1))
    seen = []

    class Session:
        def get(self, url, **_):
            assert url.startswith(sources.ELH_SCOREBOARD)
            seen.append(url)
            return _response(200, SCOREBOARD) if url.endswith(f"/{yesterday}.json") else _response(404)

    rows = sources.elh(Session())
    assert len(seen) == 3  # today is never asked; each of the last three days once
    logos = "https://s3-eu-west-1.amazonaws.com/hokej.cz/logos/200"
    assert [(r["league"], r["entity_id"], r["entity_name"], r["url"], r["source"]) for r in rows] == [
        (
            "elh",
            "8",
            "HC Sparta Praha",
            f"{logos}/8.png",
            "hokej.cz",
        ),  # hokej.cz id, not onlajny 156
        ("elh", "22", "HC KOMETA BRNO", f"{logos}/22.png", "hokej.cz"),
        ("elh", "89", "BK Mladá Boleslav", f"{logos}/89.png", "hokej.cz"),
        ("elh", "828", "Rytíři Kladno", f"{logos}/828.png", "hokej.cz"),
    ]


def test_elh_fails_when_no_day_had_a_game(monkeypatch):
    monkeypatch.setattr(sources, "ELH_DELAY", 0)

    class Session:
        def get(self, url, **_):
            return _response(404)

    with pytest.raises(RuntimeError, match="elh"):
        sources.elh(Session())


def test_elh_a_blocked_scoreboard_fails_the_source(monkeypatch):
    monkeypatch.setattr(sources, "ELH_DELAY", 0)

    class Session:
        def get(self, url, **_):
            return _response(403)

    with pytest.raises(requests.HTTPError):
        sources.elh(Session())


def test_page_retries_a_drop_or_a_5xx_once_skips_a_404_and_stops_on_a_block(monkeypatch):
    _nosleep(monkeypatch)

    class Session:
        def __init__(self, *answers):
            self.answers = list(answers)

        def get(self, url, **_):
            a = self.answers.pop(0)
            if isinstance(a, Exception):
                raise a
            return _response(a, text="<html>ok</html>")

    assert sources._page(Session(requests.ConnectionError(), 200), "u") == "<html>ok</html>"
    assert sources._page(Session(500, 200), "u") == "<html>ok</html>"
    assert sources._page(Session(500, 500), "u") is None
    assert sources._page(Session(404), "u") is None
    with pytest.raises(requests.HTTPError):
        sources._page(Session(403), "u")
    with pytest.raises(requests.ConnectionError):
        sources._page(Session(requests.ConnectionError(), requests.ConnectionError()), "u")
