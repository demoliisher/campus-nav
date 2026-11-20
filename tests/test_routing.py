# -*- coding: utf-8 -*-
"""Road network, routing, and scoring runs."""

import math
import random

import pytest

from campus_nav.checks import run_check


def test_self_check_passes(cmap):
    assert run_check(cmap)


def test_network_connected(cmap):
    comps = cmap.components()
    assert len(comps) == 1
    assert len(comps[0]) == len(cmap.coords)


def test_area_and_ring_semantics(cmap):
    area = next(a for a in cmap.areas if a.kind == "area")
    assert area.chord_eids
    track = next(a for a in cmap.areas if a.desc == "Running Track")
    assert track.kind == "ring"
    assert track.run_direction == "ccw"
    assert all(cmap.edges[eid].oneway for eid in track.boundary_eids)
    assert not track.chord_eids


def test_shortest_route_cross_campus(cmap):
    a = cmap.proj.xy(113.41532666, 22.47806091)
    b = cmap.proj.xy(113.41332929, 22.47436157)
    route = cmap.shortest_route(a, b)
    assert route is not None
    assert math.isclose(route.total_m, route.ac_m + route.net_m + route.bd_m)
    path_len = sum(
        math.dist(p, q) for p, q in zip(route.path, route.path[1:])
    )
    assert math.isclose(path_len, route.net_m, abs_tol=1e-6)
    assert 100.0 < route.total_m < 5000.0


def test_point_run(cmap):
    run = cmap.plan_point_run(5, min_sep_m=40.0, rng=random.Random(20261120))
    assert run is not None
    assert len(run.points_xy) == 5
    assert len(run.legs) == 4
    assert all(cmap.nearest_projection(*p).dist < 1e-6 for p in run.points_xy)
    path_len = sum(math.dist(p, q) for p, q in zip(run.path, run.path[1:]))
    assert math.isclose(path_len, run.total_m, abs_tol=1e-6)


def test_point_run_reduce_backtrack(cmap):
    run = cmap.plan_point_run(
        5,
        min_sep_m=40.0,
        reduce_backtrack=True,
        rng=random.Random(20261120),
    )
    assert run is not None
    assert len(run.legs) == 4


def test_daily_cap(cmap):
    base = cmap.plan_point_run(5, min_sep_m=40.0, rng=random.Random(20261120))
    assert base is not None

    male = cmap.plan_point_run(
        points=base.points_lonlat, run_limit=True, daily_cap_m=8000.0
    )
    assert male is not None
    assert male.limit_state == "track"
    assert male.track_m > 0.0
    assert male.total_m == pytest.approx(8000.0, abs=1.0)

    female = cmap.plan_point_run(
        points=base.points_lonlat, run_limit=True, daily_cap_m=5000.0
    )
    assert female is not None
    assert female.total_m == pytest.approx(5000.0, abs=1.0)

    over = cmap.plan_point_run(
        points=base.points_lonlat, run_limit=True, daily_cap_m=100.0
    )
    assert over is not None
    assert over.limit_state == "over"
    assert over.track_m == 0.0
    assert over.total_m == pytest.approx(base.total_m)
