# -*- coding: utf-8 -*-
"""Fence loading and datum alignment."""

from campus_nav.config import default_fence_path
from campus_nav.geo.fence import load_fences
from campus_nav.geo.polygons import point_in_polygon, point_on_boundary


def test_load_fence_polygon():
    fences = load_fences(default_fence_path())
    assert len(fences) == 1
    fence = fences[0]
    assert fence.name == "Campus"
    assert fence.rings
    assert len(fence.rings[0]) >= 3


def test_missing_fence_returns_empty(tmp_path):
    assert load_fences(tmp_path / "missing.geojson") == []


def test_fence_encloses_campus_map(cmap):
    """The fence should enclose the road network; a datum mismatch puts every point outside."""
    fences = load_fences(default_fence_path())
    assert fences
    rings = [
        [cmap.proj.xy(lon, lat) for lon, lat in ring]
        for fence in fences for ring in fence.rings
    ]

    total = inside = 0
    for x, y in cmap.coords:
        total += 1
        if any(
            point_in_polygon(x, y, ring) or point_on_boundary(x, y, ring)
            for ring in rings
        ):
            inside += 1
    # The three campus gates lie on the fence boundary; a few outside points are fine.
    assert inside / total > 0.95
