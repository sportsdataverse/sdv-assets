import io

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
