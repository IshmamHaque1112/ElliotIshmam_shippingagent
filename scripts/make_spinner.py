"""Draws static/loading.gif: an amber arc turning on a transparent background. Run once; the GIF is committed."""

import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw

SIZE, FRAMES, SCALE = 48, 12, 4
AMBER = (255, 191, 71)


def frame(step: int) -> Image.Image:
    big = SIZE * SCALE
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    box = [6 * SCALE, 6 * SCALE, big - 6 * SCALE, big - 6 * SCALE]
    draw.ellipse(box, outline=(255, 191, 71, 60), width=5 * SCALE)
    start = step * 360 / FRAMES
    draw.arc(box, start, start + 110, fill=AMBER + (255,), width=5 * SCALE)
    small = img.resize((SIZE, SIZE), Image.LANCZOS)
    flat = Image.new("RGB", (SIZE, SIZE), (53, 9, 45))
    flat.paste(small, mask=small.split()[3])
    return flat.convert("P", palette=Image.ADAPTIVE, colors=32)


def main(out: str) -> None:
    frames = [frame(i) for i in range(FRAMES)]
    frames[0].save(out, save_all=True, append_images=frames[1:], duration=80, loop=0, disposal=1, optimize=False)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).resolve().parent.parent / "static" / "loading.gif"))
