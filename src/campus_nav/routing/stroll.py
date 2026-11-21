# -*- coding: utf-8 -*-
"""Stroll path generation for scoring-run playback.

The planned route is treated as a road centerline. The generated path keeps to
the right of that centerline, wanders with a bounded heading noise, and is
precomputed so playback never has to stop and search for the next target. The
running track is the exception: it is not a right-hand road but eight standard
lanes, so each passage of the northernmost entrance picks one of the eight
lanes (1.22 m apart) and the wander stays inside that lane.

Geometry notes:

* Every corner is replaced by a circular arc that is tangent to the lane before
  and after it. The arc centre is the vertex of the road-width square on the
  inside of the turn, so the runner walks *around* the corner instead of
  cutting it. With half width ``a`` and lane offset ``d`` the radius is
  ``a - d`` for a right turn and ``a + d`` for a left turn: a right turn keeps
  the runner on the inside lane, a left turn carries them on the outside lane,
  which swings wide around the junction.
* A corner is any vertex of any LineString, so bends inside one road feature
  are treated exactly like junctions between two features. Smooth curves are
  not corners: their turn is spread over many vertices, and the lane offset
  follows them without one arc per vertex.
* Where the route doubles back (a checkpoint that requires a U-turn) the two
  lanes are joined by a half circle, so the turn-around is a loop rather than
  an acute angle.
* Checkpoints are touched exactly; a checkpoint next to a corner is reached by
  a local smooth bend of the finished path.
* Every location update still advances by ``speed x interval`` meters, but the
  curve is sampled densely and then simplified, so corners are drawn as curves
  while straights keep few points.
"""

from __future__ import annotations

import bisect
import math
import random
from dataclasses import dataclass

from ..geo.polygons import point_in_polygon

ROAD_WIDTH_M = 10.0
HALF_ROAD_WIDTH_M = ROAD_WIDTH_M / 2.0
RIGHT_PREFERRED_M = 2.5
EDGE_MARGIN_M = 0.25

REVERSAL_ANGLE_RAD = math.radians(150.0)   # treated as a U-turn
CORNER_MIN_ANGLE_RAD = math.radians(25.0)  # smaller bends are map jitter
SHARP_CORNER_RAD = math.radians(40.0)      # always rounded
CORNER_PEAK_RATIO = 1.6                    # a corner stands out from its bends
MIN_TURN_RADIUS_M = 0.75                   # arc radius floor at sharp corners
ANCHOR_ZONE_M = 4.0
REVERSAL_ZONE_M = 6.0
TANGENT_WINDOW_M = 0.8
SAMPLE_M = 0.5
SIMPLIFY_M = 0.08
CENTER_SIMPLIFY_M = 0.4                    # drop map polyline jitter first
ARC_STEP_M = 0.4
NOISE_MAX_M = 1.6
TRACK_TOL_M = 1.2                          # centerline this close is on the track
TRACK_DECISION_TOL_M = 2.5                 # passage of the northern entrance
TRACK_LANE_COUNT = 8                       # standard 400 m track: eight lanes
TRACK_LANE_WIDTH_M = 1.22                  # standard lane width (9.76 m band)
TRACK_LANE_RAMP_M = 15.0                   # lane change after a track decision
TRACK_LANE_NOISE = 0.25                    # keep the wander inside one lane


@dataclass(frozen=True)
class StrollPath:
    """A precomputed walk path and the parameters that produced it."""

    points: tuple[tuple[float, float], ...]
    total_m: float
    step_m: float
    period_sec: float
    max_deg: float
    reversals: int = 0


@dataclass(frozen=True)
class _Corner:
    """One rounded corner of the route."""

    s_in: float                                   # station of the arc start
    s_out: float                                  # station of the arc end
    lane: float                                   # lane offset of the arc
    points: tuple[tuple[float, float], ...]       # P ... Q


def _dedupe(points) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for p in points:
        q = (float(p[0]), float(p[1]))
        if not out or math.dist(out[-1], q) > 1e-9:
            out.append(q)
    return out


