"""Build Detour brand assets.

Run from the repository root:

    uv run --no-project --with pillow==11.3.0 python scripts/build_brand.py

The mark is a merge road sign crossed with a git merge. A diamond sign holds a
through-lane arrow, and a second lane starts at a git commit node and merges in
at 45 degrees. It's pixel art, drawn per size so 16 px and 32 px both stay
crisp, in black, white, and blue only.

Outputs app/brand/* (served by the portal) and .github/assets/* (README and
social preview).
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
APP_BRAND = ROOT / "app" / "brand"
GITHUB_ASSETS = ROOT / ".github" / "assets"

BLACK = (10, 12, 16, 255)
WHITE = (255, 255, 255, 255)
BLUE = (31, 99, 237, 255)
CLEAR = (0, 0, 0, 0)

# Geometry per grid size. Lanes are (first column, width); rows are inclusive.
SPECS = {
    16: {
        "rim": 7,
        "lane": (7, 2),
        "arrow_top": 2,
        "arrow_widths": (2, 4),
        "lane_end": 13,
        "node": (3, 8, 3),  # left, top, size
        "node_hole": 0,
        "node_round": False,
    },
    32: {
        "rim": 14,
        "lane": (16, 4),
        "arrow_top": 5,
        "arrow_widths": (2, 4, 6, 8, 10),
        "lane_end": 26,
        "node": (8, 18, 6),
        "node_hole": 2,
        "node_round": True,
    },
}


def mark(size: int) -> Image.Image:
    spec = SPECS[size]
    center = (size - 1) / 2
    grid = [[CLEAR] * size for _ in range(size)]
    for y in range(size):
        for x in range(size):
            distance = abs(x - center) + abs(y - center)
            if distance <= size / 2:
                grid[y][x] = BLACK
            if distance <= spec["rim"]:
                grid[y][x] = BLUE

    def paint(y0: int, y1: int, x0: int, x1: int, color=WHITE) -> None:
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                if grid[y][x] in (BLUE, WHITE):
                    grid[y][x] = color

    lane_x, lane_w = spec["lane"]
    lane_mid = lane_x + lane_w / 2
    for i, width in enumerate(spec["arrow_widths"]):
        y = spec["arrow_top"] + i
        paint(y, y, int(lane_mid - width / 2), int(lane_mid + width / 2) - 1)
    paint(spec["arrow_top"] + len(spec["arrow_widths"]), spec["lane_end"], lane_x, lane_x + lane_w - 1)

    node_x, node_y, node_size = spec["node"]
    merge_x = node_x + (node_size - lane_w) // 2
    for k in range(1, lane_x - merge_x + 1):
        paint(node_y - k, node_y - k, merge_x + k, min(merge_x + lane_w - 1 + k, lane_x + lane_w - 1))
    last = node_size - 1
    for y in range(node_size):
        for x in range(node_size):
            if spec["node_round"] and (x in (0, last)) and (y in (0, last)):
                continue
            grid[node_y + y][node_x + x] = WHITE
    hole = spec["node_hole"]
    if hole:
        start = (node_size - hole) // 2
        paint(node_y + start, node_y + start + hole - 1, node_x + start, node_x + start + hole - 1, BLUE)

    image = Image.new("RGBA", (size, size))
    for y, row in enumerate(grid):
        for x, color in enumerate(row):
            image.putpixel((x, y), color)
    return image


def upscale(image: Image.Image, size: int) -> Image.Image:
    return image.resize((size, size), Image.NEAREST)


def svg(image: Image.Image) -> str:
    width, height = image.size
    rects = []
    for y in range(height):
        x = 0
        while x < width:
            pixel = image.getpixel((x, y))
            if pixel[3] == 0:
                x += 1
                continue
            start = x
            while x < width and image.getpixel((x, y)) == pixel:
                x += 1
            color = "#{:02x}{:02x}{:02x}".format(*pixel[:3])
            rects.append(f'<rect x="{start}" y="{y}" width="{x - start}" height="1" fill="{color}"/>')
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'shape-rendering="crispEdges"><title>Detour</title>{"".join(rects)}</svg>\n'
    )


def pixel_text(text: str, scale: int, color: tuple[int, int, int, int]) -> Image.Image:
    """Render text with the built-in bitmap font, then enlarge it."""
    font = ImageFont.load_default_imagefont()
    left, top, right, bottom = ImageDraw.Draw(Image.new("L", (1, 1))).textbbox((0, 0), text, font=font)
    small = Image.new("RGBA", (right - left, bottom - top), CLEAR)
    draw = ImageDraw.Draw(small)
    draw.fontmode = "1"
    draw.text((-left, -top), text, font=font, fill=color)
    return small.resize((small.width * scale, small.height * scale), Image.NEAREST)


def social_preview() -> Image.Image:
    canvas = Image.new("RGBA", (1280, 640), BLACK)
    canvas.alpha_composite(upscale(mark(32), 320), (110, 160))
    x = 500
    title = pixel_text("DETOUR", 12, WHITE)
    canvas.alpha_composite(title, (x, 212))
    edition = pixel_text("COMMUNITY EDITION", 5, BLUE)
    canvas.alpha_composite(edition, (x + 4, 212 + title.height + 34))
    tagline = pixel_text("Local context for your AI tools", 4, WHITE)
    canvas.alpha_composite(tagline, (x + 4, 212 + title.height + 34 + edition.height + 28))
    return canvas.convert("RGB")


def main() -> None:
    APP_BRAND.mkdir(parents=True, exist_ok=True)
    GITHUB_ASSETS.mkdir(parents=True, exist_ok=True)
    m16, m32 = mark(16), mark(32)

    (APP_BRAND / "favicon.svg").write_text(svg(m32), encoding="utf-8")
    m32.save(APP_BRAND / "favicon.ico", sizes=[(16, 16), (32, 32)], append_images=[m16])
    apple = Image.new("RGBA", (180, 180), BLACK)
    apple.alpha_composite(upscale(m32, 160), (10, 10))
    apple.convert("RGB").save(APP_BRAND / "apple-touch-icon.png", optimize=True)

    (GITHUB_ASSETS / "logo.svg").write_text(svg(m32), encoding="utf-8")
    upscale(m32, 512).save(GITHUB_ASSETS / "logo-512.png", optimize=True)
    social_preview().save(GITHUB_ASSETS / "social-preview.png", optimize=True)
    print("ok")


if __name__ == "__main__":
    main()
