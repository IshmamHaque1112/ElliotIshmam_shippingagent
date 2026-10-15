"""Reads the colour variables in static/app.css and checks WCAG contrast. Exit 1 if any pair is too low.

Text pairs need 4.5 to 1; chart colours, borders of form fields and the focus ring need 3 to 1.
"""

import re
import sys
from pathlib import Path

CSS = Path(__file__).resolve().parent.parent / "static" / "app.css"

TEXT = [("ink", "bg"), ("ink", "panel"), ("muted", "bg"), ("muted", "panel"), ("head-ink", "head"), ("accent-ink", "accent"),
        ("accent", "bg"), ("accent", "panel"), ("bad", "bad-bg"), ("warn", "warn-bg"), ("ok", "ok-bg"), ("unk", "unk-bg"), ("late", "late-bg"),
        ("ink", "soft"), ("bad", "panel"), ("warn", "panel"), ("ok", "panel"), ("unk", "panel"), ("late", "panel")]
GRAPHIC = [("field", "bg"), ("field", "panel"), ("focus", "bg"), ("focus", "panel"), ("focus", "head"),
           ("bad", "panel"), ("warn", "panel"), ("ok", "panel"), ("unk", "panel"), ("late", "panel"), ("accent", "panel")]


def variables(text: str) -> dict[str, str]:
    root = re.search(r":root\s*\{(.*?)\}", text, re.S).group(1)
    return dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})", root))


def luminance(hex_color: str) -> float:
    channels = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def ratio(a: str, b: str) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def check(css_path: Path = CSS) -> list[tuple[str, str, float, float, bool]]:
    v = variables(css_path.read_text(encoding="utf-8"))
    rows = []
    for pairs, need in ((TEXT, 4.5), (GRAPHIC, 3.0)):
        for fg, bg in pairs:
            r = ratio(v[fg], v[bg])
            rows.append((fg, bg, round(r, 2), need, r >= need))
    return rows


if __name__ == "__main__":
    rows = check()
    for fg, bg, r, need, ok in rows:
        print(f"{'ok  ' if ok else 'FAIL'} {fg:>10} on {bg:<9} {r:>5.2f}  (needs {need})")
    sys.exit(0 if all(row[4] for row in rows) else 1)