def _smoothstep(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


class _Centerline:
    """Station lookup on the route centerline."""

    def __init__(self, points: list[tuple[float, float]]):
        self.pts = list(points)
        self.seg_len: list[float] = []
        self.cum = [0.0]
        for a, b in zip(self.pts, self.pts[1:]):
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


def _pin_targets(center: _Centerline, anchors, reversals):
    """Checkpoint positions that the finished path should pass through."""
    out = []
    for p in anchors:
        target = (float(p[0]), float(p[1]))
        station = center.nearest_s(target)
        if station <= 0.25 or station >= center.total - 0.25:
            continue
        if any(abs(station - r) < REVERSAL_ZONE_M for r in reversals):
            continue
        out.append((station, target))
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


def _corner_arc(center: _Centerline, index: int, turn: float,
                a_turn: float, lane_mag: float) -> tuple[float, float, tuple]:
    """Arc tangent to the right lane before and after a corner.

    ``a_turn`` is the half width used at this corner (it shrinks when the legs
    are short). The centre is the road-width square's vertex on the inside of
    the turn; the radius grows by ``lane_mag`` on a left turn because the
    runner is on the outside lane there. Returns (s_in, s_out, points).
    """
    jx, jy = center.pts[index]
    prev_p = center.pts[index - 1]
    next_p = center.pts[index + 1]
    l1 = center.seg_len[index - 1]
    l2 = center.seg_len[index]
    u = ((jx - prev_p[0]) / l1, (jy - prev_p[1]) / l1)
    v = ((next_p[0] - jx) / l2, (next_p[1] - jy) / l2)
    # Inside normals of both roads: left for a left turn, right for a right one.
    side = 1.0 if turn > 0.0 else -1.0
    n_in = (-u[1] * side, u[0] * side)
    n_out = (-v[1] * side, v[0] * side)
    cos_turn = math.cos(abs(turn))
    # The runner is on the inside lane of a right turn (radius a - d) and on
    # the outside lane of a left turn, which swings wide (radius a + d).
    radius = max(1e-6, a_turn * (1.0 + cos_turn) + side * lane_mag)
    cx = jx + a_turn * (n_in[0] + n_out[0])
    cy = jy + a_turn * (n_in[1] + n_out[1])
    p = (cx - radius * n_in[0], cy - radius * n_in[1])
    q = (cx - radius * n_out[0], cy - radius * n_out[1])
    angle0 = math.atan2(p[1] - cy, p[0] - cx)
    steps = max(4,
                int(math.ceil(abs(turn) * radius / ARC_STEP_M)),
                int(math.ceil(abs(turn) / math.radians(8.0))))
    points = [p]
    for i in range(1, steps + 1):
        angle = angle0 + turn * i / steps
        points.append((cx + math.cos(angle) * radius,
                       cy + math.sin(angle) * radius))
    points[-1] = q
    s_in = center.cum[index] - a_turn * math.sin(abs(turn))
    s_out = center.cum[index] + a_turn * math.sin(abs(turn))
    return s_in, s_out, tuple(points)


def _build_corners(center: _Centerline, turns, reversals,
                   half_width: float, preferred: float) -> list[_Corner]:
    """Round every corner that is not a U-turn.

    A corner that is too sharp for its square is left to the plain lane path.
    Checkpoints are handled afterwards by a local bend of the finished path.
    """
    specs = sorted((center.cum[i], i, turn, l1, l2)
                   for i, turn, l1, l2 in turns
                   if CORNER_MIN_ANGLE_RAD <= abs(turn) < REVERSAL_ANGLE_RAD)
    if not specs:
        return []
    bend = {i: abs(turn) for i, turn, _l1, _l2 in turns}
    # A smooth curve spreads its turn over many vertices; a corner concentrates
    # it in one. Sharp corners always get an arc (the lane would otherwise cusp
    # at the vertex); gentle bends only when they stand out from their
    # neighbours, so a ring road is not scalloped by one arc per vertex.
    specs = [spec for spec in specs
             if abs(spec[2]) >= SHARP_CORNER_RAD
             or abs(spec[2]) >= CORNER_PEAK_RATIO
             * max(bend.get(spec[1] - 1, 0.0), bend.get(spec[1] + 1, 0.0))]
    if not specs:
        return []
    # Walk corner vertices and U-turn stations together, in route order.
    markers = sorted([(s, 0, turn, i, l1, l2) for s, i, turn, l1, l2 in specs]
                     + [(r, 1, 0.0, -1, 0.0, 0.0) for r in reversals])
    stations = [m[0] for m in markers]
    corners: list[_Corner] = []
    for k, (s_j, kind, turn, index, l1, l2) in enumerate(markers):
        if kind == 1:
            continue
        gap_prev = s_j - (stations[k - 1] if k else 0.0)
        gap_next = (stations[k + 1] if k + 1 < len(stations) else center.total) - s_j
        sin_turn = max(1e-6, math.sin(abs(turn)))
        # The arc's tangent points must stay on the segments that meet at the
        # corner, so the adjacent segments bound the square as well.
        a_turn = min(half_width, 0.5 * gap_prev / sin_turn,
                     0.5 * gap_next / sin_turn, l1 / sin_turn, l2 / sin_turn)
        cos_turn = math.cos(abs(turn))
        base = a_turn * (1.0 + cos_turn)
        if turn < 0.0:
            # Right turn: the runner is on the inside lane, so the radius
            # shrinks by the lane offset; keep it above the floor.
            if base < MIN_TURN_RADIUS_M + 1e-9:
                continue
            lane_mag = max(0.0, min(preferred, base - MIN_TURN_RADIUS_M))
        else:
            # Left turn: the runner is on the outside lane, radius a + d.
            lane_mag = preferred
        s_in2, s_out2, points = _corner_arc(center, index, turn, a_turn,
                                            lane_mag)
        corners.append(_Corner(s_in2, s_out2, lane_mag, points))
    return corners


def _noise_scale(s: float, corners) -> float:
    """Fade the heading wander out at the exact arc endpoints."""
    scale = 1.0
    for c in corners:
        if c.s_in <= s <= c.s_out:
            return 0.0
        d = min(abs(s - c.s_in), abs(s - c.s_out))
        if d < ANCHOR_ZONE_M:
            scale = min(scale, _smoothstep(d / ANCHOR_ZONE_M))
    return scale


def _lane_target(s: float, corners, preferred: float) -> float:
    """Lane offset at a station; a tight right-turn arc narrows it slightly."""
    target = preferred
    for c in corners:
        if c.lane >= preferred - 1e-9:
            continue
        if c.s_in <= s <= c.s_out:
            return c.lane
        d = min(abs(s - c.s_in), abs(s - c.s_out))
        if d < ANCHOR_ZONE_M:
            w = _smoothstep(d / ANCHOR_ZONE_M)
            target = min(target, c.lane + (preferred - c.lane) * w)
    return target


def _segment_distance(p, a, b) -> float:
    dx = b[0] - a[0]
    dy = b[1] - a[1]
    ll = dx * dx + dy * dy
    if ll <= 1e-18:
        return math.hypot(p[0] - a[0], p[1] - a[1])
    t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / ll
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    return math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy))


