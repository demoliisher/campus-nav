# -*- coding: utf-8 -*-
"""Data structures for the road network and planning results."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class Edge:
    u: int
    v: int
    length: float
    feature_id: int
    desc: str
    kind: str = "line"          # line / ring / area_boundary / area_chord
    oneway: bool = False        # if True, only u -> v is allowed


@dataclass(slots=True)
class Area:
    desc: str
    kind: str                   # ring / area
    ring_xy: list[tuple[float, float]]
    node_ids: list[int]
    boundary_eids: list[int]
    chord_eids: list[int]
    run_direction: str = ""     # ccw / cw (rings only)


@dataclass(slots=True)
class Projection:
    edge_id: int
    t: float
    foot: tuple[float, float]
    dist: float


@dataclass(slots=True)
class RouteResult:
    a_xy: tuple[float, float]
    b_xy: tuple[float, float]
    c_xy: tuple[float, float]
    d_xy: tuple[float, float]
    c_lonlat: tuple[float, float]
    d_lonlat: tuple[float, float]
    path: list[tuple[float, float]]
    ac_m: float
    net_m: float
    bd_m: float
    total_m: float
    proj_a: Projection
    proj_b: Projection
    areas_used: frozenset[int]
    used_dirs: frozenset[tuple[int, bool]] = frozenset()
    last_dir: tuple[int, bool] | None = None
    avoid_fallback: bool = False


@dataclass(slots=True)
class CapExtension:
    """Leg appended for the daily cap: travel to the track and keep running."""

    cap_m: float
    transit_m: float = 0.0
    track_m: float = 0.0
    limit_state: str = ""                  # over / transit / track / no_track
    transit_leg: RouteResult | None = None
    track_entry_xy: tuple[float, float] | None = None
    areas_used: frozenset[int] = frozenset()
    path: list[tuple[float, float]] = field(default_factory=list)
    extra_m: float = 0.0                   # transit_m + track_m


@dataclass(slots=True)
class PointRun:
    """Scoring run: five ordered waypoints and the ordered route (normal or immersive)."""
    points_lonlat: list[tuple[float, float]]
    points_xy: list[tuple[float, float]]
    legs: list[RouteResult]
    path: list[tuple[float, float]]
    leg_m: list[float]
    total_m: float
    areas_used: frozenset[int]
    cap_m: float = 0.0                     # daily cap in meters when enabled
    transit_m: float = 0.0                 # checkpoint 5 -> track entrance (hidden point 6)
    track_m: float = 0.0                   # distance run on the track
    limit_state: str = ""                  # over / transit / track / no_track
    transit_leg: RouteResult | None = None
    track_entry_xy: tuple[float, float] | None = None
    kind: str = "auto"                     # auto / immersive
    start_lonlat: tuple[float, float] | None = None
    start_xy: tuple[float, float] | None = None
    completed: int = 0                     # immersive mode: checkpoints completed
    finished: bool = False
