#!/usr/bin/env python3
"""Draw the LocalVault AI icon and write a multi-resolution .ico.

    python make_icon.py            -> desktop_assets/icon.ico (+ icon.png preview)

The mark is a light chip on a dark tile: local compute, not a lock and not a
padlock. Sizes used by the taskbar (16, 24, 32, 48) are stored as classic BMP
icons so Windows and WinForms can read them. The 256 image is PNG inside the
.ico, which Explorer uses for the large file icon.

build_exe.bat runs this before PyInstaller. The same icon.ico is the exe icon,
the native-window icon, and the file a pinned shortcut should point at.
"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "desktop_assets")
SIZES = [16, 24, 32, 48, 64, 128, 256]
LIGHT = (236, 236, 236, 255)
DARK = (12, 12, 12, 255)


def _even(n):
    n = int(n)
    if n % 2:
        n += 1
    return n


def draw(size):
    """One crisp RGBA image. Shapes are centered so 16 px still reads as a chip."""
    from PIL import Image, ImageDraw

    s = int(size)
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    radius = 2 if s <= 20 else max(3, int(round(s * 0.18)))
    rim = 1 if s <= 32 else max(2, int(round(s * 0.045)))
    d.rounded_rectangle([0, 0, s - 1, s - 1], radius=radius, fill=DARK)
    d.rounded_rectangle([0, 0, s - 1, s - 1], radius=radius, outline=LIGHT, width=rim)

    margin = max(rim + 2, int(round(s * 0.18)))
    body = _even(round(s * 0.42))
    max_body = s - 2 * margin
    if max_body % 2:
        max_body -= 1
    body = min(body, max(4, max_body))
    bx0 = (s - body) // 2
    bx1 = bx0 + body - 1
    d.rectangle([bx0, bx0, bx1, bx1], fill=LIGHT)

    pin_len = max(1, int(round(s * 0.10)))
    pin_thick = _even(max(2, round(body * 0.22)))
    gap = max(1, int(round(body * 0.16)))
    while pin_thick > 1 and (2 * pin_thick + gap) > body - 2:
        pin_thick -= 1
    while gap > 1 and (2 * pin_thick + gap) > body - 2:
        gap -= 1
    extra = body - (2 * pin_thick + gap)
    if extra % 2 and (2 * pin_thick + gap + 1) <= body:
        gap += 1
        extra -= 1
    y0 = bx0 + max(0, extra // 2)
    bands = (
        (y0, y0 + pin_thick - 1),
        (y0 + pin_thick + gap, y0 + pin_thick + gap + pin_thick - 1),
    )
    px0 = max(rim + 1, bx0 - pin_len)
    px1 = min(s - 2 - rim, bx1 + pin_len)
    for top, bot in bands:
        d.rectangle([px0, top, bx0, bot], fill=LIGHT)
        d.rectangle([bx1, top, px1, bot], fill=LIGHT)

    core = _even(max(2, round(s * 0.12)))
    if core > body - 4:
        core = max(2, body - 4)
        if core % 2:
            core -= 1
    c0 = (s - core) // 2
    d.rectangle([c0, c0, c0 + core - 1, c0 + core - 1], fill=DARK)
    return img


def _bmp_dib(im):
    """ICO color image: bottom-up 32-bit DIB plus a 1-bit AND mask. No file header."""
    im = im.convert("RGBA")
    w, h = im.size
    px = im.load()
    header = struct.pack(
        "<IIIHHIIIIII",
        40, w, h * 2, 1, 32, 0, w * h * 4, 0, 0, 0, 0,
    )
    xor_rows = []
    mask_rows = []
    stride = ((w + 31) // 32) * 4
    for y in range(h - 1, -1, -1):
        color = bytearray()
        mask = bytearray(stride)
        for x in range(w):
            r, g, b, a = px[x, y]
            color += bytes((b, g, r, a))
            if a < 128:
                mask[x // 8] |= 0x80 >> (x % 8)
        xor_rows.append(bytes(color))
        mask_rows.append(bytes(mask))
    return header + b"".join(xor_rows) + b"".join(mask_rows)


def _png_bytes(im):
    import io

    buf = io.BytesIO()
    im.convert("RGBA").save(buf, format="PNG")
    return buf.getvalue()


def ico_bytes(images):
    """Pack images into one .ico. Sizes below 256 are BMP; 256 and above are PNG."""
    images = sorted(images, key=lambda im: im.size[0])
    count = len(images)
    blobs = []
    for im in images:
        w = im.size[0]
        blobs.append(_png_bytes(im) if w >= 256 else _bmp_dib(im))
    header = struct.pack("<HHH", 0, 1, count)
    entries = []
    offset = 6 + 16 * count
    for im, blob in zip(images, blobs):
        w, h = im.size
        entries.append(struct.pack(
            "<BBBBHHII",
            0 if w >= 256 else w,
            0 if h >= 256 else h,
            0, 0, 1, 32, len(blob), offset,
        ))
        offset += len(blob)
    return header + b"".join(entries) + b"".join(blobs)


def main(out_dir=OUT_DIR):
    os.makedirs(out_dir, exist_ok=True)
    images = [draw(n) for n in SIZES]
    ico = os.path.join(out_dir, "icon.ico")
    with open(ico, "wb") as fh:
        fh.write(ico_bytes(images))
    images[-1].save(os.path.join(out_dir, "icon.png"))
    print("wrote", ico)
    return ico


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else OUT_DIR)