def _ring_distance(p, ring) -> float:
    n = len(ring)
    return min(_segment_distance(p, ring[i], ring[(i + 1) % n])
               for i in range(n))


def _compact_controls(controls) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for s, value in controls:
        if out and abs(s - out[-1][0]) < 1e-9:
            out[-1] = (s, value)
        else:
            out.append((s, value))
    return out


def _track_inner_sign(center: _Centerline, ring, station: float) -> float:
    """+1 when the runner's right-hand side points into the track infield."""
    _, tangent = center.at(min(center.total, station + 0.5))
    p, _ = center.at(station)
    probe = (p[0] + tangent[1] * 1.5, p[1] - tangent[0] * 1.5)
    return 1.0 if point_in_polygon(probe[0], probe[1], ring) else -1.0


def _track_lane_offset(index: int, inner_sign: float, lat_max: float) -> float:
    """Signed lane offset for a standard track lane, counted from the infield.

    Lane 1 is the innermost: a standard 400 m track has eight 1.22 m lanes, so
    their centres lie 0.61, 1.83, ..., 9.15 m out from the inner edge. The
    band is 9.76 m wide and fits inside the 10 m track; dividing the width
    evenly keeps every lane 1.22 m from its neighbours and each lap about
    7 m longer than the previous one. The result is clamped to the walkable
    margin in case a caller uses a narrower road width.
    """
    half_band = TRACK_LANE_COUNT * TRACK_LANE_WIDTH_M / 2.0
    centre = (index - 0.5) * TRACK_LANE_WIDTH_M
    value = inner_sign * (half_band - centre)
    return max(-lat_max, min(lat_max, value))


