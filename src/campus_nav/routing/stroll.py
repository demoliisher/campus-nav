# -*- coding: utf-8 -*-
"""Stroll path generation for scoring-run playback.

The planned route is treated as a road centerline. The generated path stays on
the right side of that centerline within a 10 m road width, wanders with a
bounded heading noise, and is precomputed so playback never has to stop and
search for the next target.
"""

from __future__ import annotations

import bisect
import math
import random
from dataclasses import dataclass

ROAD_WIDTH_M = 10.0
HALF_ROAD_WIDTH_M = ROAD_WIDTH_M / 2.0
RIGHT_PREFERRED_M = 2.5
EDGE_MARGIN_M = 0.25
CORNER_ANGLE_RAD = math.radians(25.0)
CORNER_ZONE_M = 8.0
ANCHOR_ZONE_M = 4.0


@dataclass(frozen=True)
class StrollPath:
    """A precomputed walk path and the parameters that produced it."""

    points: tuple[tuple[float, float], ...]
    total_m: float
    step_m: float
    period_sec: float
    max_deg: float


def _dedupe(points) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for p in points:
        q = (float(p[0]), float(p[1]))
        if not out or math.dist(out[-1], q) > 1e-9:
            out.append(q)
    return out


class _Centerline:
    """Station lookup on the route polyline."""

    def __init__(self, points: list[tuple[float, float]]):
        self.pts = points
        self.seg_len: list[float] = []
        self.cum = [0.0]
        for a, b in zip(points, points[1:]):
            length = math.dist(a, b)
            self.seg_len.append(length)
            self.cum.append(self.cum[-1] + length)
        self.total = self.cum[-1]

    def at(self, s: float) -> tuple[tuple[float, float], tuple[float, float]]:
        """Return (point, unit tangent) at station s."""
        s = max(0.0, min(self.total, s))
        i = bisect.bisect_right(self.cum, s) - 1
        i = max(0, min(i, len(self.seg_len) - 1))
        length = self.seg_len[i]
        t = 0.0 if length <= 1e-12 else (s - self.cum[i]) / length
        a, b = self.pts[i], self.pts[i + 1]
        x = a[0] + (b[0] - a[0]) * t
        y = a[1] + (b[1] - a[1]) * t
        if length > 1e-12:
            tangent = ((b[0] - a[0]) / length, (b[1] - a[1]) / length)
        else:
            tangent = (1.0, 0.0)
        return (x, y), tangent

    def nearest_s(self, p: tuple[float, float]) -> float:
        """Project a point onto the polyline and return its station."""
        best_s = 0.0
        best_d = math.inf
        px, py = p
        for i, (a, b) in enumerate(zip(self.pts, self.pts[1:])):
            dx = b[0] - a[0]
            dy = b[1] - a[1]
            ll = dx * dx + dy * dy
            if ll <= 1e-18:
                continue
            t = ((px - a[0]) * dx + (py - a[1]) * dy) / ll
            t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
            fx = a[0] + t * dx
            fy = a[1] + t * dy
            d = math.hypot(px - fx, py - fy)
            if d < best_d:
                best_d = d
                best_s = self.cum[i] + t * self.seg_len[i]
        return best_s


def _corner_distances(center: _Centerline) -> list[tuple[float, float]]:
    """Significant turns as (station, fade zone), excluding gentle curves."""
    out: list[tuple[float, float]] = []
    for i in range(1, len(center.pts) - 1):
        a, b, c = center.pts[i - 1], center.pts[i], center.pts[i + 1]
        l1, l2 = center.seg_len[i - 1], center.seg_len[i]
        if l1 <= 1e-9 or l2 <= 1e-9:
            continue
        v1 = ((b[0] - a[0]) / l1, (b[1] - a[1]) / l1)
        v2 = ((c[0] - b[0]) / l2, (c[1] - b[1]) / l2)
        cross = v1[0] * v2[1] - v1[1] * v2[0]
        dot = v1[0] * v2[0] + v1[1] * v2[1]
        if abs(math.atan2(cross, dot)) >= CORNER_ANGLE_RAD:
            zone = min(CORNER_ZONE_M, 0.45 * min(l1, l2))
            out.append((center.cum[i], max(zone, 1.0)))
    return out


