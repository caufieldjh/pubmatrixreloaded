"""Color schemes for the matrix, the same ones the web page offers.

A scheme is three colors, for the fewest documents, the middle, and the most.
The steps between them are blended in OKLab so they look evenly spaced, and
each step gets black or white text, whichever has more contrast on it.
"""

from __future__ import annotations

import argparse
import re

from .matrix import HEAT_STEPS

PRESETS: dict[str, tuple[str, str, str]] = {
    "blue": ("#cde2fb", "#4a89d6", "#0d366b"),
    "red": ("#fde0dc", "#dc4b35", "#6e0d0d"),
    "green": ("#dcf2dc", "#3f9b4f", "#0d4a1c"),
    "black": ("#eeeeee", "#888888", "#111111"),
    "white": ("#222222", "#888888", "#ffffff"),
}

_HEX = re.compile(r"#?([0-9a-f]{3}|[0-9a-f]{6})", re.I)


def parse_hex(text: str) -> str | None:
    """'#abc', 'abc', '#aabbcc' or 'aabbcc' as '#aabbcc', or None if it is not a hex color."""
    match = _HEX.fullmatch(text.strip())
    if not match:
        return None
    digits = match.group(1)
    if len(digits) == 3:
        digits = "".join(d * 2 for d in digits)
    return f"#{digits.lower()}"


def parse_scheme(text: str) -> tuple[str, str, str]:
    """Read a preset name, or three hex colors separated by commas, for argparse."""
    name = text.strip().lower()
    if name in PRESETS:
        return PRESETS[name]
    parts = [parse_hex(part) for part in text.split(",")]
    if len(parts) != 3 or None in parts:
        raise argparse.ArgumentTypeError(
            f"{text!r} is not a color scheme. Use one of {', '.join(PRESETS)}, "
            "or three hex colors for fewest, middle, and most, such as fde0dc,dc4b35,6e0d0d"
        )
    low, middle, high = (str(part) for part in parts)
    return low, middle, high


def _channels(hex_color: str) -> list[float]:
    return [int(hex_color[at : at + 2], 16) / 255 for at in (1, 3, 5)]


def _to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _from_linear(c: float) -> float:
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def _cbrt(x: float) -> float:
    return x ** (1 / 3) if x >= 0 else -((-x) ** (1 / 3))


def _to_oklab(hex_color: str) -> list[float]:
    r, g, b = (_to_linear(c) for c in _channels(hex_color))
    l_ = _cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b)
    m_ = _cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b)
    s_ = _cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b)
    return [
        0.2104542553 * l_ + 0.793617785 * m_ - 0.0040720468 * s_,
        1.9779984951 * l_ - 2.428592205 * m_ + 0.4505937099 * s_,
        0.0259040371 * l_ + 0.7827717662 * m_ - 0.808675766 * s_,
    ]


def _from_oklab(lab: list[float]) -> str:
    big_l, a, b = lab
    l_ = (big_l + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m_ = (big_l - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s_ = (big_l - 0.0894841775 * a - 1.291485548 * b) ** 3
    rgb = (
        4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_,
        -1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_,
        -0.0041960863 * l_ - 0.7034186147 * m_ + 1.707614701 * s_,
    )
    # Half up, as Math.round does on the page; Python's round() goes to even.
    channels = (int(255 * min(1.0, max(0.0, _from_linear(c))) + 0.5) for c in rgb)
    return "#" + "".join(f"{c:02x}" for c in channels)


def luminance(hex_color: str) -> float:
    """Relative luminance, as WCAG defines it."""
    r, g, b = (_to_linear(c) for c in _channels(hex_color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def ink_for(hex_color: str) -> str:
    """Black or white, whichever reads better on this background."""
    light = luminance(hex_color)
    return "#ffffff" if 1.05 / (light + 0.05) > (light + 0.05) / 0.05 else "#000000"


def heat_scale(scheme: tuple[str, str, str]) -> list[tuple[str, str]]:
    """HEAT_STEPS (background, ink) pairs from low through middle to high."""
    ends = [_to_oklab(color) for color in scheme]
    steps = []
    for n in range(HEAT_STEPS):
        t = n / (HEAT_STEPS - 1)
        start, end, f = (ends[0], ends[1], t * 2) if t <= 0.5 else (ends[1], ends[2], t * 2 - 1)
        background = _from_oklab([v + (w - v) * f for v, w in zip(start, end)])
        steps.append((background, ink_for(background)))
    return steps
