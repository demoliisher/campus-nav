# -*- coding: utf-8 -*-
"""Basic geometry tests for polygons and line segments."""

from __future__ import annotations

import math


def point_in_polygon(x: float, y: float, ring) -> bool:
    """Return True if the point is inside the polygon (ray casting)."""
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > y) != (yj > y):
            x_cross = (xj - xi) * (y - yi) / (yj - yi) + xi
            if x < x_cross:
                inside = not inside
        j = i
    return inside


def point_on_boundary(x: float, y: float, ring) -> bool:
    """Return True if the point lies on the polygon boundary (tolerance 1e-6 m)."""
    for i in range(len(ring)):
        ax, ay = ring[i]
        bx, by = ring[(i + 1) % len(ring)]
        dx, dy = bx - ax, by - ay
        ll = dx * dx + dy * dy
        if ll <= 0:
            continue
        t = ((x - ax) * dx + (y - ay) * dy) / ll
        if t < -1e-9 or t > 1.0 + 1e-9:
            continue
        px, py = ax + t * dx, ay + t * dy
        if math.hypot(x - px, y - py) < 1e-6:
            return True
    return False


def proper_cross(p1, p2, p3, p4) -> bool:
    """Return True if two segments cross strictly (shared endpoints do not count)."""

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    d1 = cross(p3, p4, p1)
    d2 = cross(p3, p4, p2)
    d3 = cross(p1, p2, p3)
    d4 = cross(p1, p2, p4)
    return (d1 * d2 < 0.0) and (d3 * d4 < 0.0)


def segment_inside(a, b, ring) -> bool:
    """Return True if the segment lies entirely inside the polygon (boundary included)."""
    mx = (a[0] + b[0]) / 2.0
    my = (a[1] + b[1]) / 2.0
    if not (point_in_polygon(mx, my, ring) or point_on_boundary(mx, my, ring)):
        return False
    n = len(ring)
    for i in range(n):
        p = ring[i]
        q = ring[(i + 1) % n]
        if proper_cross(a, b, p, q):
            return False
    return True
