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
def test_corner_is_rounded_and_avoids_the_raw_vertex():
    route = [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0)]
    path = build_stroll_path(
        route, pace_sec=300.0, period_sec=1.0, max_deg=5.0, seed=3,
    )
    points = list(path.points)
    # No spike: the turn is spread over several samples.
    assert _worst_turn(points) < 50.0
    # The path does not step on the GeoJSON vertex itself; it may pass near it
    # only as part of a wide arc around the corner.
    assert min(math.dist(p, route[1]) for p in points) > 0.5


def test_inside_corner_is_cut_instead_of_folding():
    route = [(0.0, 0.0), (100.0, 0.0), (100.0, -100.0)]
    path = build_stroll_path(
        route, pace_sec=300.0, period_sec=1.0, max_deg=5.0, seed=3,
    )
    points = list(path.points)
    assert _worst_turn(points) < 50.0
    assert min(math.dist(p, route[1]) for p in points) > 0.5


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
