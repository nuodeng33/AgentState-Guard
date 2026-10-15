#!/usr/bin/env python3
"""Generate AgentState Guard launcher/desktop brand icons.

Renders the same shield-with-three-nodes brand mark used by the web AppShell
(`ShieldNodesMark`) onto the R4 V1 dark console background and writes:

  desktop/src-tauri/icons/32x32.png
  desktop/src-tauri/icons/128x128.png
  desktop/src-tauri/icons/icon.ico      (multi-size ICO)
  android/app/src/main/res/mipmap-*/ic_launcher.png
  android/app/src/main/res/mipmap-*/ic_launcher_round.png
  android/app/src/main/res/mipmap-*/ic_launcher_foreground.png
  android/app/src/main/res/values/ic_launcher_background.xml

The mark geometry is taken verbatim from web/src/components/AppShell.tsx so the
installed app icon matches the in-app brand mark. Palette uses the shared theme
tokens from web/src/styles/app.css.

Requirements: Pillow (run inside an isolated venv; the script steals nothing
from the backend authority — it only writes image resources).
"""

from __future__ import annotations

import io
import os
import struct

from PIL import Image, ImageDraw

# Shield path from web/src/components/AppShell.tsx (ShieldNodesMark), rendered
# as dense polylines for rasterization at icon sizes.

# ---- palette (web/src/styles/app.css tokens) ----
BACKGROUND = (15, 23, 42, 255)      # --background #0f172a
SHIELD_BODY = (30, 41, 59, 255)     # --surface #1e293b
PRIMARY = (99, 102, 241, 255)       # --primary #6366f1
SECONDARY = (139, 92, 246, 255)     # --secondary #8b5cf6

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def _cubic(p0: tuple[float, float], c1: tuple[float, float],
           c2: tuple[float, float], p1: tuple[float, float],
           steps: int = 24) -> list[tuple[float, float]]:
    pts = []
    for i in range(1, steps + 1):
        t = i / steps
        mt = 1.0 - t
        x = mt**3 * p0[0] + 3 * mt * mt * t * c1[0] + 3 * mt * t * t * c2[0] + t**3 * p1[0]
        y = mt**3 * p0[1] + 3 * mt * mt * t * c1[1] + 3 * mt * t * t * c2[1] + t**3 * p1[1]
        pts.append((x, y))
    return pts


def shield_polygon(cx: float, cy: float, scale: float) -> list[tuple[float, float]]:
    """Return the shield silhouette polygon centered at (cx, cy).

    Matches the AppShell brand path exactly:
      M12 2.5 L4.5 5.5 V10.7 c0 4.6 3.2 8.9 7.5 10.3
      c4.3 -1.4 7.5 -5.7 7.5 -10.3 V5.5 L12 2.5 Z
    """
    verts: list[tuple[float, float]] = [
        (12.0, 2.5),
        (4.5, 5.5),
        (4.5, 10.7),
    ]
    # Left wall cubic: from (4.5,10.7) cp (4.5,15.3) (7.7,19.6) to (12,21)
    left = _cubic((4.5, 10.7), (4.5, 15.3), (7.7, 19.6), (12.0, 21.0))
    verts += left[:-1]  # tip appended via right side
    # Right wall cubic: from (12,21) back up cp (16.3,19.6) (19.5,15.3) to (19.5,10.7)
    right_desc = _cubic((19.5, 10.7), (19.5, 15.3), (16.3, 19.6), (12.0, 21.0))
    verts += list(reversed(right_desc))
    verts += [(19.5, 5.5)]
    s = scale / 24.0
    ox, oy = cx - 12.0 * s, cy - 12.0 * s
    return [(ox + x * s, oy + y * s) for x, y in verts]


def v24(x: float, y: float, cx: float, cy: float, scale: float) -> tuple[float, float]:
    s = scale / 24.0
    return (cx + (x - 12.0) * s, cy + (y - 12.0) * s)


