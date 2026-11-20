# -*- coding: utf-8 -*-
"""Geometry and projection helpers."""

from .paths import append_path, lerp, polyline_midpoint, truncate_path
from .polygons import (
    point_in_polygon,
    point_on_boundary,
    proper_cross,
    segment_inside,
)
from .projection import LocalProjection

__all__ = [
    "LocalProjection",
    "append_path",
    "lerp",
    "point_in_polygon",
    "point_on_boundary",
    "polyline_midpoint",
    "proper_cross",
    "segment_inside",
    "truncate_path",
]
