#!/usr/bin/env python3
"""Generate the Local Chat app icon (dark theme) with Pillow.

    python make_icon.py            -> desktop_assets/icon.ico (+ icon.png preview)

Only needed at build time; build_exe.bat runs it automatically.
"""
import os
import sys

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "desktop_assets")
SIZES = [16, 24, 32, 48, 64, 128, 256]


def draw(size=1024):
    s = size
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # rounded dark tile with a faint border
    pad = int(s * 0.04)
    d.rounded_rectangle([pad, pad, s - pad, s - pad], radius=int(s * 0.22),
                        fill=(10, 10, 10, 255), outline=(58, 58, 58, 255), width=max(2, s // 64))
    # speech bubble
    bx0, by0, bx1, by1 = int(s * 0.20), int(s * 0.24), int(s * 0.80), int(s * 0.68)
    d.rounded_rectangle([bx0, by0, bx1, by1], radius=int(s * 0.12), fill=(236, 236, 236, 255))
    d.polygon([(int(s * 0.32), by1 - 2), (int(s * 0.28), int(s * 0.82)), (int(s * 0.48), by1 - 2)],
              fill=(236, 236, 236, 255))
    # three dots
    r = int(s * 0.045)
    cy = (by0 + by1) // 2
    for cx in (int(s * 0.36), int(s * 0.50), int(s * 0.64)):
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(10, 10, 10, 255))
    return img


def main(out_dir=OUT_DIR):
    os.makedirs(out_dir, exist_ok=True)
    big = draw()
    ico = os.path.join(out_dir, "icon.ico")
    big.resize((256, 256), Image.LANCZOS).save(ico, format="ICO", sizes=[(n, n) for n in SIZES])
    big.resize((256, 256), Image.LANCZOS).save(os.path.join(out_dir, "icon.png"))
    print("wrote", ico)
    return ico


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else OUT_DIR)
