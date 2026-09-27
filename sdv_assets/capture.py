"""Fetch every candidate mark, keep the real ones, store them by sha256 and update the manifest.

    uv run python -m sdv_assets.capture            # all sources
    uv run python -m sdv_assets.capture nhl_catalog mlbstatic

Images go to $SDV_ASSETS_STORE (default /mnt/sdv_repos/sdv-assets-store), outside git; scripts/publish.sh
uploads them to DigitalOcean Spaces. The manifest (manifest/marks.csv) keeps one row per mark and image:
a URL whose bytes change gets a new row, and the old row keeps its last_seen date, so history accumulates.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import os
import sys
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests
from PIL import Image

from sdv_assets import sources

STORE = Path(os.environ.get("SDV_ASSETS_STORE", "/mnt/sdv_repos/sdv-assets-store"))
MANIFEST = Path(__file__).resolve().parent.parent / "manifest"
PUBLIC_BASE = "https://sdv.nyc3.cdn.digitaloceanspaces.com/assets/public/sha256"
WORKERS = int(os.environ.get("SDV_ASSETS_WORKERS", "4"))

MARK_FIELDS = [
    "level",
    "league",
    "entity_id",
    "entity_name",
    "program",
    "mark_type",
    "variant",
    "valid_from",
    "valid_to",
    "source",
    "url",
]
IMAGE_FIELDS = [
    "sha256",
    "ext",
    "bytes",
    "width",
    "height",
    "archive_url",
    "first_seen",
    "last_seen",
]


def sniff(body: bytes) -> tuple[str | None, int | None, int | None]:
    """Image type and size from the bytes themselves; None when it isn't a usable image.
    Status codes lie: ESPN answers a missing logo with a 1-byte body."""
    if len(body) < 64:
        return None, None, None
    head = body[:512].lstrip()
    if head.startswith(b"<?xml") or head.startswith(b"<svg") or b"<svg" in body[:2048]:
        try:
            root = ET.fromstring(body)
        except ET.ParseError:
            return None, None, None
        return ("svg", None, None) if root.tag.endswith("svg") else (None, None, None)
    try:
        img = Image.open(io.BytesIO(body))
        img.verify()
        img = Image.open(io.BytesIO(body))
    except Exception:
        return None, None, None
    w, h = img.size
    if w < 8 or h < 8:
        return None, None, None
    # a fully transparent or single-colour image is a placeholder, not a mark. Placeholders are small;
    # decoding a 4096x4096 brand-set PNG for this test costs seconds, so large images get verify() only.
    if w * h <= 1_000_000:
        extrema = img.convert("RGBA").getextrema()
        if extrema[3][1] == 0 or (all(lo == hi for lo, hi in extrema[:3]) and extrema[3][0] == extrema[3][1]):
            return None, None, None
    return (
        {"PNG": "png", "JPEG": "jpg", "GIF": "gif", "WEBP": "webp"}.get(
            img.format, img.format.lower()
        ),
        w,
        h,
    )


def fetch(session: requests.Session, url: str) -> dict:
    for attempt in range(4):
        try:
            r = session.get(url, headers=sources.UA, timeout=60)
        except requests.RequestException as e:
            err = str(e)
        else:
            if r.status_code in (429, 500, 502, 503, 504):
                err = f"HTTP {r.status_code}"
            else:
                ext, w, h = (
                    sniff(r.content) if r.status_code == 200 else (None, None, None)
                )
                if not ext:
                    return {
                        "url": url,
                        "ok": False,
                        "error": f"HTTP {r.status_code}, not an image ({len(r.content)} B)",
                    }
                sha = hashlib.sha256(r.content).hexdigest()
                path = STORE / "sha256" / sha[:2] / f"{sha}.{ext}"
                if not path.exists():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(r.content)
                return {
                    "url": url,
                    "ok": True,
                    "sha256": sha,
                    "ext": ext,
                    "bytes": len(r.content),
                    "width": w,
                    "height": h,
                    "archive_url": f"{PUBLIC_BASE}/{sha[:2]}/{sha}.{ext}",
                }
        time.sleep(2**attempt)
    return {"url": url, "ok": False, "error": err}


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = sorted(rows, key=lambda r: tuple(str(r.get(k) or "") for k in fields))
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def main(names: list[str]) -> None:
    today = dt.date.today().isoformat()
    session = requests.Session()
    chosen = [s for s in sources.SOURCES if not names or s.__name__ in names]

    candidates = []
    for src in chosen:
        rows = src(session)
        print(f"{src.__name__}: {len(rows)} candidate marks", flush=True)
        candidates += rows
    urls = sorted({r["url"] for r in candidates})
    print(f"fetching {len(urls)} unique URLs with {WORKERS} workers", flush=True)

    results = {}
    with ThreadPoolExecutor(WORKERS) as pool:
        for i, res in enumerate(pool.map(lambda u: fetch(session, u), urls), 1):
            results[res["url"]] = res
            if i % 500 == 0:
                print(f"  {i}/{len(urls)}", flush=True)

    # carry first_seen forward for images already in the manifest; keep rows this run didn't see
    old = read_csv(MANIFEST / "marks.csv")
    first_seen = {(r["url"], r["sha256"]): r["first_seen"] for r in old}
    kept = {
        (
            r["url"],
            r["sha256"],
            r["level"],
            r["league"],
            r["entity_id"],
            r["variant"],
        ): r
        for r in old
    }
    failures = []
    for c in candidates:
        res = results[c["url"]]
        if not res["ok"]:
            failures.append({**c, "error": res["error"], "checked": today})
            continue
        key = (
            c["url"],
            res["sha256"],
            c["level"],
            c["league"],
            c["entity_id"],
            c["variant"],
        )
        kept[key] = {
            **c,
            **res,
            "first_seen": first_seen.get((c["url"], res["sha256"]), today),
            "last_seen": today,
        }

    write_csv(MANIFEST / "marks.csv", list(kept.values()), MARK_FIELDS + IMAGE_FIELDS)
    # a run over some sources keeps the other sources' failures
    failures += [r for r in read_csv(MANIFEST / "failures.csv") if r["url"] not in results]
    write_csv(MANIFEST / "failures.csv", failures, MARK_FIELDS + ["error", "checked"])
    ok = sum(1 for r in results.values() if r["ok"])
    images = len({r["sha256"] for r in results.values() if r["ok"]})
    print(
        f"done: {ok}/{len(urls)} URLs are images ({images} distinct), {len(kept)} manifest rows, "
        f"{len(failures)} candidate rows failed",
        flush=True,
    )


if __name__ == "__main__":
    main(sys.argv[1:])
