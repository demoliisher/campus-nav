# -*- coding: utf-8 -*-
"""Pace constants, parsing, and formatting."""

from __future__ import annotations

import re

PACE_MIN_SEC = 5            # 0'05'' (quick preview)
PACE_MAX_SEC = 600          # 10'00''
PACE_DEFAULT_SEC = 300      # 5'00''


def fmt_duration(seconds: float) -> str:
    """Seconds -> x'yy'' (used for pace and elapsed time)."""
    total = max(0, int(round(seconds)))
    minutes, sec = divmod(total, 60)
    return f"{minutes}'{sec:02d}''"


def parse_pace(text: str) -> int | None:
    """Parse a pace into seconds; return None if out of range or invalid.

    Accepts forms such as 5'00'', 5'00", 5:00, 5', and 5.
    """
    s = text.strip().lower()
    for unit in ("per km", "perkm", "/km", "km"):
        s = s.replace(unit, "")
    s = (s.replace("\u2032", "'").replace("\u2019", "'")
          .replace("\u2033", '"').replace("\u201d", '"').strip())
    if not s:
        return None
    m = re.fullmatch(r"(\d{1,2})(?:\s*[':]\s*(\d{0,2})\s*(?:''|\")?)?", s)
    if not m:
        return None
    seconds = int(m.group(2)) if m.group(2) else 0
    if seconds >= 60:
        return None
    total = int(m.group(1)) * 60 + seconds
    if total < PACE_MIN_SEC or total > PACE_MAX_SEC:
        return None
    return total
