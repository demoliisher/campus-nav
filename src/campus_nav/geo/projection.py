# -*- coding: utf-8 -*-
"""Projection between longitude/latitude and a local metric plane."""

from __future__ import annotations

import math


class LocalProjection:
    """Longitude/latitude -> local metric coordinates.

    At campus scale (about 1 km) an equirectangular approximation is enough;
    the x and y scales are computed from the center latitude, and the distance
    error is far below the tracing error of the roads themselves.
    """

    def __init__(self, lon0: float, lat0: float):
        self.lon0 = lon0
        self.lat0 = lat0
        phi = math.radians(lat0)
        self.mx = 111412.84 * math.cos(phi) - 93.5 * math.cos(3.0 * phi)
        self.my = 111132.92 - 559.82 * math.cos(2.0 * phi) + 1.175 * math.cos(4.0 * phi)

    def xy(self, lon: float, lat: float) -> tuple[float, float]:
        return ((lon - self.lon0) * self.mx, (lat - self.lat0) * self.my)

    def lonlat(self, x: float, y: float) -> tuple[float, float]:
        return (self.lon0 + x / self.mx, self.lat0 + y / self.my)
