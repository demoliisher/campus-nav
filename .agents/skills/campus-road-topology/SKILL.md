---
name: campus-road-topology
description: Use when building, repairing, or extending the topology of a campus road GeoJSON for routing (campus-nav and similar apps) — orthogonalizing hand-traced roads, making T-junctions share one node, extending/inserting connectors, ring↔line conversions, merging duplicate roads, adding gates/POIs, splitting roads, and auditing connectivity before delivery.
metadata:
  version: "1.0.0"
  date: "2026-11-21"
---

# Campus road topology

Turn a hand-traced GeoJSON that merely *looks* like a map into a routable graph:
every junction is one shared node, and the whole network is a single connected component.

## When to use

- A hand-traced or scraped road network is about to feed a routing algorithm, and the
  dangling ends, oblique joins, and duplicated segments must be cleaned up first.
- You need to add a road or a gate, reroute a road, or turn an enclosed area into an
  open path.
- After any change you must prove "the graph is still connected" and hand over a
  reviewable change list.

## Three invariants

1. **The source trace is read-only; edit a derived file.** Never overwrite the
   hand-traced file. All edits happen in place in a working copy such as
   `*_orthogonal.geojson` ("orthogonalized"). Back it up before each edit to
   `backup/<name>_<timestamp>.geojson`.
2. **Node identity means identical coordinate strings.** Two lines "meet" only when
   they carry the same `[lon, lat]` list (8 decimals). Assert `a == b` in code;
   never accept a distance threshold as proof of a connection. Thresholds are for
   *finding* candidates, not for *deciding* connectivity.
3. **Every tolerance is in metres.** Degree deltas have no intuition: at this latitude
   1° of latitude ≈ 110.7 km, so `1e-5°` ≈ 1.1 m. A tolerance of `2.0` in degrees is a
   200 km error.

## Fixed local frame

Project through the same origin every time, or the frame drifts as soon as the vertex
count changes and every historical coordinate becomes meaningless:

```python
lon0, lat0 = 113.40877964, 22.47782802      # campus centre, hard-coded
MX, MY = 102912.608, 110736.765             # metres per degree at this latitude
x = (lon - lon0) * MX
y = (lat - lat0) * MY
```

Do every distance, foot, ray, and intersection in this metric plane, then write back
with `round(lon0 + x/MX, 8)`.

## Main loop: audit → classify → apply → verify → report

### 1. Audit

For each open endpoint (two per line), look for the three kinds of landing on every
other feature:

- a target vertex;
- a perpendicular foot on a target segment (`t` clamped to `[0, 1]`);
- the intersection of the endpoint's own forward ray with the target (is this road
  aimed straight at that one?).

Keep the smallest gap and record `gap / perpendicular distance / extension length /
angle`. At the same time scan for three hidden problems:

- **Crossings without a node** — two lines cross, but the crossing is not a vertex of
  either (insert a node on both).
- **Vertex on line** — a vertex sits on another line that has no node there.
- **Duplicated road** — two lines overlap collinearly for several metres (the same
  road drawn twice). This is the most insidious defect in a routing graph.

The audit threshold (e.g. 15 m) is a *reporting* threshold, not a correctness one;
correctness is established by the graph check in step 4.

### 2. Classify

| Class | Test | Action |
|---|---|---|
| done | gap ≈ 0 (same coordinate) | leave it |
| snap | endpoint within ~2 m of a target vertex | set the endpoint to that vertex |
| insert | endpoint sits on the target line, last segment already perpendicular | insert the foot node into the target |
| extend | ray hits the target within ~15 m at ≈90° | extend the endpoint, insert the node |
| link | none of the above | merge into a corner or add a short connector (needs judgement) |

Prefer the smallest change that does not create a sliver: `done` over `snap` over
`extend`.

### 3. Apply

**T-junction recipe** (the common case): move the branch endpoint along one axis only
so it lands on the target's meridian/parallel → find the right segment in the target
and insert the same coordinate → assert string equality on both sides and a 90.000°
angle.

**Sliver rule**: when the intersection is within ~0.15–0.6 m of an existing vertex,
**move that vertex** instead of inserting a new one; merge endpoints that are within
~2 m of a corner. Print the shortest edge after every change and go back if centimetre
edges appear.

**Ring → line**: check `first == last` first, then choose the edge to cut. The two cut
corners usually have to become the new endpoints of the replacement line, or they are
left dangling. After a polygon becomes a line, drop the area semantics (`traverse`,
`@circle`) — they no longer apply.

**Duplicate roads on one meridian**: if one line's edge coincides with another line's
edge (e.g. a building perimeter edge and the avenue of the same longitude), **merge**
that edge into the avenue and re-emit the rest as an open line (a U shape). Never keep
two overlapping edges.

