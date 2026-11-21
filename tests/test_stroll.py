# -*- coding: utf-8 -*-
"""Stroll path generation for scoring-run playback."""

import math

import pytest

from campus_nav.routing.stroll import (
    HALF_ROAD_WIDTH_M,
    build_stroll_path,
)


def _distance_to_route(point, route):
    px, py = point
    best = math.inf
    for a, b in zip(route, route[1:]):
        dx = b[0] - a[0]
        dy = b[1] - a[1]
        ll = dx * dx + dy * dy
        t = 0.0 if ll <= 0.0 else ((px - a[0]) * dx + (py - a[1]) * dy) / ll
        t = min(1.0, max(0.0, t))
        fx = a[0] + t * dx
        fy = a[1] + t * dy
        best = min(best, math.hypot(px - fx, py - fy))
    return best


def _worst_turn(points, min_seg=0.05):
    """Sharpest direction change between consecutive segments, in degrees."""
    worst = 0.0
    for a, b, c in zip(points, points[1:], points[2:]):
        l1 = math.dist(a, b)
        l2 = math.dist(b, c)
        if l1 < min_seg or l2 < min_seg:
            continue
        dot = ((b[0] - a[0]) * (c[0] - b[0])
               + (b[1] - a[1]) * (c[1] - b[1])) / (l1 * l2)
        worst = max(worst, math.degrees(math.acos(max(-1.0, min(1.0, dot)))))
    return worst


def test_stroll_starts_and_ends_on_route():
    route = [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0)]
    path = build_stroll_path(
        route, pace_sec=300.0, period_sec=1.0, max_deg=5.0, seed=7,
        anchors=route,
    )
    assert path.points[0] == pytest.approx(route[0])
    assert path.points[-1] == pytest.approx(route[-1])
    assert path.total_m > 0.0


def test_stroll_stays_within_half_road_width():
    route = [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0)]
    path = build_stroll_path(
        route, pace_sec=300.0, period_sec=1.0, max_deg=5.0, seed=11,
        anchors=route,
    )
    for point in path.points:
        assert _distance_to_route(point, route) <= HALF_ROAD_WIDTH_M + 1e-6


def test_stroll_keeps_to_the_right_of_centerline():
    route = [(0.0, 0.0), (300.0, 0.0)]
    path = build_stroll_path(
        route, pace_sec=300.0, period_sec=1.0, max_deg=5.0, seed=3,
        anchors=route,
    )
    middle = [p for p in path.points if 20.0 < p[0] < 280.0]
    assert middle
    assert all(p[1] <= 1e-6 for p in middle)


def test_stroll_heading_noise_is_bounded_and_effective():
    route = [(0.0, 0.0), (300.0, 0.0)]
    still = build_stroll_path(
        route, pace_sec=300.0, period_sec=1.0, max_deg=0.0, seed=5,
        anchors=route,
    )
    noisy = build_stroll_path(
        route, pace_sec=300.0, period_sec=1.0, max_deg=5.0, seed=5,
        anchors=route,
    )
    assert still.points != noisy.points
    assert still.max_deg == 0.0
    assert noisy.max_deg == 5.0


def test_stroll_step_size_follows_pace_and_period():
    route = [(0.0, 0.0), (100.0, 0.0)]
    path = build_stroll_path(
        route, pace_sec=300.0, period_sec=2.0, max_deg=2.0, seed=1,
    )
    assert path.step_m == pytest.approx(1000.0 / 300.0 * 2.0)
    assert path.period_sec == 2.0


def test_stroll_pulls_checkpoints_back_to_centerline():
    route = [(0.0, 0.0), (200.0, 0.0)]
    anchor = (100.0, 0.0)
    path = build_stroll_path(
        route, pace_sec=300.0, period_sec=1.0, max_deg=3.0, seed=9,
        anchors=[route[0], anchor, route[-1]],
    )
    assert min(math.dist(p, anchor) for p in path.points) < 0.2


def test_stroll_path_is_precomputed():
    route = [(0.0, 0.0), (100.0, 0.0)]
    path = build_stroll_path(
        route, pace_sec=300.0, period_sec=1.0, max_deg=2.0, seed=2,
    )
    # The whole path is a plain immutable point sequence; playback only
    # interpolates it and never asks for a new target.
    assert isinstance(path.points, tuple)
    assert len(path.points) > 2


def test_left_corner_is_a_tangent_arc_on_the_right_lane():
    # A left turn keeps the runner on the right (outside) lane, so its arc
    # radius is a + d and it sweeps through the junction area. What matters is
    # that it is one smooth tangent arc instead of the old pigtail, and that
    # it never crosses to the far lane.
    route = [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0)]
    path = build_stroll_path(
        route, pace_sec=300.0, period_sec=1.0, max_deg=5.0, seed=3,
    )
    points = list(path.points)
    assert _worst_turn(points) < 50.0
    approach = [p for p in points if p[0] < 90.0]
    departure = [p for p in points if p[1] > 95.0]
    assert approach and all(p[1] <= 1e-9 for p in approach)
    assert departure and all(p[0] >= 100.0 - 1e-9 for p in departure)
    # The path does not step on the GeoJSON vertex itself.
    assert min(math.dist(p, route[1]) for p in points) > 0.3


