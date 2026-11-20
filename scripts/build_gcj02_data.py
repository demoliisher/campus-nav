# -*- coding: utf-8 -*-
"""Build the GCJ-02 data used by the GUI from the source files.

    uv run python scripts/build_gcj02_data.py

The road source is WGS-84 (map_wgs84.geojson); the fence source is BD-09 from
the server's lat/lng export (fence_bd09.geojson). Both outputs are GCJ-02.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from campus_nav.geo.datum import (  # noqa: E402
    bd09_to_gcj02,
    convert_geojson,
    load_geojson,
    save_geojson,
    wgs84_to_gcj02,
)

DATA = ROOT / "src" / "campus_nav" / "data"


def build_map() -> Path:
    src = DATA / "map_wgs84.geojson"
    dst = DATA / "map_gcj02.geojson"
    data = convert_geojson(load_geojson(src), wgs84_to_gcj02)
    data["name"] = "GDPU ZS Campus Roads"
    data["datum"] = "GCJ-02"
    data["source"] = src.name
    save_geojson(data, dst)
    return dst


def build_fence() -> Path:
    src = DATA / "fence_bd09.geojson"
    dst = DATA / "fence_gcj02.geojson"
    data = convert_geojson(load_geojson(src), bd09_to_gcj02)
    data["name"] = "GDPU ZS Campus Fence"
    data["datum"] = "GCJ-02"
    data["source"] = src.name
    save_geojson(data, dst)
    return dst


def main() -> int:
    for path in (build_map(), build_fence()):
        print(f"Generated {path.relative_to(ROOT)} ({path.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