def _track_lane_zones(center: _Centerline, ring, decision, preferred: float,
                      lat_max: float, corners, seed: int):
    """Signed lane targets for the running track.

    The track is not a right-hand road: it is a standard eight-lane track. The
    runner enters in the ordinary right-side lane, then each time they pass
    the northernmost entrance they pick one of the eight lanes and the swap
    ramps over a few meters. Returns ``[(s_start, s_end, controls)]`` and
    leaves the lane to the normal rules outside these zones.
    """
    if not ring or decision is None or len(ring) < 3:
        return []
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    x0, x1 = min(xs) - TRACK_TOL_M, max(xs) + TRACK_TOL_M
    y0, y1 = min(ys) - TRACK_TOL_M, max(ys) + TRACK_TOL_M
    step = 2.0
    count = max(1, int(math.ceil(center.total / step)))
    on_track = []
    for i in range(count + 1):
        s = min(center.total, i * step)
        p, _ = center.at(s)
        on_track.append(
            x0 <= p[0] <= x1 and y0 <= p[1] <= y1
            and _ring_distance(p, ring) <= TRACK_TOL_M
        )
    runs: list[tuple[float, float]] = []
    i = 0
    while i <= count:
        if not on_track[i]:
            i += 1
            continue
        j = i
        while j + 1 <= count and on_track[j + 1]:
            j += 1
        runs.append((i * step, min(center.total, j * step)))
        i = j + 1
    if not runs:
        return []
    rng = random.Random(seed ^ 0x5EED)
    zones = []
    for start, end in runs:
        # A corner arc at the track entrance owns its stretch of the path.
        for c in corners:
            if c.s_in - 1e-9 <= start <= c.s_out + 1e-9:
                start = min(end, c.s_out + 1.0)
        if end - start < 2.0:
            continue
        # One decision per passage of the northern entrance.
        decisions: list[float] = []
        last_hit = -1e9
        s = start
        while s <= end + 1e-9:
            if math.dist(center.at(s)[0], decision) <= TRACK_DECISION_TOL_M:
                if (s - last_hit > 2.0 * TRACK_DECISION_TOL_M
                        and start + 3.0 < s < end - 2.0):
                    decisions.append(s)
                last_hit = s
            s += step
        # Enter in the normal lane, pick a track lane straight away, then
        # re-pick once per passage of the northern entrance.
        inner_sign = _track_inner_sign(center, ring, start)
        entry = _track_lane_offset(rng.randint(1, TRACK_LANE_COUNT),
                                   inner_sign, lat_max)
        controls: list[tuple[float, float]] = [
            (start, _lane_target(start, corners, preferred)),
            (min(end, start + TRACK_LANE_RAMP_M), entry),
        ]
        prev = entry
        for station in decisions:
            inner_sign = _track_inner_sign(center, ring, station)
            value = _track_lane_offset(rng.randint(1, TRACK_LANE_COUNT),
                                       inner_sign, lat_max)
            controls.append((station, prev))
            controls.append((min(end, station + TRACK_LANE_RAMP_M), value))
            prev = value
        controls.append((end, prev))
        controls.append(
            (min(center.total, end + TRACK_LANE_RAMP_M), preferred)
        )
        controls = _compact_controls(controls)
        zones.append((controls[0][0], controls[-1][0],
                      [c[0] for c in controls], controls))
    return zones