def render_icon(size: int, *, rounded: bool = False, foreground: bool = False) -> Image.Image:
    """Render one icon.

    size:       output edge length in px (rendered at 4x then downsampled).
    rounded:    apply an Android-adaptive rounded-square mask.
    foreground: transparent background + mark only (Android foreground layer).
    """
    ss = 4  # supersample
    S = size * ss
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    if foreground:
        # Android foreground layer: safe zone is the center 2/3.
        shield_s = S * 0.56
    else:
        shield_s = S * 0.80

    cx = cy = S / 2.0

    if not foreground:
        d.rectangle([0, 0, S, S], fill=BACKGROUND)

    body = shield_polygon(cx, cy + S * 0.005, shield_s)
    # Slightly inset outline polygon for the stroke ring.
    outline = shield_polygon(cx, cy + S * 0.005, shield_s * 0.94)

    d.polygon(body, fill=SHIELD_BODY)
    d.line(outline + [outline[0]], fill=PRIMARY, width=max(2, int(S * 0.028)), joint="curve")

    s = shield_s * 0.94
    lw = max(2, int(S * 0.020))
    # Trace: M8.5 9.5 L12 13.2 L15.5 9.5
    p1 = v24(8.5, 9.5, cx, cy, s)
    p2 = v24(12.0, 13.2, cx, cy, s)
    p3 = v24(15.5, 9.5, cx, cy, s)
    d.line([p1, p2, p3], fill=SECONDARY, width=lw, joint="curve")

    r_small = S * 0.030
    r_big = S * 0.040
    for (px, py), col, fill in (
        ((8.5, 9.5), SECONDARY, None),
        ((15.5, 9.5), SECONDARY, None),
        ((12.0, 13.8), PRIMARY, PRIMARY),
    ):
        x, y = v24(px, py, cx, cy, s)
        if fill is None:
            d.ellipse([x - r_small, y - r_small, x + r_small, y + r_small], outline=col, width=lw)
        else:
            d.ellipse([x - r_big, y - r_big, x + r_big, y + r_big], fill=col)

    if rounded:
        mask = Image.new("L", (S, S), 0)
        md = ImageDraw.Draw(mask)
        radius = int(S * 0.18)
        md.rounded_rectangle([0, 0, S - 1, S - 1], radius=radius, fill=255)
        img.putalpha(mask)

    return img.resize((size, size), Image.LANCZOS)


def write_ico(path: str) -> None:
    """Write a multi-size ICO by hand (Pillow's ICO encoder is unreliable)."""
    sizes = [16, 24, 32, 48, 64, 128, 256]
    blobs: list[bytes] = []
    for s in sizes:
        img = render_icon(s)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        blobs.append(buf.getvalue())
    header = struct.pack("<HHH", 0, 1, len(blobs))
    offset = 6 + 16 * len(blobs)
    entries = b""
    for s, blob in zip(sizes, blobs):
        dim = 0 if s >= 256 else s
        entries += struct.pack(
            "<BBBBHHII", dim, dim, 0, 0, 1, 32, len(blob), offset
        )
        offset += len(blob)
    with open(path, "wb") as f:
        f.write(header + entries + b"".join(blobs))


def main() -> None:
    desktop = os.path.join(ROOT, "desktop", "src-tauri", "icons")
    os.makedirs(desktop, exist_ok=True)
    render_icon(32).save(os.path.join(desktop, "32x32.png"))
    render_icon(128).save(os.path.join(desktop, "128x128.png"))
    write_ico(os.path.join(desktop, "icon.ico"))

    android_res = os.path.join(ROOT, "android", "app", "src", "main", "res")
    dens = {"mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192}
    for dens_name, px in dens.items():
        d = os.path.join(android_res, f"mipmap-{dens_name}")
        os.makedirs(d, exist_ok=True)
        render_icon(px).save(os.path.join(d, "ic_launcher.png"))
        render_icon(px, rounded=True).save(os.path.join(d, "ic_launcher_round.png"))
        # Foreground layer is rendered at 108dp grid: px * 108/48
        fg = render_icon(px * 108 // 48, foreground=True)
        fg.save(os.path.join(d, "ic_launcher_foreground.png"))

    values = os.path.join(android_res, "values")
    os.makedirs(values, exist_ok=True)
    with open(os.path.join(values, "ic_launcher_background.xml"), "w", encoding="utf-8") as f:
        f.write(
            '<?xml version="1.0" encoding="utf-8"?>\n'
            "<!-- R4 V1 console background token (#0f172a from web/src/styles/app.css) -->\n"
            '<resources>\n'
            '    <color name="ic_launcher_background">#0F172A</color>\n'
            "</resources>\n"
        )
    print("icons generated")


if __name__ == "__main__":
    main()
