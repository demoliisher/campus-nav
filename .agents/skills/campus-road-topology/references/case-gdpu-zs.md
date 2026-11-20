# Case study: GDPU ZS

A real hand-traced basemap turned into a routed graph. The numbers come from the
project's own reports and give a sense of scale for similar jobs.

## Starting point

- `findroad/campus-roads.geojson`: 24 hand-traced features (19 lines + 5 rings).
- Problems: oblique segments (tracing error), dangling ends, two lines pointing at the
  same corner, the plaza drawn as an irregular polygon, the lake as a closed ring, a dorm
  block wrapped in a rectangle (inconsistent with every other building), and no gates.
- Consumer: `campus-nav` (Python + tkinter) — two-point Dijkstra routing, pace playback,
  check-in simulation.

## Coordinate frame

```python
lon0, lat0 = 113.40877964, 22.47782802     # campus centre
MX, MY = 102912.608, 110736.765
```

The fixed origin mattered: the feature count went from 24 to 27 and the vertex count
changed several times, yet no coordinate drifted.

## Main operations and figures

| Change | Result |
|---|---|
| Orthogonalize straight runs (39 vertices) | max displacement 2.10 m; the four connectors that deliberately aim at the track's corners were left slanted |
| Rebuild the running track as a stadium | 77-point ring, `traverse:"ring"` |
| Align the north chain to 22.481074 | F03 / plaza north edge / F20 / dorm north line on one parallel; four shared nodes |
| Split the dorm's north block | north side merged into F14 (325.68 m); east side became F13 (south end extended 5.65 m onto F03) |
| Plaza → semicircle | R = 26.86 m, arc 84.38 m, 28-point ring; the due-south apex is a real node where the central avenue starts |
| Reroute the central avenue | from the arc apex due south into the north lake, 304.13 m |
| Shift the east avenue 4.68 m east | same meridian as the dorm's west side, 113.410908; lake NE corner moved 6.79 m, p13 6.38 m |
| North lake ring → line | F06 466.40 m; the two cut ends re-attached by perpendicular feet (east 1.65 m / west 2.47 m from the deleted corners) |
| Roundabout-to-library road | F23 199.86 m, due east 0.000° |
| South lake tied in | ends moved 3.85 / 5.88 m; F24 266.42 → 277.09 m |
| Four mid-block roads onto the avenues | F05/F08 192.01 m each, F21/F22 109.09 m each; seven T-junctions all at 90.000° |
| Dorm block ring → line | west edge merged into the east avenue (F16 226.24 → 330.99 m); the rest became the 296.38 m "east arc" |
| Three gates | all on 113.4064864: F03 extended 153.94 m (319.09 m), west gate 156.28 m, south gate 390.42 m |
| Normalize the roundabout's two sides | F23 and both lake feet 22.476806 → 22.476805 (0.11 m); the ring's east sample point moved with them |
| Split the teaching-building road | 718.06 m → 226.96 m (teaching/experiment building spine) + 491.10 m (library back-hill path) |
| Radial connections into the roundabout | F18 north end (113.4082097, 22.47661502), F15 south end (113.40801889, 22.4768747); ring vertices moved 0.031 / 0.035 m |

Final state: **27 features (24 lines + 3 rings) · 45 connectable endpoints all `done` +
3 free gate ends · 0 crossings · 1 component (316 vertices)**.

## What this case taught us

1. **Manual edits happen between sessions.** The user hand-renamed two `desc` fields and
   moved one vertex (the south V point, by 15.7 m). Found by diffing the current file
   against the source trace: orthogonalization moves at most 2.10 m, so anything beyond
   3 m is either a manual edit or a different pipeline step. Consequence: never re-run the
   full pipeline afterwards; every later change became a one-off "read current state,
   touch only the target features" script, and the pipeline got a warning header.
2. **Feature numbers are not stable, and names can lie.** In the source file
   the `desc` "Campus Road - Teaching Building to Library Rear" is actually the
   south-lake arc; today's F18 is a different line. Matching by `desc` picked the
   wrong feature — match by geometry and point count.
3. **A sampled circle can be 0.11 m off its own centre latitude.** The roundabout's east
   and west sample points differ by 0.11 m in latitude (rounding). Normalizing both sides
   to the centre latitude required moving that ring sample point too, or the shared node
   would have split.
4. **Two lines on one meridian are the same road.** The dorm perimeter's west edge
   coincided with the east avenue; merging it let F16 run all the way to the north-west
   corner, with F21's T-junction becoming one of its vertices — one overlapping edge gone.
5. **Split a road at a semantically meaningful point.** The cut went at the V point where
   the south-gate road arrives, so two road halves plus the gate road share one node — more
   meaningful than splitting at an arbitrary midpoint.
6. **Gates are nodes too.** The three gate roads give "a check-in point at the gate" a
   place to attach; gate ends are legitimately free endpoints and must be reported as
   such, not counted as "unconnected".

## Files and commands

```text
findroad/
├── campus-roads.geojson            # source trace, read-only
├── campus-roads-orthogonal.geojson # working copy (the only map that gets edited)
└── analysis/
    ├── run_pipeline.sh                # 16-step full rebuild (disabled after manual edits)
    ├── orthogonal_tees.py             # four mid-block roads onto the avenues
    ├── dorm_ring_to_line.py           # dorm block ring → line (idempotent)
    ├── add_gates.py                   # three gates (idempotent)
    ├── align_roundabout_lat.py        # normalize both sides of the roundabout
    ├── split_f18.py                   # split the teaching-building road
    ├── ray_into_roundabout.py         # radial connections
    ├── analyze.py                     # endpoint audit + area figures
    ├── verify_connectivity.py         # union-find connectivity
    └── make_report.py                 # report.md
```

Hand-off to the app:

```bash
cp findroad/campus-roads-orthogonal.geojson \
   campus-nav/src/campus_nav/data/map_wgs84.geojson
cd campus-nav
uv run python scripts/build_gcj02_data.py
uv run campus-nav --check
uv run pytest
```

Attributes: `desc` is the display name; `traverse` is `area` (plaza) or `ring` (track,
roundabout). Gates are not separate Point features — they are the free ends of three
gate roads (the west-secondary gate is F03's west endpoint; the west and south gates are
each one short line).
