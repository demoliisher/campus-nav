# -*- coding: utf-8 -*-
"""Project paths and default map data."""

from __future__ import annotations

from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
DATA_DIR = PACKAGE_DIR / "data"
DEFAULT_MAP = DATA_DIR / "map_gcj02.geojson"
DEFAULT_FENCE = DATA_DIR / "fence_gcj02.geojson"


def default_map_path() -> Path:
    """Return the packaged default map path (GCJ-02)."""
    return DEFAULT_MAP


def default_fence_path() -> Path:
    """Return the packaged default fence path (GCJ-02)."""
    return DEFAULT_FENCE
