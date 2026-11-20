# -*- coding: utf-8 -*-
"""Campus fence (electronic fence supplied by the server) loading."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Fence:
    """A fence; ``rings`` holds the GCJ-02 outer rings of its polygons."""

    name: str
    desc: str
    rings: tuple[tuple[tuple[float, float], ...], ...]


def load_fences(path: str | Path) -> list[Fence]:
    """Load a GeoJSON fence file; supports Polygon and MultiPolygon."""
    p = Path(path)
    if not p.exists():
        return []
    data = json.loads(p.read_text(encoding="utf-8"))
    fences: list[Fence] = []
    for idx, feat in enumerate(data.get("features") or []):
        geom = feat.get("geometry") or {}
        gtype = geom.get("type")
        coords = geom.get("coordinates") or []
        if gtype == "Polygon":
            polys = [coords]
        elif gtype == "MultiPolygon":
            polys = coords
        else:
            continue
        props = feat.get("properties") or {}
        rings = []
        for poly in polys:
            if not poly:
                continue
            outer = poly[0]
            ring = tuple(
                (float(pos[0]), float(pos[1])) for pos in outer if len(pos) >= 2
            )
            if len(ring) >= 3:
                rings.append(ring)
        if not rings:
            continue
        name = str(props.get("name") or props.get("desc") or f"Fence {idx + 1}")
        desc = str(props.get("desc") or name)
        fences.append(Fence(name, desc, tuple(rings)))
    return fences
