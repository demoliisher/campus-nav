# -*- coding: utf-8 -*-
"""Polyline helpers: midpoint, interpolation, stitching, and length clipping."""

from __future__ import annotations

import math


def polyline_midpoint(points: list[tuple[float, float]]) -> tuple[float, float]:
    """Return the length-weighted midpoint of a polyline."""
    if not points:
        return (0.0, 0.0)
    if len(points) == 1:
        return points[0]
    seg_len = [math.dist(a, b) for a, b in zip(points, points[1:])]
    half = sum(seg_len) / 2.0
    acc = 0.0
    for (a, b), length in zip(zip(points, points[1:]), seg_len):
        if length > 0 and acc + length >= half:
            f = (half - acc) / length
            return (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)
        acc += length
    return points[-1]


def lerp(p: tuple[float, float], q: tuple[float, float], t: float) -> tuple[float, float]:
    return (p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t)


def append_path(dst: list[tuple[float, float]], src: list[tuple[float, float]]) -> None:
    """Append src to dst, dropping a duplicated joint point."""
    if not src:
        return
    if dst and math.dist(dst[-1], src[0]) < 1e-6:
        dst.extend(src[1:])
    else:
        dst.extend(src)


def truncate_path(points: list[tuple[float, float]],
                   max_dist: float) -> list[tuple[float, float]]:
    """Return the prefix of a polyline up to max_dist."""
    if not points:
        return []
    if max_dist <= 1e-9:
        return [points[0]]
    out = [points[0]]
    acc = 0.0
    for a, b in zip(points, points[1:]):
        seg = math.dist(a, b)
        if seg <= 1e-12:
            continue
        if acc + seg <= max_dist + 1e-9:
            out.append(b)
            acc += seg
        else:
            f = (max_dist - acc) / seg
            out.append(lerp(a, b, f))
            return out
    return out
