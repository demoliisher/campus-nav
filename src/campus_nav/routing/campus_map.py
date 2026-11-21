# -*- coding: utf-8 -*-
"""Campus road network: GeoJSON loading, graph construction, routing, and run planning."""

from __future__ import annotations

import json
import math
import random
from collections import defaultdict
from pathlib import Path

from ..geo.paths import append_path, lerp, polyline_midpoint, truncate_path
from ..geo.polygons import point_in_polygon, segment_inside
from ..geo.projection import LocalProjection
from .dijkstra import dijkstra
from .models import Area, CapExtension, Edge, PointRun, Projection, RouteResult


def _iter_positions(features):
    for feat in features:
        geom = feat.get("geometry") or {}
        gtype = geom.get("type")
        if gtype == "LineString":
            for pos in geom.get("coordinates") or []:
                yield pos
        elif gtype == "Polygon":
            for ring in geom.get("coordinates") or []:
                for pos in ring:
                    yield pos


class CampusMap:
    """Turn roads, rings, and areas from a GeoJSON file into a routable undirected graph."""

    def __init__(self, path: str | Path):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        self.features = data.get("features") or []
        positions = list(_iter_positions(self.features))
        if not positions:
            raise ValueError("GeoJSON contains no usable road coordinates")
        lon0 = sum(p[0] for p in positions) / len(positions)
        lat0 = sum(p[1] for p in positions) / len(positions)
        self.proj = LocalProjection(lon0, lat0)

        self._node_index: dict[tuple[float, float], int] = {}
        self._pairs: set[tuple[int, int]] = set()
        self.coords: list[tuple[float, float]] = []       # node coordinates in meters
        self.edges: list[Edge] = []
        self.areas: list[Area] = []
        self.edge_area: dict[int, int] = {}               # edge -> area (boundary/chord)
        self.crossing_eids: set[int] = set()              # in-area chord edges
        self.labels: list[tuple[str, tuple[float, float]]] = []

        for fi, feat in enumerate(self.features):
            geom = feat.get("geometry") or {}
            props = feat.get("properties") or {}
            desc = str(props.get("desc") or f"Feature {fi + 1}")
            gtype = geom.get("type")

            if gtype == "LineString":
                lonlat = [tuple(p[:2]) for p in geom.get("coordinates") or []]
                self._add_polyline(lonlat, fi, desc, "line")
                if len(lonlat) >= 2:
                    self.labels.append(
                        (desc, polyline_midpoint([self.proj.xy(*p) for p in lonlat]))
                    )

            elif gtype == "Polygon":
                rings = geom.get("coordinates") or []
                if not rings:
                    continue
                kind = "area" if str(props.get("traverse", "area")) == "area" else "ring"
                run_dir = str(props.get("run_direction", "")).strip().lower()
                if run_dir in ("ccw", "counterclockwise"):
                    run_dir = "ccw"
                elif run_dir in ("cw", "clockwise"):
                    run_dir = "cw"
                else:
                    run_dir = ""
                if kind != "ring":
                    run_dir = ""                 # areas are freely traversable; no direction
                outer = [tuple(p[:2]) for p in rings[0]]
                if len(outer) < 3:
                    continue
                if outer[0] == outer[-1]:
                    outer = outer[:-1]
                ring_xy = [self.proj.xy(*p) for p in outer]
                if run_dir:
                    # Align vertex order with the requested direction (planar
                    # coordinates, y points north).
                    area2 = 0.0
                    n = len(ring_xy)
                    for i in range(n):
                        x1, y1 = ring_xy[i]
                        x2, y2 = ring_xy[(i + 1) % n]
                        area2 += x1 * y2 - x2 * y1
                    stored_ccw = area2 > 0.0
                    if (run_dir == "ccw") != stored_ccw:
                        outer.reverse()
                        ring_xy.reverse()
                node_ids = [self._node(*p) for p in outer]
                boundary_eids = []
                edge_kind = "area_boundary" if kind == "area" else "ring"
                for i in range(len(outer)):
                    eid = self._add_segment(
                        outer[i], outer[(i + 1) % len(outer)], fi, desc, edge_kind,
                        oneway=bool(run_dir),
                    )
                    if eid is not None:
                        boundary_eids.append(eid)
                ai = len(self.areas)
                self.areas.append(
                    Area(desc, kind, ring_xy, node_ids, boundary_eids, [],
                         run_direction=run_dir)
                )
                for eid in boundary_eids:
                    self.edge_area[eid] = ai
                cx = sum(p[0] for p in ring_xy) / len(ring_xy)
                cy = sum(p[1] for p in ring_xy) / len(ring_xy)
                self.labels.append((desc, (cx, cy)))
                # Inner rings (holes) contribute boundaries only, never a
                # traversable area.
                for hole in rings[1:]:
                    pts = [tuple(p[:2]) for p in hole]
                    if len(pts) >= 3:
                        if pts[0] == pts[-1]:
                            pts = pts[:-1]
                        for i in range(len(pts)):
                            self._add_segment(pts[i], pts[(i + 1) % len(pts)], fi, desc, "ring")

        if not self.coords:
            raise ValueError("Map contains no valid nodes")

        # Add area chords only after all lines and rings are built, so they do
        # not duplicate existing roads.
        for ai, area in enumerate(self.areas):
            if area.kind == "area":
                self._build_area_chords(ai)

        self.adj: list[list[tuple[int, float, int, bool | None]]] = [[] for _ in self.coords]
        for eid, e in enumerate(self.edges):
            self.adj[e.u].append((e.v, e.length, eid, True))
            if not e.oneway:
                self.adj[e.v].append((e.u, e.length, eid, False))

        self.proj_eids = [i for i, e in enumerate(self.edges) if e.kind != "area_chord"]
        self.road_eids = list(self.proj_eids)
        self.proj_total_m = sum(self.edges[eid].length for eid in self.proj_eids)

        xs = [p[0] for p in self.coords]
        ys = [p[1] for p in self.coords]
        self.bounds = (min(xs), min(ys), max(xs), max(ys))

    # ---- Graph construction ----------------------------------------------
    def _node(self, lon: float, lat: float) -> int:
        key = (round(lon, 7), round(lat, 7))
        idx = self._node_index.get(key)
        if idx is None:
            idx = len(self.coords)
            self._node_index[key] = idx
            self.coords.append(self.proj.xy(lon, lat))
        return idx

    def _add_segment(self, a, b, feature_id: int, desc: str, kind: str,
                     oneway: bool = False) -> int | None:
        u = self._node(a[0], a[1])
        v = self._node(b[0], b[1])
        if u == v:
            return None
        ax, ay = self.coords[u]
        bx, by = self.coords[v]
        length = math.hypot(bx - ax, by - ay)
        if length < 1e-6:
            return None
        eid = len(self.edges)
        self.edges.append(Edge(u, v, length, feature_id, desc, kind, oneway))
        self._pairs.add((u, v) if u < v else (v, u))
        return eid

    def _add_polyline(self, lonlat, feature_id: int, desc: str, kind: str) -> list[int]:
        eids = []
        for a, b in zip(lonlat, lonlat[1:]):
            eid = self._add_segment(a, b, feature_id, desc, kind)
            if eid is not None:
                eids.append(eid)
        return eids

    def _build_area_chords(self, ai: int) -> None:
        """Create visible interior chords between boundary vertices of an area."""
        area = self.areas[ai]
        nodes = area.node_ids
        n = len(nodes)
        for i in range(n):
            for j in range(i + 1, n):
                u, v = nodes[i], nodes[j]
                pair = (u, v) if u < v else (v, u)
                if pair in self._pairs:
                    continue
                a = self.coords[u]
                b = self.coords[v]
                if not segment_inside(a, b, area.ring_xy):
                    continue
                length = math.hypot(b[0] - a[0], b[1] - a[1])
                if length < 1e-6:
                    continue
                eid = len(self.edges)
                self.edges.append(Edge(u, v, length, -1, area.desc, "area_chord"))
                self._pairs.add(pair)
                area.chord_eids.append(eid)
                self.edge_area[eid] = ai
                self.crossing_eids.add(eid)

    def area_at(self, x: float, y: float) -> int | None:
        for ai, area in enumerate(self.areas):
            if area.kind == "area" and point_in_polygon(x, y, area.ring_xy):
                return ai
        return None

    # ---- Queries ---------------------------------------------------------
    def components(self) -> list[list[int]]:
        # Check physical connectivity on the undirected network; one-way
        # restrictions do not change whether a node is reachable.
        und: list[list[int]] = [[] for _ in self.coords]
        for e in self.edges:
            und[e.u].append(e.v)
            und[e.v].append(e.u)
        seen: set[int] = set()
        comps: list[list[int]] = []
        for s in range(len(self.coords)):
            if s in seen:
                continue
            stack = [s]
            seen.add(s)
            comp = []
            while stack:
                u = stack.pop()
                comp.append(u)
                for v in und[u]:
                    if v not in seen:
                        seen.add(v)
                        stack.append(v)
            comps.append(comp)
        return comps

    def nearest_projection(self, x: float, y: float) -> Projection:
        """Orthogonal projection of a point onto the nearest road (clamped to the segment).

        Interior area chords are excluded: C and D must land on real roads or
        boundaries.
        """
        best: Projection | None = None
        for eid in self.proj_eids:
            e = self.edges[eid]
            ax, ay = self.coords[e.u]
            bx, by = self.coords[e.v]
            dx = bx - ax
            dy = by - ay
            ll = dx * dx + dy * dy
            if ll <= 0.0:
                continue
            t = ((x - ax) * dx + (y - ay) * dy) / ll
            t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
            fx = ax + t * dx
            fy = ay + t * dy
            d = math.hypot(x - fx, y - fy)
            if best is None or d < best.dist - 1e-9:
                best = Projection(eid, t, (fx, fy), d)
        if best is None:
            raise ValueError("Road network is empty; cannot project")
        return best

    def shortest_route(self, axy: tuple[float, float], bxy: tuple[float, float],
                       avoid_dirs=None, snap_radius: float = 0.0) -> RouteResult | None:
        """A, B -> nearest-road feet C, D -> temporary connectors -> shortest C to D route.

        ``avoid_dirs`` is a set of directed edges whose reverse must be avoided
        (reduce-backtracking passes only the last edge of the previous leg to
        avoid an immediate reversal at a checkpoint); if no route remains, the
        exclusion is dropped.

        ``snap_radius`` suppresses a connector when the point lies within that
        distance of its nearest road: the route then starts directly at the
        projected foot, so the short off-road hop is neither drawn nor counted.
        """
        pa = self.nearest_projection(*axy)
        pb = self.nearest_projection(*bxy)

        coords = list(self.coords)
        adj = [list(lst) for lst in self.adj]
        next_eid = len(self.edges)
        local_crossing: dict[int, int] = {}        # query-added crossing edge -> area

        def link(a: int, b: int, w: float, eid: int) -> None:
            """Add sub-edges following the original edge direction; one-way edges only a -> b."""
            adj[a].append((b, w, eid, True))
            if not self.edges[eid].oneway:
                adj[b].append((a, w, eid, False))

        # Split the original edges containing C and D and insert virtual
        # nodes; two feet on the same edge must be split together.
        groups: dict[int, list[tuple[float, str]]] = defaultdict(list)
        groups[pa.edge_id].append((pa.t, "C"))
        groups[pb.edge_id].append((pb.t, "D"))

        tag_node: dict[str, int] = {}
        for eid, items in groups.items():
            e = self.edges[eid]
            u, v = e.u, e.v
            adj[u] = [item for item in adj[u] if item[2] != eid]
            adj[v] = [item for item in adj[v] if item[2] != eid]

            prev_node = u
            prev_t = 0.0
            for t, tag in sorted(items):
                if abs(t - prev_t) <= 1e-12:
                    tag_node[tag] = prev_node
                    continue
                if t <= 1e-9:
                    tag_node[tag] = u
                    continue
                if t >= 1.0 - 1e-9:
                    tag_node[tag] = v
                    continue
                node = len(coords)
                coords.append(lerp(self.coords[u], self.coords[v], t))
                adj.append([])
                w = (t - prev_t) * e.length
                link(prev_node, node, w, eid)
                tag_node[tag] = node
                prev_node, prev_t = node, t

            w = (1.0 - prev_t) * e.length
            if w > 1e-9:
                link(prev_node, v, w, eid)

        c_node = tag_node["C"]
        d_node = tag_node["D"]

        def add_crossing_edge(u: int, v: int, area_idx: int) -> None:
            nonlocal next_eid
            if u == v:
                return
            w = math.dist(coords[u], coords[v])
            if w < 1e-9:
                return
            eid = next_eid
            next_eid += 1
            adj[u].append((v, w, eid, None))
            adj[v].append((u, w, eid, None))
            local_crossing[eid] = area_idx

        def area_of_boundary_edge(eid: int) -> int | None:
            if self.edges[eid].kind != "area_boundary":
                return None
            return self.edge_area.get(eid)

        # When C or D lands in the middle of an area boundary, connect it to
        # every boundary vertex of that area, so the plaza can still be crossed
        # directly from this edge.
        for proj, node_tag in ((pa, "C"), (pb, "D")):
            if not (0.0 < proj.t < 1.0):
                continue
            ai = area_of_boundary_edge(proj.edge_id)
            if ai is None:
                continue
            area = self.areas[ai]
            node = tag_node[node_tag]
            here = coords[node]
            e = self.edges[proj.edge_id]
            for vid in area.node_ids:
                if vid == node or vid in (e.u, e.v):
                    continue
                if not segment_inside(here, coords[vid], area.ring_xy):
                    continue
                add_crossing_edge(node, vid, ai)

        # C and D lie on different edges of the same area boundary: connect
        # them with a direct interior chord.
        ai_c = area_of_boundary_edge(pa.edge_id)
        ai_d = area_of_boundary_edge(pb.edge_id)
        if (ai_c is not None and ai_c == ai_d and pa.edge_id != pb.edge_id
                and 0.0 < pa.t < 1.0 and 0.0 < pb.t < 1.0):
            area = self.areas[ai_c]
            if segment_inside(coords[c_node], coords[d_node], area.ring_xy):
                add_crossing_edge(c_node, d_node, ai_c)

        solved = dijkstra(adj, len(coords), c_node, d_node, forbidden=avoid_dirs)
        avoid_fallback = False
        if solved is None and avoid_dirs:
            # Only option: drop the backtracking exclusion and fall back to
            # the ordinary shortest route.
            solved = dijkstra(adj, len(coords), c_node, d_node)
            avoid_fallback = True
        if solved is None:
            return None
        net_m, node_path, used_eids, used_dirs, last_dir = solved
        path = [coords[i] for i in node_path]

        areas_used: set[int] = set()
        a_area = self.area_at(*axy)
        b_area = self.area_at(*bxy)
        if a_area is not None:
            areas_used.add(a_area)
        if b_area is not None:
            areas_used.add(b_area)
        for eid in used_eids:
            if eid in local_crossing:
                areas_used.add(local_crossing[eid])
            elif eid in self.crossing_eids:
                areas_used.add(self.edge_area[eid])

        snap_a = snap_radius > 0.0 and pa.dist <= snap_radius + 1e-9
        snap_b = snap_radius > 0.0 and pb.dist <= snap_radius + 1e-9
        ac_m = 0.0 if snap_a else pa.dist
        bd_m = 0.0 if snap_b else pb.dist
        return RouteResult(
            a_xy=axy,
            b_xy=bxy,
            c_xy=pa.foot,
            d_xy=pb.foot,
            c_lonlat=self.proj.lonlat(*pa.foot),
            d_lonlat=self.proj.lonlat(*pb.foot),
            path=path,
            ac_m=ac_m,
            net_m=net_m,
            bd_m=bd_m,
            total_m=ac_m + net_m + bd_m,
            proj_a=pa,
            proj_b=pb,
            areas_used=frozenset(areas_used),
            used_dirs=used_dirs,
            last_dir=last_dir,
            avoid_fallback=avoid_fallback,
            snap_a=snap_a,
            snap_b=snap_b,
        )

    # ---- Simulated scoring run -------------------------------------------
    def random_road_point(self, rng=None) -> tuple[float, float]:
        """Pick a length-uniform random point on a traversable road and return lon/lat."""
        rng = rng or random
        if self.proj_total_m <= 0.0:
            raise ValueError("Road network is empty; cannot sample a point")
        r = rng.random() * self.proj_total_m
        chosen = self.proj_eids[-1]
        for eid in self.proj_eids:
            e = self.edges[eid]
            if r <= e.length:
                chosen = eid
                break
            r -= e.length
        e = self.edges[chosen]
        ax, ay = self.coords[e.u]
        bx, by = self.coords[e.v]
        t = rng.random()
        return self.proj.lonlat(ax + (bx - ax) * t, ay + (by - ay) * t)

    def _track_access_nodes(self, track: Area) -> list[int]:
        """Track vertices that connect to non-track roads (track entrances)."""
        boundary = set(track.boundary_eids)
        incident: dict[int, list[int]] = defaultdict(list)
        for eid, e in enumerate(self.edges):
            if eid in boundary:
                continue
            incident[e.u].append(eid)
            incident[e.v].append(eid)
        out: list[int] = []
        for nid in track.node_ids:
            if incident.get(nid) and nid not in out:
                out.append(nid)
        return out

    def _track_loop_path(self, track: Area, access_node: int,
                         distance: float) -> list[tuple[float, float]]:
        """Run counterclockwise from a track entrance for distance meters (multiple laps allowed)."""
        if distance <= 1e-9 or not track.boundary_eids:
            return []
        idx = next(
            (i for i, eid in enumerate(track.boundary_eids)
             if self.edges[eid].u == access_node),
            None,
        )
        if idx is None:
            return []
        pts = [self.coords[access_node]]
        remaining = float(distance)
        n = len(track.boundary_eids)
        guard = 0
        while remaining > 1e-9 and guard < 300000:
            guard += 1
            e = self.edges[track.boundary_eids[idx]]
            take = min(remaining, e.length)
            if e.length > 1e-12:
                pts.append(lerp(self.coords[e.u], self.coords[e.v],
                                 take / e.length))
            else:
                pts.append(self.coords[e.v])
            remaining -= take
            idx = (idx + 1) % n
        return pts

    def plan_cap_extension(self, last_xy: tuple[float, float],
                           base_total: float, daily_cap_m: float,
                           avoid_dirs=None) -> CapExtension:
        """Travel from last_xy to the track and keep running until the daily cap is reached.

        The returned path contains both the trip to the track entrance and the
        track laps (if the cap is reached en route, only the truncated transit
        leg is kept). extra_m = transit_m + track_m.
        """
        cap_m = float(daily_cap_m)
        track = next(
            (a for a in self.areas
             if a.desc == "Running Track" and a.kind == "ring"),
            None,
        )
        if track is None:
            return CapExtension(cap_m=cap_m, limit_state="no_track")
        if base_total >= cap_m - 1e-9:
            return CapExtension(cap_m=cap_m, limit_state="over")

        best = None
        for nid in self._track_access_nodes(track):
            transit = self.shortest_route(
                last_xy, self.coords[nid], avoid_dirs=avoid_dirs
            )
            if transit is None:
                continue
            if best is None or transit.total_m < best[1].total_m:
                best = (nid, transit)
        if best is None:
            return CapExtension(cap_m=cap_m, limit_state="no_track")

        entry_node, transit = best
        need = cap_m - base_total
        if transit.total_m >= need - 1e-9:
            # Cap reached before or just after entering the track.
            return CapExtension(
                cap_m=cap_m,
                transit_m=min(transit.total_m, need),
                track_m=0.0,
                limit_state="transit",
                transit_leg=transit,
                track_entry_xy=self.coords[entry_node],
                areas_used=transit.areas_used,
                path=truncate_path(transit.path, need),
                extra_m=need,
            )

        transit_m = transit.total_m
        track_m = cap_m - base_total - transit_m
        track_path = self._track_loop_path(track, entry_node, track_m)
        path: list[tuple[float, float]] = []
        append_path(path, transit.path)
        append_path(path, track_path)
        return CapExtension(
            cap_m=cap_m,
            transit_m=transit_m,
            track_m=track_m,
            limit_state="track",
            transit_leg=transit,
            track_entry_xy=self.coords[entry_node],
            areas_used=transit.areas_used,
            path=path,
            extra_m=cap_m - base_total,
        )

    def plan_point_run(self, n: int = 5, min_sep_m: float = 40.0,
                       reduce_backtrack: bool = False, run_limit: bool = False,
                       daily_cap_m: float = 8000.0, rng=None,
                       points=None,
                       snap_radius: float = 0.0) -> PointRun | None:
        """Generate or reuse n ordered road points and chain them 1 -> 2 -> ... -> n.

        With reduce_backtrack, only an immediate reversal at a checkpoint is
        avoided: the next leg may not traverse the last edge of the previous
        leg in reverse (the edge leading to the GeoJSON point just visited).
        If no other route exists, the exclusion is dropped
        (RouteResult.avoid_fallback=True); other repeats are allowed.

        With run_limit, after the five checkpoints the route heads to the track
        via the shortest path (hidden point 6) and continues counterclockwise
        until the total reaches daily_cap_m (8 km male / 5 km female).

        ``snap_radius`` is passed to each leg: user points within that distance
        of a road join it directly, with no temporary connector.
        """
        if points is None:
            rng = rng or random.Random()
            target = max(2, n)
            pts_ll: list[tuple[float, float]] = []
            pts_xy: list[tuple[float, float]] = []
            attempts = 0
            max_attempts = target * 400
            while len(pts_xy) < target and attempts < max_attempts:
                attempts += 1
                p = self.random_road_point(rng)
                xy = self.proj.xy(*p)
                if all(math.dist(xy, q) >= min_sep_m for q in pts_xy):
                    pts_ll.append(p)
                    pts_xy.append(xy)
            if len(pts_xy) < target:
                return None
        else:
            pts_ll = [tuple(p) for p in points]
            if len(pts_ll) < 2:
                return None
            pts_xy = [self.proj.xy(*p) for p in pts_ll]

        legs: list[RouteResult] = []
        leg_m: list[float] = []
        areas: set[int] = set()
        path: list[tuple[float, float]] = []
        prev_last = None
        for i in range(len(pts_xy) - 1):
            avoid = None
            if reduce_backtrack and i > 0 and prev_last is not None:
                avoid = {prev_last}
            leg = self.shortest_route(
                pts_xy[i], pts_xy[i + 1], avoid_dirs=avoid,
                snap_radius=snap_radius,
            )
            if leg is None:
                return None
            legs.append(leg)
            leg_m.append(leg.total_m)
            areas.update(leg.areas_used)
            prev_last = leg.last_dir
            if not path:
                path.extend(leg.path)
            elif math.dist(path[-1], leg.path[0]) < 1e-6:
                path.extend(leg.path[1:])
            else:
                path.extend(leg.path)
        if not path:
            return None

        # Random points already lie on roads; snap the endpoints so the drawn
        # path passes exactly through the numbered checkpoints.
        if math.dist(path[0], pts_xy[0]) <= 1.0:
            path[0] = pts_xy[0]
        if math.dist(path[-1], pts_xy[-1]) <= 1.0:
            path[-1] = pts_xy[-1]
        clean = [path[0]]
        for p in path[1:]:
            if math.dist(clean[-1], p) > 1e-9:
                clean.append(p)

        base_total = sum(leg_m)
        cap_m = 0.0
        transit_m = 0.0
        track_m = 0.0
        limit_state = ""
        transit_leg = None
        track_entry_xy = None
        full = list(clean)
        total_m = base_total

        if run_limit and daily_cap_m > 0.0:
            avoid = None
            if reduce_backtrack and legs and legs[-1].last_dir is not None:
                avoid = {legs[-1].last_dir}
            extension = self.plan_cap_extension(
                pts_xy[-1], base_total, daily_cap_m, avoid_dirs=avoid
            )
            cap_m = extension.cap_m
            transit_m = extension.transit_m
            track_m = extension.track_m
            limit_state = extension.limit_state
            transit_leg = extension.transit_leg
            track_entry_xy = extension.track_entry_xy
            areas.update(extension.areas_used)
            append_path(full, extension.path)
            total_m = base_total + extension.extra_m

        return PointRun(
            points_lonlat=pts_ll,
            points_xy=pts_xy,
            legs=legs,
            path=full,
            leg_m=leg_m,
            total_m=total_m,
            areas_used=frozenset(areas),
            cap_m=cap_m,
            transit_m=transit_m,
            track_m=track_m,
            limit_state=limit_state,
            transit_leg=transit_leg,
            track_entry_xy=track_entry_xy,
        )
