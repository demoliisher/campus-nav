# -*- coding: utf-8 -*-
"""Stroll path generation for scoring-run playback.

The planned route is treated as a road centerline. The generated path keeps to
the right of that centerline inside a 10 m road width, wanders with a bounded
heading noise, and is precomputed so playback never has to stop and search for
the next target.

Geometry notes:

* The centerline tangent is a central difference over a short window, so it is
  continuous through polyline vertices; the path rounds corners instead of
  stepping on the raw GeoJSON points.
* Every location update still advances by ``speed x interval`` meters, but the
  curve is sampled densely and then simplified, so corners are drawn as curves
  while straights keep few points.
* On the inside of a turn the lateral offset is capped, which cuts the corner
  and avoids the cusp an offset curve would otherwise create.
* Where the route doubles back (a checkpoint that requires a U-turn) the two
  lanes are joined by a half circle, so the turn-around is a loop rather than
  an acute angle.
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

REVERSAL_ANGLE_RAD = math.radians(150.0)   # treated as a U-turn
INSIDE_RADIUS_FRACTION = 0.5               # keep this share of the corner radius
MIN_INSIDE_M = 0.35
CAP_MIN_WINDOW_M = 4.0                     # widen the low-cap zone around a corner
CAP_SMOOTH_WINDOW_M = 3.0                  # and blend its shoulders
ANCHOR_ZONE_M = 4.0
REVERSAL_ZONE_M = 6.0
TANGENT_WINDOW_M = 0.8
CENTER_SMOOTH_M = 6.0                      # corner rounding radius of the centerline
SAMPLE_M = 0.5
SIMPLIFY_M = 0.08
ARC_STEP_M = 0.4


@dataclass(frozen=True)
class StrollPath:
    """A precomputed walk path and the parameters that produced it."""

    points: tuple[tuple[float, float], ...]
    total_m: float
    step_m: float
    period_sec: float
    max_deg: float
    reversals: int = 0


def _dedupe(points) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for p in points:
        q = (float(p[0]), float(p[1]))
        if not out or math.dist(out[-1], q) > 1e-9:
            out.append(q)
    return out


def _pin_points(points: list[tuple[float, float]], targets, half: int,
                max_pull: float = 3.0) -> list[tuple[float, float]]:
    """Bend a smoothed polyline so it passes through exact points.

    Checkpoints sit on the raw road, which smoothing may have cut a couple of
    meters inside the corner. A local smooth bump puts the curve back on the
    checkpoint without reintroducing a sharp corner.
    """
    if not targets or half <= 0 or len(points) < 3:
        return list(points)
    out = list(points)
    for tx, ty in targets:
        i = min(range(len(out)),
                key=lambda k: math.dist(out[k], (tx, ty)))
        dx, dy = tx - out[i][0], ty - out[i][1]
        pull = math.hypot(dx, dy)
        if pull <= 1e-9 or pull > max_pull:
            continue
        lo = max(0, i - half)
        hi = min(len(out), i + half + 1)
        for j in range(lo, hi):
            w = _smoothstep(1.0 - abs(j - i) / float(half))
            out[j] = (out[j][0] + dx * w, out[j][1] + dy * w)
    return out


def _smoothstep(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


def _resample(points: list[tuple[float, float]],
              ds: float) -> list[tuple[float, float]]:
    """Resample a polyline at a roughly constant spacing."""
    out: list[tuple[float, float]] = [points[0]]
    for a, b in zip(points, points[1:]):
        seg = math.dist(a, b)
        if seg <= 1e-12:
            continue
        steps = max(1, int(math.ceil(seg / ds)))
        for i in range(1, steps + 1):
            t = i / steps
            out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
    return _dedupe(out)


def _smooth(points: list[tuple[float, float]], half: int,
            passes: int = 2) -> list[tuple[float, float]]:
    """Triangular moving average that leaves the two ends alone.

    This is what turns the raw GeoJSON polyline into a drivable curve: corners
    become arcs of roughly ``half`` samples radius, so an offset lane no longer
    folds back on itself at every vertex. The ends stay untouched so a leg
    still meets a U-turn perpendicular to the road: the turn-around loop keeps
    its shape instead of being pulled inward.
    """
    if half <= 0 or len(points) < 3:
        return list(points)
    n = len(points)
    working = list(points)
    for _ in range(max(1, passes)):
        out: list[tuple[float, float]] = []
        for i in range(n):
            edge = min(i, n - 1 - i)
            if edge < half:
                # Keep the approach/departure straight; blend out smoothly.
                out.append(points[i] if edge == 0 else working[i])
                continue
            lo = i - half
            hi = i + half
            sx = sy = 0.0
            weight = 0
            for j in range(lo, hi + 1):
                w = half + 1 - abs(j - i)
                sx += working[j][0] * w
                sy += working[j][1] * w
                weight += w
            out.append((sx / weight, sy / weight))
        working = out
    return working


class _Centerline:
    """Station lookup on the (smoothed) route centerline."""

    def __init__(self, points: list[tuple[float, float]],
                 smooth_m: float = 0.0, sample_m: float = 0.5):
        pts = list(points)
        if smooth_m > 0.0:
            pts = _resample(pts, sample_m)
            pts = _smooth(pts, max(1, int(round(smooth_m / sample_m))))
        self.pts = pts
        self.seg_len: list[float] = []
        self.cum = [0.0]
        for a, b in zip(pts, pts[1:]):
            length = math.dist(a, b)
            self.seg_len.append(length)
            self.cum.append(self.cum[-1] + length)
        self.total = self.cum[-1]
        self.kappa = [0.0] * len(pts)
        for i in range(1, len(pts) - 1):
            a, b, c = pts[i - 1], pts[i], pts[i + 1]
            v1 = (b[0] - a[0], b[1] - a[1])
            v2 = (c[0] - b[0], c[1] - b[1])
            l1 = math.hypot(*v1)
            l2 = math.hypot(*v2)
            if l1 <= 1e-9 or l2 <= 1e-9:
                continue
            cross = v1[0] * v2[1] - v1[1] * v2[0]
            dot = v1[0] * v2[0] + v1[1] * v2[1]
            self.kappa[i] = math.atan2(cross, dot) / (0.5 * (l1 + l2))

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

    def tangent_at(self, s: float, window: float = TANGENT_WINDOW_M,
                   blocks=()) -> tuple[float, float]:
        """Tangent over a window, avoiding direction reversals inside it.

        A central difference would cancel out exactly on a U-turn (the route
        goes out and back), so a block splits the window and the one-sided
        difference on the current side is used instead.
        """
        lo = max(0.0, s - window)
        hi = min(self.total, s + window)
        for block in blocks:
            if lo < block < s:
                lo = max(lo, block)
            elif s <= block < hi:
                hi = min(hi, block)
        p1, _ = self.at(lo)
        p2, _ = self.at(hi)
        dx, dy = p2[0] - p1[0], p2[1] - p1[1]
        norm = math.hypot(dx, dy)
        if norm > 1e-9:
            return (dx / norm, dy / norm)
        return self.at(s)[1]

    def kappa_at(self, s: float) -> float:
        """Signed curvature (positive = left turn) at station s."""
        i = bisect.bisect_right(self.cum, s) - 1
        i = max(1, min(i, len(self.kappa) - 2))
        return self.kappa[i]

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


def _vertex_turns(center: _Centerline):
    """(vertex index, signed turn, incoming length, outgoing length)."""
    out: list[tuple[int, float, float, float]] = []
    for i in range(1, len(center.pts) - 1):
        a, b, c = center.pts[i - 1], center.pts[i], center.pts[i + 1]
        l1, l2 = center.seg_len[i - 1], center.seg_len[i]
        if l1 <= 1e-9 or l2 <= 1e-9:
            continue
        v1 = ((b[0] - a[0]) / l1, (b[1] - a[1]) / l1)
        v2 = ((c[0] - b[0]) / l2, (c[1] - b[1]) / l2)
        cross = v1[0] * v2[1] - v1[1] * v2[0]
        dot = v1[0] * v2[0] + v1[1] * v2[1]
        out.append((i, math.atan2(cross, dot), l1, l2))
    return out


def _anchor_stations(center: _Centerline, anchors, reversals) -> list[float]:
    """Checkpoint stations that must be touched, plus both route ends."""
    out = [0.0, center.total]
    for p in anchors:
        s = center.nearest_s((float(p[0]), float(p[1])))
        # A checkpoint that sits on a U-turn is passed around, not touched.
        if any(abs(s - r) < REVERSAL_ZONE_M for r in reversals):
            continue
        if 0.25 < s < center.total - 0.25:
            out.append(s)
    return sorted(set(out))


def _raw_cap(s: float, center: _Centerline, reversals,
             lat_max: float) -> float:
    """Instantaneous inside-lane cap for one station."""
    cap = math.inf
    kappa = center.kappa_at(s)
    if kappa < -1e-6:            # right turn: the right-hand lane is inside
        cap = max(MIN_INSIDE_M, INSIDE_RADIUS_FRACTION / -kappa)
    for station in reversals:
        d = abs(s - station)
        if d < REVERSAL_ZONE_M:
            cap = min(cap, RIGHT_PREFERRED_M
                      + (lat_max - RIGHT_PREFERRED_M)
                      * _smoothstep(d / REVERSAL_ZONE_M))
    return cap


def _smooth_caps(caps: list[float], ds: float) -> list[float]:
    """Widen and blend the cap profile.

    A hard min would yank the lane sideways right at the apex and fold the
    trail over itself, so the low-cap zone is widened first (moving minimum)
    and its shoulders are averaged afterwards. The apex keeps its own value,
    which is the one the no-fold condition needs.
    """
    n = len(caps)
    if n == 0:
        return caps
    w_min = max(1, int(round(CAP_MIN_WINDOW_M / ds)))
    w_avg = max(1, int(round(CAP_SMOOTH_WINDOW_M / ds)))
    widened = [min(caps[max(0, i - w_min):min(n, i + w_min + 1)])
               for i in range(n)]
    out = []
    for i in range(n):
        lo = max(0, i - w_avg)
        hi = min(n, i + w_avg + 1)
        # Averaging only the widened profile keeps the apex at its own value
        # while the shoulders ramp over 2*w_avg meters.
        out.append(sum(widened[lo:hi]) / (hi - lo))
    return out


def _anchor_factor(s: float, stations) -> float:
    factor = 1.0
    for station in stations:
        d = abs(s - station)
        if d < ANCHOR_ZONE_M:
            factor = min(factor, _smoothstep(d / ANCHOR_ZONE_M))
    return factor


def _offset_point(center: _Centerline, s: float, lat: float,
                  blocks) -> tuple[float, float]:
    point, _ = center.at(s)
    tangent = center.tangent_at(s, blocks=blocks)
    return (point[0] + tangent[1] * lat, point[1] - tangent[0] * lat)


def _turn_arc(center: _Centerline, station: float, lat: float,
              blocks) -> list[tuple[float, float]]:
    """Half circle joining the two lanes when the route reverses.

    The runner overshoots the checkpoint and comes back on the other lane, so
    the sweep goes through the point ahead of them rather than bouncing back.
    """
    cxy, _ = center.at(station)
    tangent = center.tangent_at(station - 1e-3, blocks=blocks)
    normal = (tangent[1], -tangent[0])
    radius = max(1e-6, abs(lat))
    start = (cxy[0] + normal[0] * lat, cxy[1] + normal[1] * lat)
    angle0 = math.atan2(start[1] - cxy[1], start[0] - cxy[0])
    ahead = math.atan2(tangent[1], tangent[0])
    # Of the two half circles, keep the one whose middle is ahead of the runner.
    sweep = math.pi if math.cos(angle0 + math.pi / 2.0 - ahead) >= 0.0 else -math.pi
    steps = max(6, int(math.ceil(abs(sweep) * radius / ARC_STEP_M)))
    out: list[tuple[float, float]] = []
    for i in range(1, steps + 1):
        angle = angle0 + sweep * i / steps
        out.append((cxy[0] + math.cos(angle) * radius,
                    cxy[1] + math.sin(angle) * radius))
    return out


def _append(out: list[tuple[float, float]], p: tuple[float, float]) -> None:
    if not out or math.dist(out[-1], p) > 1e-9:
        out.append(p)


def _path_len(points) -> float:
    return sum(math.dist(a, b) for a, b in zip(points, points[1:]))


def _rdp(points: list[tuple[float, float]], eps: float) -> list[tuple[float, float]]:
    """Ramer-Douglas-Peucker: keep the shape, drop redundant straights."""
    if len(points) < 3:
        return list(points)
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        ax, ay = points[i]
        bx, by = points[j]
        dx, dy = bx - ax, by - ay
        norm2 = dx * dx + dy * dy
        best = -1.0
        split = -1
        for k in range(i + 1, j):
            px, py = points[k][0] - ax, points[k][1] - ay
            if norm2 <= 1e-18:
                dist = math.hypot(px, py)
            else:
                t = max(0.0, min(1.0, (px * dx + py * dy) / norm2))
                dist = math.hypot(px - t * dx, py - t * dy)
            if dist > best:
                best, split = dist, k
        if best > eps:
            keep[split] = True
            stack.append((i, split))
            stack.append((split, j))
    return [p for p, kept in zip(points, keep) if kept]


def _simplify(points: list[tuple[float, float]], eps: float,
              pinned: list[int]) -> list[tuple[float, float]]:
    """Simplify in sections so checkpoint samples survive."""
    marks = sorted({0, len(points) - 1, *pinned})
    out: list[tuple[float, float]] = [points[0]]
    for a, b in zip(marks, marks[1:]):
        if b <= a:
            continue
        section = _rdp(points[a:b + 1], eps)
        out.extend(section[1:])
    return out


def build_stroll_path(route_points, *, pace_sec: float, period_sec: float,
                      max_deg: float, seed: int = 0, anchors=(),
                      road_width: float = ROAD_WIDTH_M) -> StrollPath:
    """Build the right-side stroll path for a route centerline.

    ``pace_sec`` is seconds per kilometer and ``period_sec`` is the interval
    between location updates. Their product is the distance advanced per
    update. ``anchors`` (checkpoints and the start) are pulled to the
    centerline so the runner still reaches them. The whole path is generated
    up front; playback only interpolates it.
    """
    pts = _dedupe(route_points)
    period = max(0.0, float(period_sec))
    max_deg = max(0.0, float(max_deg))
    if len(pts) < 2:
        return StrollPath(tuple(pts), 0.0, 0.0, period, max_deg)

    raw = _Centerline(pts)
    pace = max(1e-9, float(pace_sec))
    step_m = max(0.05, 1000.0 / pace * period)
    half = max(0.0, float(road_width) / 2.0)
    lat_min = min(EDGE_MARGIN_M, half * 0.5)
    lat_max = max(lat_min, half - EDGE_MARGIN_M)
    preferred = min(RIGHT_PREFERRED_M, lat_max)

    # Smooth each leg between U-turns separately: averaging across a fold
    # would blend the outbound and return lanes and leave the tangent there
    # undefined. The legs stay pinned to the shared vertex, so the arc that
    # joins them is built against a well-defined approach direction.
    turns = _vertex_turns(raw)
    breaks = [i for i, turn, _l1, _l2 in turns
              if abs(turn) >= REVERSAL_ANGLE_RAD]
    bounds = [0, *breaks, len(pts) - 1]
    legs = [_Centerline(pts[a:b + 1], smooth_m=CENTER_SMOOTH_M,
                        sample_m=SAMPLE_M).pts
            for a, b in zip(bounds, bounds[1:])]
    joined: list[tuple[float, float]] = []
    for i, leg in enumerate(legs):
        joined.extend(leg if i == 0 else leg[1:])
    joined = _pin_points(joined, [(float(p[0]), float(p[1])) for p in anchors],
                         max(2, int(round(ANCHOR_ZONE_M / SAMPLE_M))))
    center = _Centerline(joined)
    reversals: list[float] = []
    acc = 0.0
    for leg in legs[:-1]:
        acc += _path_len(leg)
        reversals.append(acc)
    anchors_s = _anchor_stations(center, anchors, reversals)

    # Precompute the inside-lane cap along the route and smooth it, so the
    # lane narrows gradually before a corner instead of snapping at the apex.
    sample_count = int(center.total / SAMPLE_M) + 1
    caps = _smooth_caps(
        # Clamp to lat_max first: an unbounded cap would poison the average.
        [min(_raw_cap(i * SAMPLE_M, center, reversals, lat_max), lat_max)
         for i in range(sample_count)],
        SAMPLE_M,
    )

    def cap_at(s: float) -> float:
        i = int(round(max(0.0, min(center.total, s)) / SAMPLE_M))
        return caps[min(i, len(caps) - 1)]

    rng = random.Random(seed)
    max_rad = math.radians(max_deg)
    lat = rng.uniform(max(lat_min, 1.0), max(lat_min, min(3.8, lat_max)))

    # Heading noise is drawn once per location interval and interpolated, so
    # the wander is continuous instead of stepping at every update.
    knots = int(center.total / step_m) + 2
    thetas = [rng.uniform(-max_rad, max_rad) if max_rad > 0.0 else 0.0
              for _ in range(knots)]

    def theta_at(s: float) -> float:
        k = s / step_m
        i = min(int(k), knots - 2)
        frac = k - i
        weight = 0.5 - 0.5 * math.cos(math.pi * frac)
        return thetas[i] + (thetas[i + 1] - thetas[i]) * weight

    ds = max(0.2, min(SAMPLE_M, step_m / 4.0))
    pull_per_m = 0.10 / step_m

    dense: list[tuple[float, float]] = [center.pts[0]]
    pinned: list[int] = [0]
    rev_index = 0
    anchor_index = 0
    s = 0.0
    guard = 0
    limit = int(center.total / ds) + 2
    while s < center.total - 1e-9 and guard < limit * 2 + 16:
        guard += 1
        s2 = min(center.total, s + ds)

        # U-turns are handled as their own event so the two lanes join in an arc.
        if rev_index < len(reversals) and s < reversals[rev_index] <= s2 + 1e-9:
            station = reversals[rev_index]
            rev_index += 1
            lat = min(max(lat, lat_min), lat_max, cap_at(station))
            _append(dense, _offset_point(center, station - 1e-3, lat, reversals))
            for p in _turn_arc(center, station, lat, reversals):
                _append(dense, p)
            s = station
            continue

        theta = theta_at(s2)
        lat += ds * math.sin(theta) + ds * pull_per_m * (preferred - lat)
        lat = min(max(lat, lat_min), lat_max)

        # Touch checkpoints exactly; the samples around them fade the offset.
        while (anchor_index < len(anchors_s)
               and anchors_s[anchor_index] <= s2):
            station = anchors_s[anchor_index]
            anchor_index += 1
            if station <= s + 1e-9:
                continue
            _append(dense, center.at(station)[0])
            pinned.append(len(dense) - 1)

        lat_eff = min(lat, cap_at(s2)) * _anchor_factor(s2, anchors_s)
        _append(dense, _offset_point(center, s2, lat_eff, reversals))
        s = s2

    _append(dense, center.pts[-1])
    pinned.append(len(dense) - 1)
    pinned = sorted(set(pinned))

    simplified = _simplify(dense, SIMPLIFY_M, pinned)
    return StrollPath(tuple(simplified), _path_len(simplified), step_m,
                      period, max_deg, len(reversals))
