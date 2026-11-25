import io

import pytest
from PIL import Image

from sdv_assets.capture import sniff


def png(img):
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def test_sniff_keeps_real_images_and_rejects_placeholders():
    logo = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    logo.paste((200, 30, 30, 255), (16, 16, 48, 48))
    assert sniff(png(logo)) == ("png", 64, 64)
    # ESPN answers a missing logo with a 1-byte body
    assert sniff(b"x") == (None, None, None)
    # fully transparent and single-colour squares are placeholders
    assert sniff(png(Image.new("RGBA", (64, 64), (0, 0, 0, 0))))[0] is None
    assert sniff(png(Image.new("RGBA", (64, 64), (255, 255, 255, 255))))[0] is None
    # a one-colour mark on transparency is still a mark (mono variants)
    mono = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    mono.paste((0, 0, 0, 255), (8, 8, 56, 56))
    assert sniff(png(mono))[0] == "png"
    assert sniff(b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect width="10" height="10"/></svg>' + b" " * 64)[0] == "svg"
    assert sniff(b"<html><body>Not found</body></html>" + b" " * 64)[0] is None


def test_sniff_rejects_large_placeholders_too():
    # any size: a 1001 x 1000 blank is still a placeholder
    assert sniff(png(Image.new("RGBA", (1001, 1000), (0, 0, 0, 0))))[0] is None
    assert sniff(png(Image.new("RGB", (1001, 1000), (255, 255, 255))))[0] is None


def test_a_failing_source_does_not_stop_the_others_but_fails_the_run(tmp_path, monkeypatch):
    from sdv_assets import capture, sources

    def down(_session):
        raise RuntimeError("host down")

    def up(_session):
        return [sources.row("team", "x", 1, "X", "https://img/1.png", "x")]

    monkeypatch.setattr(capture, "MANIFEST", tmp_path)
    monkeypatch.setattr(sources, "SOURCES", [down, up])
    monkeypatch.setattr(capture, "fetch", lambda _s, url: {"url": url, "ok": False, "error": "offline"})
    with pytest.raises(SystemExit, match="down"):
        capture.main([])
    assert [r["url"] for r in capture.read_csv(tmp_path / "failures.csv")] == ["https://img/1.png"]
