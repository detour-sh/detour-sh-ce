"""Build Detour brand assets from one 16x16 pixel-art mark.

Run from the repository root:

    uv run --with pillow==11.3.0 python scripts/build_brand.py

Outputs app/brand/* (served by the portal) and .github/assets/* (README and
social preview). Edit MARK below and rerun to change the logo.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
APP_BRAND = ROOT / "app" / "brand"
GITHUB_ASSETS = ROOT / ".github" / "assets"

# A road that splits around a stop and merges back: a git merge drawn as a
# traffic detour. "." transparent, "t" tile, "r" road, "a" detour stop.
MARK = [
    ".tttttttttttttt.",
    "tttttrrttttttttt",
    "ttttrrrrtttttttt",
    "tttrrrrrrttttttt",
    "tttttrrrrttttttt",
    "tttttrrtrrtttttt",
    "tttttrrttrraattt",
    "tttttrrtttaaaatt",
    "tttttrrtttaaaatt",
    "tttttrrttttaartt",
    "tttttrrtttttrrtt",
    "tttttrrttttrrttt",
    "tttttrrtttrrtttt",
    "tttttrrrrrrttttt",
    "tttttrrrrrtttttt",
    ".ttttrrtttttttt.",
]

PALETTE = {
    "t": (23, 100, 72, 255),     # portal accent green, #176448
    "r": (238, 243, 239, 255),   # road, #eef3ef
    "a": (242, 179, 61, 255),    # detour-sign amber, #f2b33d
    ".": (0, 0, 0, 0),
}

INK = (24, 33, 29)          # #18211d
PAPER = (238, 243, 239)     # #eef3ef
MUTED = (114, 201, 159)     # #72c99f


def mark_image() -> Image.Image:
    assert len(MARK) == 16 and all(len(row) == 16 for row in MARK), "MARK must be 16x16"
    image = Image.new("RGBA", (16, 16))
    for y, row in enumerate(MARK):
        for x, cell in enumerate(row):
            image.putpixel((x, y), PALETTE[cell])
    return image


def scaled(image: Image.Image, size: int) -> Image.Image:
    return image.resize((size, size), Image.NEAREST)


def svg(image: Image.Image) -> str:
    rects = []
    for y in range(16):
        x = 0
        while x < 16:
            pixel = image.getpixel((x, y))
            if pixel[3] == 0:
                x += 1
                continue
            start = x
            while x < 16 and image.getpixel((x, y)) == pixel:
                x += 1
            color = "#{:02x}{:02x}{:02x}".format(*pixel[:3])
            rects.append(f'<rect x="{start}" y="{y}" width="{x - start}" height="1" fill="{color}"/>')
    body = "".join(rects)
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" '
        f'shape-rendering="crispEdges"><title>Detour</title>{body}</svg>\n'
    )


def pixel_text(text: str, scale: int, color: tuple[int, int, int]) -> Image.Image:
    """Render text at a tiny size without anti-aliasing, then enlarge it."""
    font = ImageFont.load_default_imagefont() if hasattr(ImageFont, "load_default_imagefont") else ImageFont.load_default()
    probe = ImageDraw.Draw(Image.new("L", (1, 1)))
    left, top, right, bottom = probe.textbbox((0, 0), text, font=font)
    small = Image.new("RGBA", (right - left, bottom - top), (0, 0, 0, 0))
    draw = ImageDraw.Draw(small)
    draw.fontmode = "1"
    draw.text((-left, -top), text, font=font, fill=color + (255,))
    return small.resize((small.width * scale, small.height * scale), Image.NEAREST)


def social_preview(mark: Image.Image) -> Image.Image:
    canvas = Image.new("RGB", (1280, 640), INK)
    logo = scaled(mark, 288)
    canvas.paste(logo, (120, 176), logo)
    title = pixel_text("DETOUR", 12, PAPER)
    canvas.paste(title, (480, 214), title)
    subtitle = pixel_text("COMMUNITY EDITION", 5, MUTED)
    canvas.paste(subtitle, (484, 214 + title.height + 36), subtitle)
    tagline = pixel_text("Local context for your AI tools", 4, PAPER)
    canvas.paste(tagline, (484, 214 + title.height + 36 + subtitle.height + 28), tagline)
    return canvas


def main() -> None:
    APP_BRAND.mkdir(parents=True, exist_ok=True)
    GITHUB_ASSETS.mkdir(parents=True, exist_ok=True)
    mark = mark_image()

    (APP_BRAND / "favicon.svg").write_text(svg(mark), encoding="utf-8")
    scaled(mark, 32).save(APP_BRAND / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
    apple = Image.new("RGBA", (180, 180), PALETTE["t"])
    apple.paste(scaled(mark, 160), (10, 10), scaled(mark, 160))
    apple.convert("RGB").save(APP_BRAND / "apple-touch-icon.png", optimize=True)

    (GITHUB_ASSETS / "logo.svg").write_text(svg(mark), encoding="utf-8")
    scaled(mark, 512).save(GITHUB_ASSETS / "logo-512.png", optimize=True)
    social_preview(mark).save(GITHUB_ASSETS / "social-preview.png", optimize=True)
    print("Wrote", *sorted(str(p.relative_to(ROOT)) for p in [*APP_BRAND.iterdir(), *GITHUB_ASSETS.iterdir()]), sep="\n  ")


if __name__ == "__main__":
    main()
