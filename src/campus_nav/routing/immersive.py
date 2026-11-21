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

    The whole five-checkpoint run (and the optional daily-cap extension) is
    planned when the start point is chosen; playback only reveals it one
    checkpoint at a time. Keeping the full plan around lets the UI preview all
    five checkpoints and their walk path without consuming the generator, and
    restart the same run afterwards.

    The user picks the start point on the map; the first leg projects it
    orthogonally onto the nearest road (a temporary connector) before joining
    the network.
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
        self._points_ll: list[tuple[float, float]] = []
        self._points_xy: list[tuple[float, float]] = []
        self._legs: list[RouteResult] = []
        self._leg_m: list[float] = []
        self._leg_areas: list[frozenset[int]] = []
        self._cap: CapExtension | None = None
        self._revealed = 0
        self._completed = 0
        self._finished = False

    # ---- Public state ----------------------------------------------------
    def start(self, start_xy: tuple[float, float]) -> PointRun | None:
        """Use a map point as the start and plan the complete run."""
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
        self._leg_areas = [frozenset(leg.areas_used)]
        self._snap_leg(leg, start_xy, cp_xy)

        while len(self._legs) < self.n:
            source = self._points_xy[-1]
            cp_ll = self._new_point(self._points_xy + [self._start_xy])
            if cp_ll is None:
                return None
            cp_xy = self.cmap.proj.xy(*cp_ll)
            leg = self.cmap.shortest_route(
                source, cp_xy, avoid_dirs=self._avoid_dirs()
            )
            if leg is None:
                return None
            self._snap_leg(leg, source, cp_xy)
            self._points_ll.append(cp_ll)
            self._points_xy.append(cp_xy)
            self._legs.append(leg)
            self._leg_m.append(leg.total_m)
            self._leg_areas.append(frozenset(leg.areas_used))

        self._cap = self._plan_cap()
        self._revealed = 1
        self._completed = 0
        self._finished = False
        self.run = self._snapshot()
        return self.run

    def advance(self) -> PointRun | None:
        """Reveal the next checkpoint (or finish after the last one)."""
        if self.run is None:
            return None
        if self._finished:
            return self.run

        if self._revealed >= self.n:
            self._completed = self.n
            self._finished = True
        else:
            self._revealed += 1
            self._completed = self._revealed - 1
        self.run = self._snapshot()
        return self.run

    def restart(self) -> PointRun | None:
        """Rewind to the first checkpoint, keeping the planned run."""
        if self.run is None:
            return None
        self._revealed = 1
        self._completed = 0
        self._finished = False
        self.run = self._snapshot()
        return self.run

    def full_snapshot(self) -> PointRun | None:
        """All planned checkpoints and legs, without changing the reveal state.

        The daily-cap extension (track transit plus laps) is included when the
        cap is enabled, so a preview can show the complete run.
        """
        if self.run is None:
            return None
        return self._snapshot(revealed=self.n, completed=0, finished=False,
                              with_cap=True)

    def refresh_cap(self) -> PointRun | None:
        """Recompute the cap extension after the cap or gender setting changes."""
        if self.run is None:
            return None
        self._cap = self._plan_cap()
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

    def _avoid_dirs(self) -> set[tuple[int, bool]] | None:
        if not self.reduce_backtrack or not self._legs:
            return None
        last = self._legs[-1].last_dir
        return {last} if last is not None else None

    def _plan_cap(self) -> CapExtension | None:
        if not self.run_limit or self.daily_cap_m <= 0.0 or not self._legs:
            return None
        return self.cmap.plan_cap_extension(
            self._points_xy[-1], sum(self._leg_m), self.daily_cap_m,
            avoid_dirs=self._avoid_dirs(),
        )

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

    def _snapshot(self, revealed: int | None = None,
                  completed: int | None = None,
                  finished: bool | None = None,
                  with_cap: bool | None = None) -> PointRun:
        revealed = (self._revealed if revealed is None
                    else max(1, min(self.n, int(revealed))))
        completed = self._completed if completed is None else int(completed)
        finished = self._finished if finished is None else bool(finished)
        include_cap = finished if with_cap is None else bool(with_cap)
        legs = self._legs[:revealed]
        path: list[tuple[float, float]] = (
            [self._start_xy] if self._start_xy is not None else []
        )
        areas: set[int] = set()
        for leg, leg_areas in zip(legs, self._leg_areas[:revealed]):
            append_path(path, leg.path)
            areas.update(leg_areas)
        extension = self._cap if include_cap else None
        if extension is not None:
            append_path(path, extension.path)
            areas.update(extension.areas_used)
        base_total = sum(self._leg_m[:revealed])
        return PointRun(
            points_lonlat=list(self._points_ll[:revealed]),
            points_xy=list(self._points_xy[:revealed]),
            legs=list(legs),
            path=path,
            leg_m=list(self._leg_m[:revealed]),
            total_m=base_total + (extension.extra_m if extension else 0.0),
            areas_used=frozenset(areas),
            cap_m=extension.cap_m if extension else 0.0,
            transit_m=extension.transit_m if extension else 0.0,
            track_m=extension.track_m if extension else 0.0,
            limit_state=extension.limit_state if extension else "",
            transit_leg=extension.transit_leg if extension else None,
            track_entry_xy=extension.track_entry_xy if extension else None,
            kind="immersive",
            start_lonlat=self._start_ll,
            start_xy=self._start_xy,
            completed=completed,
            finished=finished,
        )