def _anchor_distances(center: _Centerline, anchors) -> list[float]:
    out = [0.0, center.total]
    for p in anchors:
        s = center.nearest_s((float(p[0]), float(p[1])))
        if 0.25 < s < center.total - 0.25:
            out.append(s)
    return sorted(out)


def _fade(s: float, corners, anchors) -> float:
    """1 on a straight stretch, 0 at a corner/checkpoint/route end."""
    factor = 1.0
    for d, zone in corners:
        dist = abs(s - d)
        if dist < zone:
            factor = min(factor, dist / zone)
    for d in anchors:
        dist = abs(s - d)
        if dist < ANCHOR_ZONE_M:
            factor = min(factor, dist / ANCHOR_ZONE_M)
    return max(0.0, min(1.0, factor))


def _between(s0: float, s1: float, corners, anchors) -> list[float]:
    marks = [d for d, _zone in corners if s0 < d < s1]
    marks.extend(d for d in anchors if s0 < d < s1)
    return sorted(set(marks))


def _append(out: list[tuple[float, float]], p: tuple[float, float]) -> None:
    if not out or math.dist(out[-1], p) > 1e-9:
        out.append(p)


def _path_len(points: list[tuple[float, float]]) -> float:
    return sum(math.dist(a, b) for a, b in zip(points, points[1:]))


def build_stroll_path(route_points, *, pace_sec: float, period_sec: float,
                      max_deg: float, seed: int = 0, anchors=(),
                      road_width: float = ROAD_WIDTH_M) -> StrollPath:
    """Build the right-side stroll path for a route centerline.

    ``pace_sec`` is seconds per kilometer and ``period_sec`` is the interval
    between location updates. Their product is the distance advanced per
    update. ``anchors`` (checkpoints and the start) are pulled back to the
    centerline so the runner still reaches them. The whole path is generated
    up front; playback only interpolates it.
    """
    pts = _dedupe(route_points)
    period = max(0.0, float(period_sec))
    max_deg = max(0.0, float(max_deg))
    if len(pts) < 2:
        return StrollPath(tuple(pts), 0.0, 0.0, period, max_deg)

    center = _Centerline(pts)
    pace = max(1e-9, float(pace_sec))
    step_m = max(0.05, 1000.0 / pace * period)
    half = max(0.0, float(road_width) / 2.0)
    lat_min = min(EDGE_MARGIN_M, half * 0.5)
    lat_max = max(lat_min, half - EDGE_MARGIN_M)
    preferred = min(RIGHT_PREFERRED_M, lat_max)

    corners = _corner_distances(center)
    anchors_s = _anchor_distances(center, anchors)
    rng = random.Random(seed)
    max_rad = math.radians(max_deg)
    lat = rng.uniform(max(lat_min, 1.0), max(lat_min, min(3.8, lat_max)))

    out: list[tuple[float, float]] = [center.pts[0]]
    s = 0.0
    guard = 0
    while s < center.total - 1e-9 and guard < 2_000_000:
        guard += 1
        theta = rng.uniform(-max_rad, max_rad) if max_rad > 0.0 else 0.0
        advance = step_m * math.cos(theta)
        s2 = min(center.total, s + max(1e-9, advance))
        if s2 <= s + 1e-12:
            s2 = min(center.total, s + 1e-6)

        # Lateral displacement of this hop, with a weak pull back toward the
        # preferred right-side offset so the path does not stick to an edge.
        lat += step_m * math.sin(theta)
        lat += (preferred - lat) * 0.10
        lat = min(max(lat, lat_min), lat_max)

        # Keep geometric corner/checkpoint vertices inside large steps.
        for mark in _between(s, s2, corners, anchors_s):
            point, _tangent = center.at(mark)
            _append(out, point)

        point, tangent = center.at(s2)
        normal = (tangent[1], -tangent[0])
        fade = _fade(s2, corners, anchors_s)
        _append(out, (point[0] + normal[0] * lat * fade,
                      point[1] + normal[1] * lat * fade))
        s = s2

    _append(out, center.pts[-1])
    return StrollPath(tuple(out), _path_len(out), step_m, period, max_deg)
