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


def test_snap_radius_suppresses_short_connector(cmap):
    along = cmap.proj.xy(113.41513, 22.47796)
    edge = cmap.edges[cmap.nearest_projection(*along).edge_id]
    ax, ay = cmap.coords[edge.u]
    bx, by = cmap.coords[edge.v]
    length = math.dist((ax, ay), (bx, by))
    nx, ny = -(by - ay) / length, (bx - ax) / length
    off = (((ax + bx) / 2.0) + nx * 30.0, ((ay + by) / 2.0) + ny * 30.0)

    proj = cmap.nearest_projection(*off)
    snapped = cmap.shortest_route(off, proj.foot, snap_radius=proj.dist + 1.0)
    kept = cmap.shortest_route(off, proj.foot, snap_radius=proj.dist - 5.0)

    assert snapped is not None and snapped.snap_a
    assert snapped.ac_m == 0.0
    assert kept is not None and not kept.snap_a
    assert kept.ac_m == pytest.approx(proj.dist)


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


def test_random_points_avoid_gate_roads(cmap):
    excluded = cmap.random_excluded_features
    assert excluded, "map data should mark the gate roads"

    deg = [0] * len(cmap.coords)
    for e in cmap.edges:
        deg[e.u] += 1
        deg[e.v] += 1
    gate_features = {e.feature_id for e in cmap.edges
                     if deg[e.u] == 1 or deg[e.v] == 1}
    assert gate_features and gate_features <= excluded

    rng = random.Random(20261121)
    for _ in range(300):
        point = cmap.random_road_point(rng)
        proj = cmap.nearest_projection(*cmap.proj.xy(*point))
        edge = cmap.edges[proj.edge_id]
        # Only an exact shared node could resolve to a gate edge.
        assert (edge.feature_id not in excluded
                or not (1e-9 < proj.t < 1.0 - 1e-9))


def test_generated_runs_avoid_gate_roads(cmap):
    excluded = cmap.random_excluded_features
    for seed in range(5):
        run = cmap.plan_point_run(5, min_sep_m=40.0, rng=random.Random(seed))
        assert run is not None
        for xy in run.points_xy:
            proj = cmap.nearest_projection(*xy)
            assert cmap.edges[proj.edge_id].feature_id not in excluded
