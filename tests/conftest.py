# -*- coding: utf-8 -*-
"""Shared pytest fixtures."""

from __future__ import annotations

import pytest

from campus_nav.config import default_map_path
from campus_nav.routing.campus_map import CampusMap


@pytest.fixture(scope="session")
def map_path():
    path = default_map_path()
    if not path.exists():
        pytest.skip(f"Missing map file: {path}")
    return path


@pytest.fixture(scope="session")
def cmap(map_path):
    return CampusMap(map_path)
