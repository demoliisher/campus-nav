# -*- coding: utf-8 -*-
"""Projection, polyline, and polygon helpers."""

import pytest

from campus_nav.geo.paths import append_path, polyline_midpoint, truncate_path
from campus_nav.geo.polygons import point_in_polygon, segment_inside
from campus_nav.geo.projection import LocalProjection

SQUARE = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]


def test_projection_roundtrip():
    proj = LocalProjection(113.4088, 22.4778)
    lon, lat = 113.4101, 22.4809
    assert proj.lonlat(*proj.xy(lon, lat)) == pytest.approx((lon, lat), abs=1e-12)


def test_projection_scale_is_metric():
    proj = LocalProjection(113.4, 22.4)
    dx, dy = proj.xy(113.401, 22.401)
    assert 90.0 < dx < 115.0
    assert 110.0 < dy < 112.0


def test_polyline_midpoint():
    assert polyline_midpoint([(0.0, 0.0), (10.0, 0.0)]) == (5.0, 0.0)
    assert polyline_midpoint([(0.0, 0.0), (3.0, 0.0), (3.0, 4.0)]) == (3.0, 0.5)


def test_truncate_path():
    pts = [(0.0, 0.0), (3.0, 0.0), (3.0, 4.0)]
    out = truncate_path(pts, 5.0)
    assert out[-1] == pytest.approx((3.0, 2.0))
    assert truncate_path(pts, 2.0)[-1] == pytest.approx((2.0, 0.0))


def test_append_path_dedupes_joint():
    dst = [(0.0, 0.0), (1.0, 0.0)]
    append_path(dst, [(1.0, 0.0), (2.0, 0.0)])
    assert dst == [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0)]


def test_point_in_polygon():
    assert point_in_polygon(5.0, 5.0, SQUARE)
    assert not point_in_polygon(15.0, 5.0, SQUARE)


def test_segment_inside():
    assert segment_inside((2.0, 2.0), (8.0, 8.0), SQUARE)
    assert not segment_inside((2.0, 2.0), (12.0, 2.0), SQUARE)
