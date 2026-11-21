# -*- coding: utf-8 -*-
"""``--check`` self-test: network, routing, one-way rings, backtracking, cap, immersive."""

from __future__ import annotations

import math

from .geo.paths import sample_polyline
from .pacing import fmt_duration, parse_pace
from .routing.campus_map import CampusMap
from .routing.immersive import ImmersiveSession


def run_check(cmap: CampusMap) -> bool:
    results: list[tuple[bool, str]] = []

    def check(name: str, cond: bool, detail: str = "") -> None:
        results.append((cond, f"[{'PASS' if cond else 'FAIL'}] {name}" + (f": {detail}" if detail else "")))

    comps = cmap.components()
    road_km = sum(e.length for e in cmap.edges if e.kind != "area_chord") / 1000.0
    print(f"Map features: {len(cmap.features)}")
    print(f"Network: {len(cmap.coords)} nodes / {len(cmap.edges)} edges"
          f" (interior area chords: {len(cmap.crossing_eids)}), "
          f"total road length {road_km:.3f} km")
    print(f"Connected components: {len(comps)} (largest: {max(len(c) for c in comps)} nodes)")
    for ai, area in enumerate(cmap.areas):
        direction = f", direction {area.run_direction}" if area.run_direction else ""
        print(f"  [{ai}] {area.desc}: {area.kind}, {len(area.boundary_eids)} boundary segments, "
              f"{len(area.chord_eids)} interior chords{direction}")
    check("Road network is fully connected", len(comps) == 1)

    proj = cmap.proj
    area_idxs = [i for i, a in enumerate(cmap.areas) if a.kind == "area"]
    ring_idxs = [i for i, a in enumerate(cmap.areas) if a.kind == "ring"]
    check("Contains a traversable area", bool(area_idxs))
    check("Rings have boundaries only, no interior chords",
          all(not cmap.areas[i].chord_eids for i in ring_idxs))

    # 1) Two points projected onto the same vertical road segment: C->D ~ 0
    #    Coordinates are GCJ-02 (the default map datum).
    a = proj.xy(113.41512875, 22.47796098)
    b = proj.xy(113.41552855, 22.47796084)
    r = cmap.shortest_route(a, b)
    check("Cross-road query is solvable", r is not None)
    if r:
        check("C->D distance is ~0 when projected on the same segment",
              r.net_m < 0.05, f"{r.net_m:.4f} m")
        check("Both feet lie inside their segments",
              0.0 < r.proj_a.t < 1.0 and 0.0 < r.proj_b.t < 1.0,
              f"tA={r.proj_a.t:.3f}, tB={r.proj_b.t:.3f}")
        check("Connector lengths match perpendicular distances",
              abs(r.ac_m - r.proj_a.dist) < 1e-9 and abs(r.bd_m - r.proj_b.dist) < 1e-9)
        check("Total distance = AC + CD + DB",
              abs(r.total_m - (r.ac_m + r.net_m + r.bd_m)) < 1e-9,
              f"{r.total_m:.2f} m")

    # 1b) Snap radius: a point close to a road joins it without a connector.
    line_eids = [i for i, e in enumerate(cmap.edges)
                 if e.kind == "line" and e.length > 60.0]
    if line_eids:
        eid = max(line_eids, key=lambda i: cmap.edges[i].length)
        e = cmap.edges[eid]
        ax, ay = cmap.coords[e.u]
        bx, by = cmap.coords[e.v]
        nx, ny = -(by - ay) / e.length, (bx - ax) / e.length
        off = (((ax + bx) / 2.0) + nx * 30.0, ((ay + by) / 2.0) + ny * 30.0)
        snap_proj = cmap.nearest_projection(*off)
        snapped = cmap.shortest_route(
            off, snap_proj.foot, snap_radius=snap_proj.dist + 1.0
        )
        kept = cmap.shortest_route(
            off, snap_proj.foot, snap_radius=max(0.0, snap_proj.dist - 5.0)
        )
        check("Snap radius removes a short connector",
              snapped is not None and snapped.snap_a and snapped.ac_m == 0.0,
              f"offset {snap_proj.dist:.1f} m")
        check("Beyond the snap radius the connector remains",
              kept is not None and not kept.snap_a and kept.ac_m > 0.0)

    check("Direction arrows repeat at the requested spacing",
          [p for p, _t in sample_polyline([(0.0, 0.0), (250.0, 0.0)], 100.0)]
          == [(100.0, 0.0), (200.0, 0.0)])

    # 2) Area: a point inside the plaza projects to a boundary, and the plaza
    #    can be crossed through an interior chord.
    if area_idxs:
        ai = area_idxs[0]
        area = cmap.areas[ai]
        cx = sum(p[0] for p in area.ring_xy) / len(area.ring_xy)
        cy = sum(p[1] for p in area.ring_xy) / len(area.ring_xy)
        pr = cmap.nearest_projection(cx, cy)
        check("C for a point inside the plaza still lands on a boundary road",
              cmap.edges[pr.edge_id].kind != "area_chord",
              f"edge kind {cmap.edges[pr.edge_id].kind}")
        n = len(area.node_ids)
        u = area.node_ids[n // 3]
        v = area.node_ids[(2 * n) // 3]
        a_xy, b_xy = cmap.coords[u], cmap.coords[v]
        r_area = cmap.shortest_route(a_xy, b_xy)
        straight = math.dist(a_xy, b_xy)
        check("Area can be crossed in a straight line", r_area is not None)
        if r_area:
            check("Area crossing uses interior chords and marks the whole area",
                  ai in r_area.areas_used and r_area.net_m <= straight + 1e-6,
                  f"net={r_area.net_m:.2f} m, straight={straight:.2f} m")
        check("Area has interior chords", len(area.chord_eids) > 10,
              f"{len(area.chord_eids)} chords")

    # 3) Ring: opposite ends of the roundabout can only be reached by circling it.
    if ring_idxs:
        ri = next((i for i in ring_idxs if "Roundabout" in cmap.areas[i].desc), ring_idxs[0])
        ring = cmap.areas[ri]
        west = min(ring.node_ids, key=lambda k: cmap.coords[k][0])
        east = max(ring.node_ids, key=lambda k: cmap.coords[k][0])
        r_ring = cmap.shortest_route(cmap.coords[west], cmap.coords[east])
        check("Roundabout query is solvable", r_ring is not None)
        if r_ring:
            check("Ring can only be circled (no interior crossing)", r_ring.net_m > 50.0,
                  f"{r_ring.net_m:.1f} m (straight-line distance about 42 m)")
            check("Ring path does not highlight the whole area", not r_ring.areas_used)

    # 3b) Running track: the attribute marks it as one-way counterclockwise.
    track = next((a for a in cmap.areas if a.desc == "Running Track"), None)
    check("Track is marked counterclockwise",
          track is not None and track.run_direction == "ccw")
    if track:
        check("All track boundary edges are one-way",
              all(cmap.edges[eid].oneway for eid in track.boundary_eids))
        only_forward = True
        for eid in track.boundary_eids:
            e = cmap.edges[eid]
            fwd_present = any(
                v == e.v and eid2 == eid for v, _w, eid2, _f in cmap.adj[e.u]
            )
            rev_present = any(
                v == e.u and eid2 == eid for v, _w, eid2, _f in cmap.adj[e.v]
            )
            if not fwd_present or rev_present:
                only_forward = False
                break
        check("Track graph allows forward travel only", only_forward)

        seq = [cmap.coords[cmap.edges[eid].u] for eid in track.boundary_eids]
        area2 = 0.0
        for i in range(len(seq)):
            x1, y1 = seq[i]
            x2, y2 = seq[(i + 1) % len(seq)]
            area2 += x1 * y2 - x2 * y1
        check("Track direction is counterclockwise", area2 > 0.0)

        long_eid = max(track.boundary_eids, key=lambda k: cmap.edges[k].length)
        e = cmap.edges[long_eid]
        fwd_r = cmap.shortest_route(cmap.coords[e.u], cmap.coords[e.v])
        rev_r = cmap.shortest_route(cmap.coords[e.v], cmap.coords[e.u])
        check("Track forward direction is traversable",
              fwd_r is not None and fwd_r.net_m < e.length + 1.0)
        check("Track reverse direction requires a detour",
              rev_r is not None and rev_r.net_m > e.length + 20.0,
              f"{rev_r.net_m:.1f} m" if rev_r else "no solution")

        # Reduce backtracking: the two ends of the roundabout go opposite ways,
        # so the reverse of the last edge must be avoided.
        west = min(ring.node_ids, key=lambda k: cmap.coords[k][0]) if ring_idxs else None
        east = max(ring.node_ids, key=lambda k: cmap.coords[k][0]) if ring_idxs else None
        if west is not None and east is not None:
            r_we = cmap.shortest_route(cmap.coords[west], cmap.coords[east])
            if r_we and r_we.last_dir is not None:
                rev_last = (r_we.last_dir[0], not r_we.last_dir[1])
                r_ew = cmap.shortest_route(
                    cmap.coords[east], cmap.coords[west],
                    avoid_dirs={r_we.last_dir},
                )
                check("Reduce backtracking avoids the reverse of the last edge",
                      r_ew is not None and not r_ew.avoid_fallback
                      and rev_last not in r_ew.used_dirs,
                      f"{r_ew.net_m:.1f} m" if r_ew else "no solution")

    # 4) Cross-campus
    a2 = proj.xy(113.41532666, 22.47806091)
    b2 = proj.xy(113.41332929, 22.47436157)
    r2 = cmap.shortest_route(a2, b2)
    check("Cross-campus query is solvable", r2 is not None)
    if r2:
        path_sum = sum(math.dist(p, q) for p, q in zip(r2.path, r2.path[1:]))
        check("Path polyline length = network distance", abs(path_sum - r2.net_m) < 1e-6,
              f"{path_sum:.2f} m vs {r2.net_m:.2f} m")
        check("Cross-campus distance is reasonable (100 m - 5 km)", 100.0 < r2.total_m < 5000.0,
              f"{r2.total_m:.1f} m")

    # 5) A = B
    p = proj.xy(113.4144789, 22.47581122)
    r3 = cmap.shortest_route(p, p)
    check("A=B is solvable", r3 is not None)
    if r3:
        check("A=B gives C=D and total = 2 x perpendicular distance",
              r3.net_m < 1e-9 and abs(r3.total_m - 2.0 * r3.ac_m) < 1e-9,
              f"AC={r3.ac_m:.2f} m, total={r3.total_m:.2f} m")

    # 6) Random sampling
    import random
    rng = random.Random(20261120)
    minx, miny, maxx, maxy = cmap.bounds
    bad = 0
    for _ in range(20):
        ax = rng.uniform(minx, maxx)
        ay = rng.uniform(miny, maxy)
        bx = rng.uniform(minx, maxx)
        by = rng.uniform(miny, maxy)
        rr = cmap.shortest_route((ax, ay), (bx, by))
        if rr is None or not math.isfinite(rr.total_m):
            bad += 1
    check("All 20 random queries are solvable", bad == 0, f"{bad} failed")

    # 7) Pace parsing
    check("Pace defaults and common formats",
          parse_pace("5'00''") == 300 and parse_pace("5:00") == 300
          and parse_pace("5km") == 300 and parse_pace("5") == 300
          and parse_pace("5'") == 300)
    check("Pace boundary values",
          parse_pace("0'05''") == 5 and parse_pace("10'00''") == 600)
    check("Out-of-range or invalid pace rejected",
          parse_pace("0'04''") is None and parse_pace("10'01''") is None
          and parse_pace("5'75''") is None and parse_pace("abc") is None)
    check("Pace formatting",
          fmt_duration(300) == "5'00''" and fmt_duration(195) == "3'15''")

    # 8) Scoring run
    incident: dict[int, list[int]] = {}
    for eid, e in enumerate(cmap.edges):
        incident.setdefault(e.u, []).append(eid)
        incident.setdefault(e.v, []).append(eid)
    gate_eids = [eids[0] for eids in incident.values() if len(eids) == 1]
    gate_features = {cmap.edges[eid].feature_id for eid in gate_eids}
    check("Roads to the campus gates are excluded from random checkpoints",
          bool(gate_features)
          and gate_features <= set(cmap.random_excluded_features),
          ", ".join(cmap.edges[eid].desc for eid in gate_eids))

    sampler = random.Random(20261120)
    stray = 0
    for _ in range(200):
        p = cmap.random_road_point(sampler)
        foot = cmap.nearest_projection(*cmap.proj.xy(*p))
        edge = cmap.edges[foot.edge_id]
        if (edge.feature_id in cmap.random_excluded_features
                and 1e-9 < foot.t < 1.0 - 1e-9):
            stray += 1
    check("Random checkpoints never land on a gate road", stray == 0,
          f"{stray}/200 stray samples")

    sample_run = cmap.plan_point_run(
        5, min_sep_m=40.0, rng=random.Random(20261120)
    )
    check("Scoring run generates five ordered waypoints",
          sample_run is not None and len(sample_run.points_xy) == 5
          and len(sample_run.legs) == 4)
    if sample_run:
        check("Scoring-run waypoints all lie on roads",
              all(cmap.nearest_projection(*p).dist < 1e-6
                  for p in sample_run.points_xy))
        path_len = sum(
            math.dist(a, b) for a, b in zip(sample_run.path, sample_run.path[1:])
        )
        check("Scoring-run distance matches path length",
              abs(path_len - sample_run.total_m) < 1e-6,
              f"{path_len:.2f} m vs {sample_run.total_m:.2f} m")

    sample_avoid = cmap.plan_point_run(
        5, min_sep_m=40.0, reduce_backtrack=True,
        rng=random.Random(20261120),
    )
    check("Scoring run (reduce backtracking) can be generated",
          sample_avoid is not None and len(sample_avoid.legs) == 4)

    # 9) Daily cap
    if sample_run is not None:
        pts5 = sample_run.points_lonlat
        r8 = cmap.plan_point_run(points=pts5, run_limit=True, daily_cap_m=8000.0)
        check("Daily cap (male 8 km) is generated",
              r8 is not None and r8.limit_state == "track")
        if r8 is not None:
            plen8 = sum(math.dist(a, b) for a, b in zip(r8.path, r8.path[1:]))
            check("Daily-cap total reaches 8 km",
                  abs(r8.total_m - 8000.0) < 1.0
                  and abs(plen8 - r8.total_m) < 0.2,
                  f"{r8.total_m:.1f} m")
            check("Daily cap includes track laps",
                  r8.track_m > 0.0 and r8.track_entry_xy is not None,
                  f"track {r8.track_m:.0f} m")
        r5 = cmap.plan_point_run(points=pts5, run_limit=True, daily_cap_m=5000.0)
        check("Daily cap (female 5 km) reaches 5 km",
              r5 is not None and abs(r5.total_m - 5000.0) < 1.0,
              f"{r5.total_m:.1f} m" if r5 else "no solution")
        over = cmap.plan_point_run(points=pts5, run_limit=True, daily_cap_m=100.0)
        check("No extension when checkpoint legs already exceed the cap",
              over is not None and over.limit_state == "over"
              and over.track_m == 0.0
              and abs(over.total_m - sample_run.total_m) < 1e-6)
        mid = cmap.plan_point_run(
            points=pts5, run_limit=True,
            daily_cap_m=sample_run.total_m + 1.0,
        )
        check("Cap reached on the way to the track",
              mid is not None and mid.limit_state == "transit"
              and mid.track_m == 0.0
              and abs(mid.total_m - (sample_run.total_m + 1.0)) < 1.0,
              f"{mid.total_m:.1f} m" if mid else "no solution")

    # 10) Immersive simulation: choose a start, then reveal checkpoints one by one.
    start_xy = proj.xy(113.41517874, 22.47824096)
    immersive = ImmersiveSession(
        cmap, n=5, min_sep_m=40.0, rng=random.Random(20261120)
    )
    ir = immersive.start(start_xy)
    check("Immersive run starts with a connector",
          ir is not None and ir.kind == "immersive"
          and len(ir.points_xy) == 1 and ir.legs[0].ac_m > 0.0,
          f"connector {ir.legs[0].ac_m:.1f} m" if ir else "no solution")
    if ir is not None:
        preview = immersive.full_snapshot()
        check("Immersive preview plans all five checkpoints",
              preview is not None and len(preview.points_xy) == 5
              and len(preview.legs) == 5 and not preview.finished
              and len(ir.points_xy) == 1 and preview.total_m > ir.total_m,
              f"{preview.total_m:.0f} m" if preview else "no solution")
        counts = [len(ir.points_xy)]
        for _ in range(4):
            ir = immersive.advance()
            if ir is None:
                break
            counts.append(len(ir.points_xy))
        check("Immersive run reveals checkpoints one by one",
              counts == [1, 2, 3, 4, 5], "->".join(map(str, counts)))
        if ir is not None:
            ir = immersive.advance()
        check("Immersive run finishes after five points",
              ir is not None and ir.finished and ir.completed == 5)
        if ir is not None:
            plen_i = sum(
                math.dist(a, b) for a, b in zip(ir.path, ir.path[1:])
            )
            check("Immersive path length = cumulative distance",
                  abs(plen_i - ir.total_m) < 1e-5,
                  f"{plen_i:.2f} m vs {ir.total_m:.2f} m")

    cap_immersive = ImmersiveSession(
        cmap, n=5, min_sep_m=40.0, run_limit=True,
        daily_cap_m=8000.0, rng=random.Random(20261120),
    )
    cir = cap_immersive.start(start_xy)
    if cir is not None:
        for _ in range(5):
            cir = cap_immersive.advance()
            if cir is None:
                break
    check("Immersive run can continue to the daily cap",
          cir is not None and cir.finished and cir.limit_state == "track"
          and abs(cir.total_m - 8000.0) < 1.0,
          f"{cir.total_m:.0f} m" if cir else "no solution")
    cap_preview = cap_immersive.full_snapshot()
    check("Immersive preview includes the track cap",
          cap_preview is not None and cap_preview.limit_state == "track"
          and abs(cap_preview.total_m - 8000.0) < 1.0
          and cap_preview.track_m > 0.0 and not cap_preview.finished,
          f"{cap_preview.total_m:.0f} m" if cap_preview else "no solution")
    guide = cmap.track_lane_guide()
    check("Running track has a lane guide",
          guide is not None and len(guide[0]) >= 3 and guide[1] is not None,
          f"{len(guide[0])} ring points" if guide else "no track")

    # 11) Only option: backtracking exclusion must fall back automatically.
    deg = [0] * len(cmap.coords)
    for e in cmap.edges:
        deg[e.u] += 1
        deg[e.v] += 1
    gate = next((n for n, d in enumerate(deg) if d == 1), None)
    if gate is not None:
        edge = next(e for e in cmap.edges if e.u == gate or e.v == gate)
        nb = edge.v if edge.u == gate else edge.u
        r_in = cmap.shortest_route(cmap.coords[nb], cmap.coords[gate])
        check("Only-option fallback is solvable",
              r_in is not None and r_in.last_dir is not None)
        if r_in is not None and r_in.last_dir is not None:
            r_out = cmap.shortest_route(
                cmap.coords[gate], cmap.coords[nb],
                avoid_dirs={r_in.last_dir},
            )
            check("Only-option case falls back automatically",
                  r_out is not None and r_out.avoid_fallback)

    print()
    for cond, line in results:
        print(line)
    failed = sum(1 for cond, _ in results if not cond)
    print()
    print(f"Self-check: {len(results) - failed}/{len(results)} passed")
    return failed == 0
