"""Stage 25: sdvplot's team index (teams + aliases) as gzipped CSV, the format sdvplotR reads from the CDN.

Reads the committed index from sdvplot main (no checkout needed); sdvplot's own CI gate (build_index.py --check) owns
its correctness. Writes <out>/<INDEX_VERSION>/{teams,aliases}.csv.gz and <out>/latest.txt.
"""

from __future__ import annotations

import gzip
import io
import re
import sys
import urllib.request
from pathlib import Path

import polars as pl

RAW = "https://raw.githubusercontent.com/sportsdataverse/sdvplot/main/src/sdvplot/data"


def fetch(name: str) -> bytes:
    with urllib.request.urlopen(f"{RAW}/{name}", timeout=60) as r:  # noqa: S310 (a fixed https URL)
        return r.read()


def main(out: Path) -> str:
    ver = fetch("INDEX_VERSION").decode().strip()
    if not re.fullmatch(r"[0-9a-f]{6,64}", ver):
        sys.exit(f"INDEX_VERSION is not a hex version: {ver!r}")
    d = out / ver
    d.mkdir(parents=True, exist_ok=True)
    for name in ("teams", "aliases"):
        df = pl.read_parquet(io.BytesIO(fetch(f"{name}.parquet")))
        with gzip.GzipFile(d / f"{name}.csv.gz", "wb", mtime=0) as f:  # mtime=0: byte-identical reruns
            df.write_csv(f, null_value="")
    (out / "latest.txt").write_text(ver + "\n", encoding="utf-8", newline="
")
    return ver


if __name__ == "__main__":
    print(main(Path(sys.argv[1] if len(sys.argv) > 1 else "build/index")))
