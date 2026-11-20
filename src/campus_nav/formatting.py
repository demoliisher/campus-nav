# -*- coding: utf-8 -*-
"""Distance and longitude/latitude formatting."""

from __future__ import annotations


def fmt_len(m: float, km_at: float = 1000.0) -> str:
    if m < km_at:
        return f"{m:.1f} m" if m < 10.0 else f"{m:.0f} m"
    return f"{m / 1000.0:.2f} km"


def fmt_lonlat(pt: tuple[float, float]) -> str:
    return f"{pt[0]:.6f}, {pt[1]:.6f}"


def fmt_walked(m: float) -> str:
    """Distance walked during playback: show 0 m near zero, else like fmt_len."""
    if m < 0.05:
        return "0 m"
    return fmt_len(m)
