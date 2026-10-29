"""Generate small two-segment SVG badges in the Crimson palette.

textLength pins each label to a fixed width, so the badge keeps its shape
whatever font GitHub's image renderer falls back to.
"""
import sys
from pathlib import Path

SURFACE = "#231016"
VALUE_BG = "#9e1b32"
BORDER = "#48202c"
TEXT = "#f4e9ed"
MUTED = "#bc9aa4"

CW = 7.1   # approx advance width at 11px monospace
H = 22


def badge(label: str, value: str) -> str:
    lw = round(len(label) * CW) + 16
    vw = round(len(value) * CW) + 16
    w = lw + vw
    lx, vx = lw / 2, lw + vw / 2
    ltl, vtl = len(label) * CW, len(value) * CW
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{H}" role="img" aria-label="{label}: {value}">
  <clipPath id="r"><rect width="{w}" height="{H}" rx="4"/></clipPath>
  <g clip-path="url(#r)" font-family="ui-monospace,'JetBrains Mono',Menlo,monospace" font-size="11" font-weight="600">
    <rect width="{lw}" height="{H}" fill="{SURFACE}"/>
    <rect x="{lw}" width="{vw}" height="{H}" fill="{VALUE_BG}"/>
    <rect width="{w}" height="{H}" rx="4" fill="none" stroke="{BORDER}"/>
    <text x="{lx}" y="15" fill="{MUTED}" text-anchor="middle" textLength="{ltl:.0f}" lengthAdjust="spacingAndGlyphs">{label}</text>
    <text x="{vx}" y="15" fill="{TEXT}" text-anchor="middle" textLength="{vtl:.0f}" lengthAdjust="spacingAndGlyphs">{value}</text>
  </g>
</svg>
'''


BADGES = {
    "license": ("license", "MIT"),
    "python": ("python", "3.11+"),
    "owasp": ("owasp", "LLM Top 10"),
    "offline": ("tests", "offline"),
}


def main(out_dir: str) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for name, (label, value) in BADGES.items():
        (out / f"{name}.svg").write_text(badge(label, value), encoding="utf-8")
    print(f"wrote {len(BADGES)} badges to {out}")


if __name__ == "__main__":
    main(sys.argv[1])
