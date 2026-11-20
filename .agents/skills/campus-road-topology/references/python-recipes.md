# Python recipes

Snippets verified on Windows + Python 3.10+. Run everything with
`python -X utf8 script.py`; **do not** use `python -I` (it drops site-packages and
`PYTHONIOENCODING`).

## Fixed projection frame

```python
import math
import numpy as np

lon0, lat0 = 113.40877964, 22.47782802      # hard-coded for the whole project
MX, MY = 102912.608, 110736.765             # metres per degree

def P(c):
    return np.array([(c[0] - lon0) * MX, (c[1] - lat0) * MY])

def LL(q):
    """metric point -> [lon, lat] at the file's 8-decimal precision"""
    return [round(lon0 + q[0] / MX, 8), round(lat0 + q[1] / MY, 8)]

def L(a, b):
    return math.hypot((a[0] - b[0]) * MX, (a[1] - b[1]) * MY)

def plen(pts):
    return sum(L(pts[k], pts[k + 1]) for k in range(len(pts) - 1))
```

## Perpendicular foot (clamped)

```python
def foot(pt, a, b):
    a, b, p = P(a), P(b), P(pt)
    ab = b - a
    den = float(ab @ ab)
    t = 0.0 if den == 0 else float((p - a) @ ab) / den
    t = max(0.0, min(1.0, t))
    return LL(a + t * ab), t
```

`t == 0` or `1` means the landing is a vertex — prefer `snap` over `insert` then.

## Ray vs segment

```python
def ray_hit(C, d, a, b):
    """Intersect the ray C + s·d with segment ab.
       Returns (metric point, s, u) or None; s > 0 is forward, 0 <= u <= 1 inside."""
    d = d / np.linalg.norm(d)
    e = P(b) - P(a)
    den = d[0] * e[1] - d[1] * e[0]
    if abs(den) < 1e-12:
        return None
    ac = P(a) - C
    s = (ac[0] * e[1] - ac[1] * e[0]) / den
    u = (ac[0] * d[1] - ac[1] * d[0]) / den
    if s <= 0 or not (0 <= u <= 1):
        return None
    return C + s * d, s, u


def ray_ring(ring, C, d):
    """Nearest forward hit on a closed ring (for 'ray from the centre' connections)."""
    best = None
    for i in range(len(ring) - 1):
        hit = ray_hit(C, d, ring[i], ring[i + 1])
        if hit and (best is None or hit[1] < best[1]):
            best = (hit[0], i, hit[2])
    pt, i, u = best
    return pt, i, u
```

## Insert a node on a line, in order

```python
def insert_node(coords, pt):
    """Insert pt into coords (in place); returns the new index."""
    best = None
    nseg = len(coords) - 1           # recompute on every call — see the note below
    for i in range(nseg):
        f, t = foot(pt, coords[i], coords[i + 1])
        d = L(f, pt)
        if best is None or d < best[0]:
            best = (d, t, i)
    d, t, i = best
    assert d < 1e-6, "point is not on the line (off by %.3f m)" % d
    assert 1e-6 < t < 1 - 1e-6, "point lands on a vertex; move the vertex instead"
    coords.insert(i + 1, list(pt))
    return i + 1
```

> Trap: when inserting several points in a row, `nseg` must be recomputed *inside* the
> loop. Hoisting it means the second point is only searched for in the old first
> segment, and the assertion fails by hundreds of metres.

## Move a vertex (watch the ring closure)

```python
def move_vertex(ring_or_line, k, pt, closed=False):
    ring_or_line[k] = list(pt)
    if closed and k == 0:
        ring_or_line[-1] = list(pt)      # moving the first point moves the closing copy
```

## Sliver avoidance: move, do not insert

```python
def snap_ring_vertex(ring, pt, tol=0.15):
    k = min(range(len(ring)), key=lambda j: np.hypot(*(P(ring[j]) - pt)))
    if k == len(ring) - 1:               # closing duplicate; edit the first point
        k = 0
    d = float(np.hypot(*(P(ring[k]) - pt)))
    assert d < tol, "hit is %.2f m from a ring vertex, too far to snap" % d
    new = LL(pt)
    ring[k] = list(new)
    if k == 0:
        ring[-1] = list(new)
    return new, d
```

Rule of thumb: move an existing vertex when the hit is within 0.15–0.6 m; otherwise
insert. Never leave a centimetre edge behind.

## Move an endpoint along one axis only

```python
def set_endpoint_lon(coords, k, x, neighbour_index):
    """Change only the longitude, keeping the edge horizontal/vertical."""
    assert abs(coords[k][1] - coords[neighbour_index][1]) < 1e-12, "edge is not horizontal"
    coords[k][0] = x
```

Assert immediately afterwards:

```python
assert coords[0][1] == coords[1][1]          # horizontal
assert coords[0][0] == coords[-1][0]         # vertical
```

## Union-find connectivity check

```python
par = {}
def find(x):
    par.setdefault(x, x)
    while par[x] != x:
        par[x] = par[par[x]]
        x = par[x]
    return x
def union(a, b):
    ra, rb = find(a), find(b)
    if ra != rb:
        par[ra] = rb

# 1) vertices of every feature
for i, f in enumerate(features, 1):
    pts = f["geometry"]["coordinates"] if f["geometry"]["type"] == "LineString" \
          else f["geometry"]["coordinates"][0]
    closed = f["geometry"]["type"] == "Polygon"
    if closed and pts[0] == pts[-1]:
        pts = pts[:-1]
    for k in range(len(pts)):
        find(("v", i, k))
    for k in range(len(pts) - 1):
        union(("v", i, k), ("v", i, k + 1))
    if closed:
        union(("v", i, len(pts) - 1), ("v", i, 0))

# 2) exactly coincident vertices across features (must not depend on the candidate list)
by_xy = {}
for i, f in enumerate(features, 1):
    pts = ...
    for k, q in enumerate(pts):
        by_xy.setdefault((round(q[0], 8), round(q[1], 8)), []).append(("v", i, k))
for keys in by_xy.values():
    for other in keys[1:]:
        union(keys[0], other)
```

> Do not skip block 2. Once a road has been extended across another, the crossing is no
> longer an open endpoint, so no candidate row exists for it; a candidate-only union
> would split one connected graph into two.

## Assertion checklist (print on every run)

```python
print("shared node          :", a_end == b_vertex)      # list equality, not a distance
print("edge horizontal      :", a[1] == b[1])
print("junction angle       : %.3f deg" % angle)
print("shortest edge        : %.2f m" % min_edge)
print("features/lines/rings :", len(T), n_line, n_poly)
print("components           :", len(components))
```

## File I/O

```python
raw = json.load(open(PATH, encoding="utf-8"))
... edit only the target features ...
assert all_checks_pass
json.dump(raw, open(PATH, "w", encoding="utf-8"), ensure_ascii=False)
```

- **Assertions before the dump** — if the script dies halfway, the file on disk is
  untouched.
- Back up first: `backup/<name>_<YYYYmmdd_HHMMSS>.geojson`.
- One script per step, reading the current state and editing only its targets; scripts
  that may run twice need an "already applied → redraw only" branch.

## Figures

```python
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False
ax.set_aspect("equal")
```

- Put legends outside the axes (`bbox_to_anchor=(0.5, -0.10)`); otherwise they cover the
  corner nodes.
- Place feature labels at the longest edge's midpoint; if a label would cover a node
  annotation, move it.
- Area detail figures use fixed windows; widen them when a new feature falls outside.
