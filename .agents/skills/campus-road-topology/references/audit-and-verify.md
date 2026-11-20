# Audit and verification in full

This expands steps 1 and 4 of the main loop in [SKILL.md](../SKILL.md): how to find
every place that *should* be joined but is not, and how to prove the network is still a
single connected graph afterwards.

## 0. Read the shape of the data first

- Only two geometry types matter: `LineString` (road) and `Polygon` (ring/area). A ring
  must have `ring[0] == ring[-1]`; if it does not, it is not a ring, just an open line
  whose ends happen to be close.
- Open endpoints = 2 × number of lines. Rings have none. Compute this number up front and
  use it for every "all endpoints are connected" claim (note: once gates are added, the
  gate ends make "endpoint count ≠ connected count" — do not conflate them).
- `properties` usually carry `desc` (name) and `traverse` (`area` = freely traversable,
  `ring` = boundary only). When a polygon becomes a line, revisit those area semantics.

## 1. Endpoint candidates

For every open endpoint A of every line (`start` or `end`):

```text
u = unit vector from A's neighbour to A   (the direction the road "points" outward)

for every other feature B:
  for every vertex V of B:
      candidate(vertex, gap = |A − V|)
  for every segment S = (P, Q) of B:
      t = clamp(((A − P)·(Q − P)) / |Q − P|², 0, 1)
      F = P + t(Q − P)
      candidate(foot, gap = |A − F|, t)
      intersect the ray A + s·u with S:
          candidate(ray, gap = s, angle)
keep the candidate with the smallest gap
```

Classes (default thresholds; tune to the trace's accuracy):

| Condition | Class | Meaning |
|---|---|---|
| gap < 0.01 m | done | already the same node (identical coordinate string) |
| target is a vertex and gap ≤ 2 m | snap | practically the same point; just rename it |
| target is a foot, A's last segment already perpendicular, 0 < t < 1 | insert | lies on the line; insert a foot node |
| ray hit, extension ≤ 15 m, angle ≈ 90° | extend | aimed at the target; extend and insert |
| anything else | link | needs judgement: merge into a corner or add a short connector |

A report may list all candidates within 15 m, but **apply only the best one**. The
threshold answers "is it worth fixing", not "is it connected" — connectivity is decided
by the graph in section 4.

## 2. Crossings and vertex-on-line

- **Crossing without a node**: two segments properly cross (the intersection is interior
  to both) and the point is not a vertex of either → insert a node on both lines, with
  the same coordinate.
- **Vertex on line**: a vertex lies on another line's segment (offset < 0.01 m) but that
  line has no node there → insert a node, or move the vertex away.

Neither appears in the endpoint-candidate table, so both must be scanned separately.
Skipping them produces maps that *look* connected but are disconnected in the graph.

## 3. Duplicated road (the most insidious)

When one stretch of road is drawn twice, every endpoint candidate is `done`, connectivity
may be 1, yet the graph carries an extra overlapping edge — which corrupts routing,
highlighting, and distance totals.

```text
for every pair of segments A, B from different features:
    if both endpoints of B are < 0.3 m from line A
    and both endpoints of A are < 0.3 m from line B
    and the overlap is longer than 3 m:
        classify as duplicate
```

Fix: merge that edge into one feature (usually the higher-class avenue) and re-emit the
remainder of the other feature as an open line. Do not simply delete "the shorter one" —
it may carry other connections.

## 4. The connectivity graph (the only correctness criterion)

```text
nodes: every vertex of every feature  ("v", feature, vertex)
       rings drop the duplicated closing vertex
edges: consecutive vertices; rings add the closing edge

union-find:
  1) adjacent vertices within a feature
  2) ring first/last
  3) exactly coincident vertices across features     ← critical, and independent of
                                                        the candidate list
  4) for every insert/extend candidate, create a synthetic node ("s", target, seg, t),
     union it with both segment ends, then with endpoint A
  5) for every crossing, create ("x", ...), union with both segments' ends, then the
     two synthetic nodes with each other
```

Rule 3 is the one that is easy to miss: after a road is extended across another, that
intersection is no longer an open endpoint for either line, so the candidate table has no
row for it. A verifier that only unions what the candidates say will report a connected
map as two components.

Output: number of components, and the node count plus feature list of the largest. Expect
**exactly one component covering every feature**.

## 5. Extra scans

Run these four after every change:

- shortest edge per feature — anything below 0.5 m outside sampled circles deserves a look;
- collinear duplicates (section 3);
- ring closure (`first == last`) and type counts (how many lines, how many rings);
- duplicate or empty feature names (easy to miss when renaming).

## 6. Reporting

- Count from data: `Counter(op for op in junctions)`; never type a number by hand.
- State each change as "old coordinate → new coordinate (moved X m)", with 8-decimal
  node coordinates.
- Group figures by area (north/west/centre/east/south), each with a fixed window; put
  labels at the longest edge's midpoint so they do not cover node annotations.
- Keep prose in sync: once the topology changes, sentences like "0.93 m still missing at
  the west end" are stale and must be rewritten in the same pass.

## 7. An acceptance order worth copying

1. `analyze.py` prints: feature count, endpoint count, candidate count, crossings,
   vertex-on-line.
2. Every candidate is `done` (or only the deliberately free gate ends remain).
3. `verify_connectivity.py` prints one component.
4. Extra scans: shortest edge, duplicates, ring closure.
5. Diff against the backup feature by feature: only the intended features changed; all
   others are byte-for-byte identical.
6. Regenerate the report and figures and check that the numbers match the data.
