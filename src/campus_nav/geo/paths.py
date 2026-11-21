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


def sample_polyline(points: list[tuple[float, float]], interval_m: float,
                    max_samples: int = 600) -> list[tuple[tuple[float, float],
                                                              tuple[float, float]]]:
    """Sample positions and unit tangents every ``interval_m`` along a polyline.

    The first sample sits one interval from the start, so the direction arrow
    placement is regular rather than crowded at the first vertex. Tangents
    point in the direction of travel.
    """
    if interval_m <= 0.0 or len(points) < 2:
        return []
    out: list[tuple[tuple[float, float], tuple[float, float]]] = []
    acc = 0.0
    next_at = float(interval_m)
    for a, b in zip(points, points[1:]):
        seg = math.dist(a, b)
        if seg <= 1e-12:
            continue
        ux = (b[0] - a[0]) / seg
        uy = (b[1] - a[1]) / seg
        while next_at <= acc + seg + 1e-9 and len(out) < max_samples:
            t = (next_at - acc) / seg
            out.append((
                (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t),
                (ux, uy),
            ))
            next_at += interval_m
        acc += seg
    return out


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
