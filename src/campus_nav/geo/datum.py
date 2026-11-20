# -*- coding: utf-8 -*-
"""Datum conversion: WGS-84 / BD-09 -> GCJ-02.

The map source data is WGS-84, while the server fence is BD-09 when exported
from its ``lat/lng`` fields. The UI uses GCJ-02 throughout, so both are
converted before drawing.
"""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path
from typing import Callable, Iterable

# Krasovsky 1940 ellipsoid parameters used by the official GCJ-02 algorithm
_A = 6378245.0
_EE = 0.00669342162296594323


def out_of_china(lon: float, lat: float) -> bool:
    """GCJ-02 only offsets coordinates within mainland China; return others as-is."""
    return not (73.66 < lon < 135.05 and 3.86 < lat < 53.55)


def _transform_lat(x: float, y: float) -> float:
    ret = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y
    ret += 0.2 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * math.pi)
            + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(y * math.pi)
            + 40.0 * math.sin(y / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (160.0 * math.sin(y / 12.0 * math.pi)
            + 320.0 * math.sin(y * math.pi / 30.0)) * 2.0 / 3.0
    return ret


def _transform_lon(x: float, y: float) -> float:
    ret = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y
    ret += 0.1 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * math.pi)
            + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(x * math.pi)
            + 40.0 * math.sin(x / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (150.0 * math.sin(x / 12.0 * math.pi)
            + 300.0 * math.sin(x / 30.0 * math.pi)) * 2.0 / 3.0
    return ret


def wgs84_to_gcj02(lon: float, lat: float) -> tuple[float, float]:
    """WGS-84 longitude/latitude -> GCJ-02 longitude/latitude."""
    if out_of_china(lon, lat):
        return lon, lat
    dlat = _transform_lat(lon - 105.0, lat - 35.0)
    dlon = _transform_lon(lon - 105.0, lat - 35.0)
    rad_lat = lat / 180.0 * math.pi
    magic = math.sin(rad_lat)
    magic = 1.0 - _EE * magic * magic
    sqrt_magic = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((_A * (1.0 - _EE)) / (magic * sqrt_magic) * math.pi)
    dlon = (dlon * 180.0) / (_A / sqrt_magic * math.cos(rad_lat) * math.pi)
    return lon + dlon, lat + dlat


def bd09_to_gcj02(lon: float, lat: float) -> tuple[float, float]:
    """BD-09 longitude/latitude -> GCJ-02 longitude/latitude."""
    x = lon - 0.0065
    y = lat - 0.006
    z = math.sqrt(x * x + y * y) - 0.00002 * math.sin(y * math.pi * 3000.0 / 180.0)
    theta = math.atan2(y, x) - 0.000003 * math.cos(x * math.pi * 3000.0 / 180.0)
    return z * math.cos(theta), z * math.sin(theta)


def _convert_position(pos: Iterable[float],
                      transform: Callable[[float, float], tuple[float, float]]
                      ) -> list[float]:
    values = list(pos)
    lon, lat = transform(float(values[0]), float(values[1]))
    return [round(lon, 8), round(lat, 8), *values[2:]]


def _convert_geometry(geom: dict,
                      transform: Callable[[float, float], tuple[float, float]]
                      ) -> None:
    coords = geom.get("coordinates")
    if coords is None:
        return
    gtype = geom.get("type")
    if gtype in ("Point",):
        geom["coordinates"] = _convert_position(coords, transform)
    elif gtype in ("MultiPoint", "LineString"):
        geom["coordinates"] = [_convert_position(p, transform) for p in coords]
    elif gtype in ("MultiLineString", "Polygon"):
        geom["coordinates"] = [
            [_convert_position(p, transform) for p in part] for part in coords
        ]
    elif gtype == "MultiPolygon":
        geom["coordinates"] = [
            [[_convert_position(p, transform) for p in ring] for ring in poly]
            for poly in coords
        ]


def convert_geojson(data: dict,
                    transform: Callable[[float, float], tuple[float, float]]
                    ) -> dict:
    """Return a copy of a GeoJSON object with coordinates and ``@circle.center`` converted."""
    out = copy.deepcopy(data)
    for feat in out.get("features") or []:
        geom = feat.get("geometry") or {}
        _convert_geometry(geom, transform)
        props = feat.get("properties") or {}
        circle = props.get("@circle")
        if isinstance(circle, dict):
            center = circle.get("center")
            if isinstance(center, (list, tuple)) and len(center) >= 2:
                circle["center"] = _convert_position(center, transform)
    return out


def load_geojson(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save_geojson(data: dict, path: str | Path) -> None:
    Path(path).write_text(
        json.dumps(data, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
