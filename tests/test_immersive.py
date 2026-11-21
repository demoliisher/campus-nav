# -*- coding: utf-8 -*-
"""Immersive simulation state machine."""

import math
import random

import pytest

from campus_nav.routing.immersive import ImmersiveSession


def _path_len(points):
    return sum(math.dist(a, b) for a, b in zip(points, points[1:]))


@pytest.fixture()
def start_xy(cmap):
    return cmap.proj.xy(113.41517874, 22.47824096)


def test_start_reveals_first_checkpoint(cmap, start_xy):
    session = ImmersiveSession(cmap, rng=random.Random(20261120))
    run = session.start(start_xy)
    assert run is not None
    assert run.kind == "immersive"
    assert run.completed == 0
    assert not run.finished
    assert len(run.points_xy) == 1
    assert len(run.legs) == 1
    assert run.path[0] == pytest.approx(start_xy)
    assert run.legs[0].ac_m > 0.0
    assert run.total_m == pytest.approx(_path_len(run.path), abs=1e-6)


def test_checkpoints_revealed_one_by_one(cmap, start_xy):
    session = ImmersiveSession(cmap, rng=random.Random(20261120))
    run = session.start(start_xy)
    assert run is not None

    counts = [len(run.points_xy)]
    for _ in range(4):
        run = session.advance()
        assert run is not None
        counts.append(len(run.points_xy))
        assert run.total_m == pytest.approx(_path_len(run.path), abs=1e-6)
    assert counts == [1, 2, 3, 4, 5]
    assert run.completed == 4
    assert not run.finished

    run = session.advance()
    assert run is not None
    assert run.finished
    assert run.completed == 5
    assert len(run.points_xy) == 5
    assert len(run.legs) == 5


def test_full_snapshot_plans_five_and_keeps_the_reveal(cmap, start_xy):
    session = ImmersiveSession(cmap, rng=random.Random(20261120))
    run = session.start(start_xy)
    assert run is not None and len(run.points_xy) == 1

    full = session.full_snapshot()
    assert full is not None
    assert len(full.points_xy) == 5
    assert len(full.legs) == 5
    assert full.completed == 0
    assert not full.finished
    assert full.total_m > run.total_m
    assert full.total_m == pytest.approx(_path_len(full.path), abs=1e-6)

    # The preview did not consume the reveal: checkpoint 2 is still next.
    nxt = session.advance()
    assert nxt is not None and len(nxt.points_xy) == 2
    assert nxt.points_xy[:2] == full.points_xy[:2]

    # Restart rewinds the same plan to the first checkpoint.
    rewound = session.restart()
    assert rewound is not None
    assert len(rewound.points_xy) == 1
    assert rewound.points_xy[0] == full.points_xy[0]
    assert rewound.completed == 0
    assert not rewound.finished


def test_checkpoints_on_roads_and_separated(cmap, start_xy):
    session = ImmersiveSession(
        cmap, min_sep_m=60.0, rng=random.Random(20261120)
    )
    run = session.start(start_xy)
    assert run is not None
    for _ in range(4):
        run = session.advance()
        assert run is not None

    for point in run.points_xy:
        assert cmap.nearest_projection(*point).dist < 1e-6
    points = [start_xy] + run.points_xy
    for i in range(len(points)):
        for j in range(i + 1, len(points)):
            assert math.dist(points[i], points[j]) >= 60.0 - 1e-6


def test_daily_cap_extension(cmap, start_xy):
    session = ImmersiveSession(
        cmap, run_limit=True, daily_cap_m=8000.0,
        rng=random.Random(20261120),
    )
    run = session.start(start_xy)
    assert run is not None
    for _ in range(5):
        run = session.advance()
        assert run is not None

    assert run.finished
    assert run.limit_state == "track"
    assert run.track_m > 0.0
    assert run.total_m == pytest.approx(8000.0, abs=1.0)
    assert run.total_m == pytest.approx(_path_len(run.path), abs=1e-6)


def test_advance_after_finish_is_stable(cmap, start_xy):
    session = ImmersiveSession(cmap, rng=random.Random(20261120))
    run = session.start(start_xy)
    assert run is not None
    for _ in range(5):
        run = session.advance()
        assert run is not None

    before = (len(run.path), run.total_m, run.completed, run.finished)
    again = session.advance()
    assert again is not None
    after = (len(again.path), again.total_m, again.completed, again.finished)
    assert after == before