def _track_lane_value(s: float, zones):
    """Signed lane target at s, or None when the path is not on the track."""
    for z0, z1, stations, controls in zones:
        if z0 - 1e-9 <= s <= z1 + 1e-9:
            i = bisect.bisect_right(stations, s) - 1
            i = max(0, min(i, len(controls) - 2))
            a, va = controls[i]
            b, vb = controls[i + 1]
            if b - a <= 1e-9:
                return vb
            return va + (vb - va) * _smoothstep((s - a) / (b - a))
    return None


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
                      road_width: float = ROAD_WIDTH_M,
                      track_ring=(), track_decision=None) -> StrollPath:
    """Build the right-side stroll path for a route centerline.

    ``pace_sec`` is seconds per kilometer and ``period_sec`` is the interval
    between location updates. Their product is the distance advanced per
    update. ``anchors`` (checkpoints and the start) are visited exactly. The
    whole path is generated up front; playback only interpolates it.
    """
    pts = _rdp(_dedupe(route_points), CENTER_SIMPLIFY_M)
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

    turns = _vertex_turns(center)
    reversals = sorted(center.cum[i] for i, turn, _l1, _l2 in turns
                       if abs(turn) >= REVERSAL_ANGLE_RAD)
    pin_targets = _pin_targets(center, anchors, reversals)
    corners = _build_corners(center, turns, reversals, half, preferred)
    track_zones = _track_lane_zones(center, track_ring, track_decision,
                                    preferred, lat_max, corners, seed)
    # Checkpoints that sit on or next to a corner arc are reached by a local
    # bend afterwards; fading the lane there would fight the arc.
    near_corner = [s for s, _p in pin_targets
                   if any(c.s_in - 1.0 <= s <= c.s_out + 1.0 for c in corners)]
    anchor_s = sorted({0.0, center.total}
                      | {s for s, _p in pin_targets
                         if not any(abs(s - nc) < 1e-9 for nc in near_corner)})
    rng = random.Random(seed)
    max_rad = math.radians(max_deg)

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

    # Schedule the events (corner arcs and U-turn loops) in route order.
    events = [(c.s_in, 0, c) for c in corners]
    events.extend((station, 1, None) for station in reversals)
    events.sort(key=lambda e: (e[0], e[1]))
    event_index = 0
    anchor_index = 0
    dev = rng.uniform(-1.0, 1.0) * NOISE_MAX_M

    dense: list[tuple[float, float]] = [center.pts[0]]
    dense_s: list[float] = [0.0]
    pinned: list[int] = [0]

    def push(p: tuple[float, float], station: float) -> None:
        if math.dist(dense[-1], p) > 1e-9:
            dense.append(p)
            dense_s.append(station)

    s = 0.0
    guard = 0
    limit = int(center.total / ds) + 2
    while s < center.total - 1e-9 and guard < limit * 2 + 16:
        guard += 1
        s2 = min(center.total, s + ds)

        if event_index < len(events) and events[event_index][0] <= s2 + 1e-9:
            station, kind, corner = events[event_index]
            event_index += 1
            if kind == 0:
                # Corner: the arc replaces the lane path around the junction.
                span = corner.s_out - corner.s_in
                n = max(1, len(corner.points) - 1)
                for i, p in enumerate(corner.points):
                    push(p, corner.s_in + span * i / n)
                s = corner.s_out
            else:
                # U-turn: join the two lanes with a half circle. The loop keeps
                # the current lane distance so it meets the approach smoothly.
                lat = max(lat_min, min(lat_max, preferred + dev))
                push(_offset_point(center, station - 1e-3, lat, reversals),
                     station)
                for p in _turn_arc(center, station, lat, reversals):
                    push(p, station)
                s = station
            continue

        dev += ds * math.sin(theta_at(s2)) + ds * pull_per_m * (0.0 - dev)
        dev = max(-NOISE_MAX_M, min(NOISE_MAX_M, dev))

        # Touch checkpoints exactly; the samples around them fade the offset.
        while (anchor_index < len(anchor_s)
               and anchor_s[anchor_index] <= s2):
            station = anchor_s[anchor_index]
            anchor_index += 1
            if station <= s + 1e-9:
                continue
            push(center.at(station)[0], station)
            pinned.append(len(dense) - 1)

        track_lane = _track_lane_value(s2, track_zones)
        base = (_lane_target(s2, corners, preferred)
                if track_lane is None else track_lane)
        # On the track the runner keeps to the chosen lane; a full road-width
        # wander would smear several lanes into one band.
        noise = _noise_scale(s2, corners)
        if track_lane is not None:
            noise *= TRACK_LANE_NOISE
        lane = base + dev * noise
        lane = max(-lat_max, min(lat_max, lane))
        lane *= _anchor_factor(s2, anchor_s)
        push(_offset_point(center, s2, lane, reversals), s2)
        s = s2

    push(center.pts[-1], center.total)
    pinned.append(len(dense) - 1)

    # A corner arc replaces the lane, so a checkpoint that sits just before or
    # after a corner would be missed. Pull the nearest sample onto it with a
    # local smooth bend (the lane path already touches checkpoints on straights).
    pin_half = max(3, int(round(1.5 * ANCHOR_ZONE_M / SAMPLE_M)))
    for s_target, target in pin_targets:
        i = min(range(len(dense_s)), key=lambda k: abs(dense_s[k] - s_target))
        dx = target[0] - dense[i][0]
        dy = target[1] - dense[i][1]
        if math.hypot(dx, dy) <= 0.02:
            continue
        lo = max(0, i - pin_half)
        hi = min(len(dense), i + pin_half + 1)
        for j in range(lo, hi):
            w = _smoothstep(1.0 - abs(j - i) / float(pin_half))
            dense[j] = (dense[j][0] + dx * w, dense[j][1] + dy * w)
        pinned.append(i)
    pinned = sorted(set(pinned))

    simplified = _simplify(dense, SIMPLIFY_M, pinned)
    return StrollPath(tuple(simplified), _path_len(simplified), step_m,
                      period, max_deg, len(reversals))
