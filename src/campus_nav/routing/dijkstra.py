# -*- coding: utf-8 -*-
"""Dijkstra shortest path with one-way edge restrictions."""

from __future__ import annotations

import heapq
import math


def dijkstra(adj, n: int, src: int, dst: int, forbidden=None):
    """Return (distance, node sequence, edge id sequence, directed edges, last edge).

    ``forbidden`` is a set of directed edges {(eid, forward)} whose reverse
    must be avoided (reduce-backtracking passes the last edge of the previous
    leg). If the reverse is requested, i.e. (eid, not forward) is in
    ``forbidden``, that direction is skipped.
    """
    dist = [math.inf] * n
    prev = [-1] * n
    prev_edge = [-1] * n
    prev_dir: list[bool | None] = [None] * n
    dist[src] = 0.0
    pq = [(0.0, src)]
    while pq:
        d, u = heapq.heappop(pq)
        if d > dist[u] + 1e-12:
            continue
        if u == dst:
            break
        for v, w, eid, fwd in adj[u]:
            if (forbidden and fwd is not None
                    and (eid, not fwd) in forbidden):
                continue
            nd = d + w
            if nd < dist[v] - 1e-12:
                dist[v] = nd
                prev[v] = u
                prev_edge[v] = eid
                prev_dir[v] = fwd
                heapq.heappush(pq, (nd, v))
    if not math.isfinite(dist[dst]):
        return None
    path = [dst]
    used = []
    used_dirs: list[tuple[int, bool]] = []
    while path[-1] != src:
        step = path[-1]
        p = prev[step]
        if p < 0:
            return None
        eid = prev_edge[step]
        used.append(eid)
        fwd = prev_dir[step]
        if fwd is not None:
            used_dirs.append((eid, fwd))
        path.append(p)
    path.reverse()
    used.reverse()
    used_dirs.reverse()
    # If the last step is an in-area query edge (fwd=None), do not treat an
    # earlier segment as the one just traversed.
    last_dir = None
    if prev_edge[dst] >= 0 and prev_dir[dst] is not None:
        last_dir = (prev_edge[dst], prev_dir[dst])
    return dist[dst], path, used, frozenset(used_dirs), last_dir
