# -*- coding: utf-8 -*-
"""Datum conversion."""

import pytest

from campus_nav.geo.datum import (
    bd09_to_gcj02,
    convert_geojson,
    out_of_china,
    wgs84_to_gcj02,
)


def test_wgs84_to_gcj02_reference_point():
    # Common reference value: WGS-84 (116.404, 39.915) -> GCJ-02
    lon, lat = wgs84_to_gcj02(116.404, 39.915)
    assert lon == pytest.approx(116.41024449916938, abs=1e-8)
    assert lat == pytest.approx(39.91640428150164, abs=1e-8)


def test_bd09_to_gcj02_reference_point():
    lon, lat = bd09_to_gcj02(116.404, 39.915)
    assert lon == pytest.approx(116.39762729119315, abs=1e-8)
    assert lat == pytest.approx(39.90865673957631, abs=1e-8)


def test_out_of_china_keeps_coordinates():
    assert out_of_china(-0.1276, 51.5072)
    assert wgs84_to_gcj02(-0.1276, 51.5072) == (-0.1276, 51.5072)


def test_convert_geojson_copies_and_converts_circle_center():
    data = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[116.404, 39.915], [116.405, 39.915],
                                 [116.405, 39.916], [116.404, 39.915]]],
            },
            "properties": {"@circle": {"center": [116.404, 39.915]}},
        }],
    }
    out = convert_geojson(data, wgs84_to_gcj02)
    got = out["features"][0]
    assert got["geometry"]["coordinates"][0][0] == pytest.approx(
        [116.4102445, 39.91640428]
    )
    assert got["properties"]["@circle"]["center"] == pytest.approx(
        [116.4102445, 39.91640428]
    )
    # Source data must not be mutated.
    assert data["features"][0]["geometry"]["coordinates"][0][0] == [
        116.404, 39.915,
    ]
    assert data["features"][0]["properties"]["@circle"]["center"] == [
        116.404, 39.915,
    ]