**Normalize a chain to one latitude**: take the authoritative value (e.g. the
roundabout's `@circle.center`), move every node on the chain in one pass — line
endpoints, inserted feet, and any polygon vertex that shares those coordinates — then
assert the line is horizontal, the end segments are still vertical, and no node is left
at the old value.

**Split a road**: cut at an existing vertex; both halves share it; keep the original
feature for one half (usually the west/south one) and append a new feature for the
other; rename both; check that the point counts and lengths add up to the original.

### 4. Verify (all five)

1. **No pending candidates** — all `done`, or only deliberately free ends (gates).
2. **Zero crossings, zero vertex-on-line.**
3. **One connected component** — union-find over "adjacent vertices within a feature +
   ring closure + **exactly coincident vertices across features** + junction nodes".
   The coincident-vertex union must not depend on the candidate list: when a road is
   extended across another, that crossing is no longer an open endpoint and produces no
   candidate row.
4. **No collinear duplicate segments** (>3 m long, offset <0.3 m).
5. **No accidental slivers** (record the shortest edge in the file; apart from sampled
   circles it should not drop below ~0.5 m).

### 5. Report

- Compute counts from the data (`Counter(op)`); never hard-code "19 places".
- Write each change as "old → new (moved X m)" with node coordinates.
- Use fixed windows per area for figures; put feature labels at the longest edge's
  midpoint so they do not cover node annotations.
- Historical statements ("the west end is still 0.93 m short") go stale the moment the
  topology changes; update them in the same pass.

## Recipe index

| Situation | Approach |
|---|---|
| Oblique straight runs | snap only when length ≥30 m, deviation ≤1.2 m, azimuth within 2° of an axis; keep connectors that deliberately aim at a corner |
| Dangling end onto an avenue | extend → insert node in the avenue → assert 90° |
| Two roads pointing at one corner | merge into one junction: set both ends to the corner coordinate (or insert the same node in each) |
| Plaza / running track | plaza `traverse:"area"` (freely traversable); track as a stadium shape with `traverse:"ring"` |
| Gates / POIs | if the gate lies on an existing road's extension, extend that road (so the name stays true); otherwise add a short connector. A gate must be a graph node |
| Roundabout connections | "ray from the centre": intersect the ray with the actual ring polyline; if the hit is <0.15 m from a ring vertex, move that vertex to avoid centimetre slivers |
| A road with two meanings | split at the V point / junction, keep the original feature for one part, append and rename the other |

## Engineering habits (all learned the hard way)

- **One script per step** — `orthogonalize.py`, `connect_f22.py`, `split_f18.py`…
  Each reads the current file, edits only its target features, prints before/after, and
  calls `json.dump` only after the assertions pass.
- **Make scripts idempotent** — detect "already applied" and switch to redraw-only mode;
  otherwise a second run appends duplicate features or trips an assertion.
- **Never re-run a full rebuild pipeline after manual edits** — it rebuilds from the
  source trace and silently reverts hand-moved vertices and renamed `desc` fields. Put a
  warning at the top of the pipeline and write incremental edits as one-off scripts.
- **Analyse the same file you measured on** — candidate coordinates are measured on the
  working copy, so the verifier must read that copy too, or segment indices shift
  (IndexError or false positives).
- **Windows/Python** — use `python -X utf8`; avoid `-I` (it drops site-packages and the
  encoding setup); run batches through a `set -euo pipefail` runner, not `cmd | tail`
  (the pipe swallows the exit code).
- **Indices drift** — after splitting, appending, or deleting features, update every
  `F##` reference in reports and scripts; never assume the numbering is stable.

## Hand-off to campus-nav

1. Copy the working file to `campus-nav/src/campus_nav/data/map_wgs84.geojson`
   (source is WGS-84).
2. `cd campus-nav && uv run python scripts/build_gcj02_data.py` to produce
   `map_gcj02.geojson`.
3. Attribute contract: `desc` (display name); `traverse` = `area` (freely traversable
   interior) / `ring` (boundary only) / `fence` (display only); one-way rings add
   `run_direction: ccw/cw`.
4. Run `uv run campus-nav --check` and `uv run pytest` and confirm the 49 self-checks
   still pass.

## References

- [Audit and verification in full](references/audit-and-verify.md) — candidate classes,
  union-find, extra scans, acceptance order.
- [Python recipes](references/python-recipes.md) — projection, foot, ray intersection,
  node insertion, vertex moves, union-find, assertion checklist.
- [Case study: GDPU ZS](references/case-gdpu-zs.md) — the method
  applied end to end, with real numbers.
