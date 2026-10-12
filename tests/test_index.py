import gzip
from pathlib import Path

import polars as pl

from sdv_assets import index


def test_main_writes_the_cdn_layout(tmp_path: Path, monkeypatch) -> None:
    teams = pl.DataFrame({"league": ["nfl"], "team_id": ["12"], "abbr": [None]})
    aliases = pl.DataFrame({"league": ["nfl"], "value": ["KC"], "valid_from": pl.Series([None], dtype=pl.Int32)})
    src = tmp_path / "src"
    src.mkdir()
    teams.write_parquet(src / "teams.parquet")
    aliases.write_parquet(src / "aliases.parquet")
    (src / "INDEX_VERSION").write_text("abc123\n")
    monkeypatch.setattr(index, "fetch", lambda name: (src / name).read_bytes())
    out = tmp_path / "out"
    assert index.main(out) == "abc123"
    assert (out / "latest.txt").read_text() == "abc123\n"
    with gzip.open(out / "abc123" / "teams.csv.gz", "rt", encoding="utf-8") as f:
        assert f.read() == "league,team_id,abbr\nnfl,12,\n"


def test_main_refuses_a_version_that_is_not_hex(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(index, "fetch", lambda name: b"../etc\n")
    try:
        index.main(tmp_path)
    except SystemExit as e:
        assert "INDEX_VERSION" in str(e)
    else:
        raise AssertionError("expected SystemExit")