def test_right_corner_arc_keeps_clear_of_the_junction():
    route = [(0.0, 0.0), (100.0, 0.0), (100.0, -100.0)]
    path = build_stroll_path(
        route, pace_sec=300.0, period_sec=1.0, max_deg=5.0, seed=3,
    )
    points = list(path.points)
    assert _worst_turn(points) < 50.0
    # Right turn: the runner is on the inside lane, radius a - d, so the arc
    # keeps a full road width away from the junction.
    assert min(math.dist(p, route[1]) for p in points) > 4.0


def test_u_turn_is_a_loop_not_an_acute_angle():
    route = [(0.0, 0.0), (120.0, 0.0), (0.0, 0.0)]
    anchor = (120.0, 0.0)
    path = build_stroll_path(
        route, pace_sec=300.0, period_sec=1.0, max_deg=5.0, seed=3,
        anchors=[anchor],
    )
    points = list(path.points)
    assert path.reversals == 1
    assert _worst_turn(points) < 50.0
    # The runner loops around the checkpoint: close, but not on a spike.
    nearest = min(math.dist(p, anchor) for p in points)
    assert 0.5 < nearest < HALF_ROAD_WIDTH_M
    # Out and back stay on opposite sides, so the two lanes do not overlap.
    assert any(p[1] < -1.0 for p in points)
    assert any(p[1] > 1.0 for p in points)



def test_corner_after_a_turnaround_is_a_wide_smooth_arc():
    # The checkpoint sits east of the corner: the runner doubles back, then
    # turns. This used to leave a pigtail right at the junction.
    route = [(0.0, 0.0), (100.0, 0.0), (60.0, 0.0), (60.0, 80.0)]
    path = build_stroll_path(
        route, pace_sec=300.0, period_sec=1.0, max_deg=5.0, seed=3,
        anchors=[(100.0, 0.0)],
    )
    points = list(path.points)
    assert path.reversals == 1
    assert _worst_turn(points) < 50.0
    # A rounded turn keeps clear of the junction point.
    assert min(math.dist(p, (60.0, 0.0)) for p in points) > 2.5


def _circle_ring(n=36, r=60.0):
    return [(r * math.cos(2.0 * math.pi * i / n),
             r * math.sin(2.0 * math.pi * i / n)) for i in range(n)]


def test_track_uses_the_eight_standard_lanes():
    # A standard 400 m track is eight lanes of 1.22 m: lane centres lie 0.61,
    # 1.83, 3.05 and 4.27 m either side of the drawn centerline. The stroll
    # must pick one of those per passage of the northern entrance, not merely
    # the inner or the outer lane, and stay inside the chosen lane.
    ring = _circle_ring()
    route = list(ring) + list(ring) + [ring[0]]
    north = max(ring, key=lambda p: p[1])
    centres = [60.0 + sign * (0.61 + 1.22 * k)
               for k in range(4) for sign in (-1.0, 1.0)]
    used = set()
    for seed in range(1, 9):
        path = build_stroll_path(
            route, pace_sec=300.0, period_sec=1.0, max_deg=5.0, seed=seed,
            track_ring=ring, track_decision=north,
        )
        points = list(path.points)[1:-1]
        assert all(abs(math.hypot(*p) - 60.0) <= 5.0 for p in points)
        in_lane = 0
        for p in points:
            radius = math.hypot(*p)
            centre = min(centres, key=lambda c: abs(c - radius))
            # Lane changes are the only points away from a lane centre.
            assert abs(centre - radius) <= 1.1
            if abs(centre - radius) <= 0.55:
                in_lane += 1
                used.add(round(centre, 2))
        assert in_lane >= 0.9 * len(points)
    # All eight lanes show up across the sampled walks, evenly spaced.
    assert len(used) == 8
    gaps = [round(b - a, 2) for a, b in zip(sorted(used), sorted(used)[1:])]
    assert all(abs(gap - 1.22) < 0.01 for gap in gaps)
    # Without the guide the runner keeps right, i.e., always outside the ring.
    plain = build_stroll_path(
        route, pace_sec=300.0, period_sec=1.0, max_deg=5.0, seed=3,
    )
    assert all(math.hypot(*p) >= 60.0 - 1e-6
               for p in list(plain.points)[1:-1])


def test_track_guide_does_not_touch_other_roads():
    ring = _circle_ring()
    north = max(ring, key=lambda p: p[1])
    route = [(500.0, 0.0), (600.0, 0.0)]
    plain = build_stroll_path(
        route, pace_sec=300.0, period_sec=1.0, max_deg=5.0, seed=3,
    )
    guided = build_stroll_path(
        route, pace_sec=300.0, period_sec=1.0, max_deg=5.0, seed=3,
        track_ring=ring, track_decision=north,
    )
    assert plain.points == guided.points
