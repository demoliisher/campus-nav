# -*- coding: utf-8 -*-
"""Immersive simulation: a scoring-run state machine that reveals checkpoints one at a time."""

from __future__ import annotations

import math
import random

from ..geo.paths import append_path
from .campus_map import CampusMap
from .models import CapExtension, PointRun, RouteResult


class ImmersiveSession:
    """Immersive scoring run.

    Unlike ``CampusMap.plan_point_run``, which generates all five points at
    once, this session reveals one checkpoint at a time: the next checkpoint
    is generated only after the current target is reached. The user picks the
    start point on the map; the first leg projects it orthogonally onto the
    nearest road (a temporary connector) before joining the network.
    """

    def __init__(self, cmap: CampusMap, n: int = 5, min_sep_m: float = 60.0,
                 reduce_backtrack: bool = False, run_limit: bool = False,
                 daily_cap_m: float = 8000.0, rng=None):
        self.cmap = cmap
        self.n = max(2, int(n))
        self.min_sep_m = float(min_sep_m)
        self.reduce_backtrack = reduce_backtrack
        self.run_limit = run_limit
        self.daily_cap_m = float(daily_cap_m)
        self.rng = rng or random.Random()

        self.run: PointRun | None = None
        self._start_xy: tuple[float, float] | None = None
        self._start_ll: tuple[float, float] | None = None
        self._points_xy: list[tuple[float, float]] = []
        self._points_ll: list[tuple[float, float]] = []
        self._legs: list[RouteResult] = []
        self._leg_m: list[float] = []
        self._path: list[tuple[float, float]] = []
        self._areas: set[int] = set()
        self._last_dir: tuple[int, bool] | None = None
        self._completed = 0
        self._finished = False
        self._cap: CapExtension | None = None

    # ---- Public state ----------------------------------------------------
    def start(self, start_xy: tuple[float, float]) -> PointRun | None:
        """Use a map point as the start, reveal checkpoint 1, and plan the connector."""
        if self.run is not None:
            return self.run

        start_xy = (float(start_xy[0]), float(start_xy[1]))
        cp_ll = self._new_point([start_xy])
        if cp_ll is None:
            return None
        cp_xy = self.cmap.proj.xy(*cp_ll)
        leg = self.cmap.shortest_route(start_xy, cp_xy)
        if leg is None:
            return None

        self._start_xy = start_xy
        self._start_ll = self.cmap.proj.lonlat(*start_xy)
        self._points_ll = [cp_ll]
        self._points_xy = [cp_xy]
        self._legs = [leg]
        self._leg_m = [leg.total_m]
        self._snap_leg(leg, start_xy, cp_xy)
        self._path = [start_xy]
        append_path(self._path, leg.path)
        self._last_dir = leg.last_dir
        self._areas = set(leg.areas_used)
        self._completed = 0
        self._finished = False
        self._cap = None
        self.run = self._snapshot()
        return self.run

    def advance(self) -> PointRun | None:
        """Called after the current target is reached: reveal the next checkpoint, finish, or extend to the cap."""
        if self.run is None:
            return None
        if self._finished:
            return self.run

        next_completed = self._completed + 1
        if next_completed >= self.n:
            # The last checkpoint has been reached: extend to the track if
            # requested, then finish.
            self._completed = next_completed
            self._apply_cap()
            self._finished = True
            self.run = self._snapshot()
            return self.run

        current_xy = self._points_xy[-1]
        cp_ll = self._new_point(self._points_xy + [self._start_xy])
        if cp_ll is None:
            return None
        cp_xy = self.cmap.proj.xy(*cp_ll)
        avoid = None
        if self.reduce_backtrack and self._last_dir is not None:
            avoid = {self._last_dir}
        leg = self.cmap.shortest_route(current_xy, cp_xy, avoid_dirs=avoid)
        if leg is None:
            return None

        self._completed = next_completed
        self._snap_leg(leg, current_xy, cp_xy)
        self._points_ll.append(cp_ll)
        self._points_xy.append(cp_xy)
        self._legs.append(leg)
        self._leg_m.append(leg.total_m)
        append_path(self._path, leg.path)
        self._last_dir = leg.last_dir
        self._areas.update(leg.areas_used)
        self.run = self._snapshot()
        return self.run

    # ---- Internal --------------------------------------------------------
    def _new_point(self, exclude) -> tuple[float, float] | None:
        """Pick a random point on a road, away from the start and existing checkpoints."""
        attempts = 0
        while attempts < 4000:
            attempts += 1
            p = self.cmap.random_road_point(self.rng)
            xy = self.cmap.proj.xy(*p)
            if all(math.dist(xy, q) >= self.min_sep_m for q in exclude):
                return p
        return None

    @staticmethod
    def _snap_leg(leg: RouteResult,
                  source_xy: tuple[float, float],
                  dest_xy: tuple[float, float]) -> None:
        """Snap leg endpoints to the actual start/end to avoid tiny projection tails."""
        if not leg.path:
            return
        if math.dist(leg.path[0], source_xy) <= 1.0:
            leg.path[0] = source_xy
        if math.dist(leg.path[-1], dest_xy) <= 1.0:
            leg.path[-1] = dest_xy

    def _apply_cap(self) -> None:
        if not self.run_limit or self.daily_cap_m <= 0.0:
            return
        base_total = sum(self._leg_m)
        avoid = None
        if self.reduce_backtrack and self._last_dir is not None:
            avoid = {self._last_dir}
        extension = self.cmap.plan_cap_extension(
            self._points_xy[-1], base_total, self.daily_cap_m,
            avoid_dirs=avoid,
        )
        self._cap = extension
        append_path(self._path, extension.path)
        self._areas.update(extension.areas_used)

    def _snapshot(self) -> PointRun:
        base_total = sum(self._leg_m)
        extension = self._cap
        return PointRun(
            points_lonlat=list(self._points_ll),
            points_xy=list(self._points_xy),
            legs=list(self._legs),
            path=list(self._path),
            leg_m=list(self._leg_m),
            total_m=base_total + (extension.extra_m if extension else 0.0),
            areas_used=frozenset(self._areas),
            cap_m=extension.cap_m if extension else 0.0,
            transit_m=extension.transit_m if extension else 0.0,
            track_m=extension.track_m if extension else 0.0,
            limit_state=extension.limit_state if extension else "",
            transit_leg=extension.transit_leg if extension else None,
            track_entry_xy=extension.track_entry_xy if extension else None,
            kind="immersive",
            start_lonlat=self._start_ll,
            start_xy=self._start_xy,
            completed=self._completed,
            finished=self._finished,
        )
