import gzip
from pathlib import Path

import polars as pl
import pytest

from sdv_assets import index


def _src(tmp_path: Path, version: str | None = None) -> tuple[Path, str]:
    teams = pl.DataFrame({"league": ["nfl"], "team_id": ["12"], "abbr": [None]})
    aliases = pl.DataFrame(
        {
            "league": ["nfl"],
            "value": ["KC"],
            "valid_from": pl.Series([None], dtype=pl.Int32),
        }
    )
    src = tmp_path / "src"
    src.mkdir()
    teams.write_parquet(src / "teams.parquet")
    aliases.write_parquet(src / "aliases.parquet")
    ver = version or index.stamp(teams, aliases)
    (src / "INDEX_VERSION").write_text(ver + "\n")
    return src, ver


def test_stamp_matches_sdvplot_build_index() -> None:
    # tools/build_index.py stamp(): sha256 of teams.write_csv() + aliases.write_csv(), first 12 hex
    import hashlib

    t = pl.DataFrame({"a": [1, None]})
    a = pl.DataFrame({"b": ["x"]})
    assert (
        index.stamp(t, a)
        == hashlib.sha256((t.write_csv() + a.write_csv()).encode("utf-8")).hexdigest()[
            :12
        ]
    )


def test_main_writes_the_cdn_layout(tmp_path: Path, monkeypatch) -> None:
    src, ver = _src(tmp_path)
    monkeypatch.setattr(index, "fetch", lambda name: (src / name).read_bytes())
    out = tmp_path / "out"
    assert index.main(out) == ver
    assert (out / "latest.txt").read_text() == ver + "\n"
    with gzip.open(out / ver / "teams.csv.gz", "rt", encoding="utf-8") as f:
        assert f.read() == "league,team_id,abbr\nnfl,12,\n"


def test_main_refuses_a_version_the_fetched_frames_do_not_match(
    tmp_path: Path, monkeypatch
) -> None:
    # INDEX_VERSION and the parquet files are separate CDN-cached requests: a mixed set must not publish
    src, _ = _src(tmp_path, version="abc123abc123")
    monkeypatch.setattr(index, "fetch", lambda name: (src / name).read_bytes())
    out = tmp_path / "out"
    with pytest.raises(SystemExit, match="does not match"):
        index.main(out)
    assert not out.exists()


def test_main_refuses_a_version_that_is_not_hex(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(index, "fetch", lambda name: b"../etc\n")
    with pytest.raises(SystemExit, match="INDEX_VERSION"):
        index.main(tmp_path)
