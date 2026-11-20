# -*- coding: utf-8 -*-
"""Canvas rendering and viewport transforms.

Used as a mixin; at runtime it relies on the canvas, cmap, viewport state
(scale/ox/oy), and helpers such as px()/pxf() provided by CampusNavApp.
"""

from __future__ import annotations

import math
import tkinter as tk

from ..formatting import fmt_len
from ..geo.paths import polyline_midpoint
from ..routing.models import PointRun, RouteResult
from .theme import (
    AREA_COLORS,
    C_A,
    C_AREA_ACTIVE,
    C_AREA_ACTIVE_LINE,
    C_B,
    C_BG,
    C_FENCE,
    C_FENCE_FILL,
    C_PACE,
    C_ROAD,
    C_ROAD_CASING,
    C_ROUTE,
    C_TEMP,
    FONT_MONO,
    FONT_UI,
)


class MapDrawingMixin:
    """Map viewport and all canvas drawing logic."""

    # ---- View ------------------------------------------------------------
    def _fit_view(self) -> None:
        w = max(self.canvas.winfo_width(), 2)
        h = max(self.canvas.winfo_height(), 2)
        minx, miny, maxx, maxy = self._view_bounds()
        dx = max(maxx - minx, 1.0)
        dy = max(maxy - miny, 1.0)
        pad = self.px(32)
        scale = min((w - 2 * pad) / dx, (h - 2 * pad) / dy)
        scale = max(scale, 0.05)
        self.scale = scale
        self._fit_scale = scale
        self.ox = (w - dx * scale) / 2.0 - minx * scale
        self.oy = (h - dy * scale) / 2.0 + maxy * scale
        self._view_locked = False
        self._redraw()

    def _view_bounds(self) -> tuple[float, float, float, float]:
        """Combined bounds of roads and fences, so the initial view shows the whole fence."""
        minx, miny, maxx, maxy = self.cmap.bounds
        for fence in self.fences:
            for ring in fence.rings:
                for lon, lat in ring:
                    x, y = self.cmap.proj.xy(lon, lat)
                    minx = min(minx, x)
                    miny = min(miny, y)
                    maxx = max(maxx, x)
                    maxy = max(maxy, y)
        return minx, miny, maxx, maxy

    def _world_at(self, px: float, py: float) -> tuple[float, float]:
        return ((px - self.ox) / self.scale, (self.oy - py) / self.scale)

    def _s(self, p: tuple[float, float]) -> tuple[float, float]:
        return (self.ox + p[0] * self.scale, self.oy - p[1] * self.scale)

    def _flat(self, pts: list[tuple[float, float]]) -> list[float]:
        out: list[float] = []
        for x, y in pts:
            out.extend((self.ox + x * self.scale, self.oy - y * self.scale))
        return out

    # ---- Drawing ---------------------------------------------------------
    def _redraw(self) -> None:
        if self.scale <= 0.0:
            return
        c = self.canvas
        c.delete("all")
        self._draw_fences()
        self._draw_areas()
        self._draw_roads()
        if self.show_labels.get():
            self._draw_road_labels()
        self._draw_fence_labels()
        if self.mode == "point":
            if self.result:
                self._draw_route(self.result)
            self._draw_points()
        else:
            if self.run:
                if self.run.kind == "immersive":
                    self._draw_immersive(self.run)
                else:
                    self._draw_run(self.run)
        self._draw_pace_marker()
        self._draw_scalebar()
        self._draw_north()

    def _draw_fences(self) -> None:
        """Fence is the lowest map layer: pale fill plus a green dashed boundary."""
        c = self.canvas
        for fence in self.fences:
            for ring in fence.rings:
                pts = [self.cmap.proj.xy(lon, lat) for lon, lat in ring]
                if len(pts) < 3:
                    continue
                coords = self._flat(pts)
                c.create_polygon(
                    coords, fill=C_FENCE_FILL, outline="", stipple="gray12",
                )
                c.create_line(
                    *(coords + coords[:2]), fill=C_FENCE,
                    width=self.pxf(1.8), dash=(7, 4),
                    capstyle=tk.ROUND, joinstyle=tk.ROUND,
                )

    def _draw_fence_labels(self) -> None:
        for fence in self.fences:
            if not fence.rings:
                continue
            lon, lat = max(fence.rings[0], key=lambda p: p[1])
            px, py = self._s(self.cmap.proj.xy(lon, lat))
            text = fence.name if "fence" in fence.name.lower() else f"{fence.name} Fence"
            self._text_bg(
                px, py - self.px(13), text, fg=C_FENCE, bg=C_BG,
                font=(FONT_UI, 8), pad=(4, 1),
            )

    def _draw_areas(self) -> None:
        active = self._active_areas()
        for ai, area in enumerate(self.cmap.areas):
            coords = self._flat(area.ring_xy)
            if ai in active:
                self.canvas.create_polygon(
                    coords, fill=C_AREA_ACTIVE, outline=C_AREA_ACTIVE_LINE,
                    width=self.pxf(2.5),
                )
            else:
                self.canvas.create_polygon(
                    coords, fill=AREA_COLORS.get(area.desc, "#efe9dd"), outline="",
                )

    def _draw_roads(self) -> None:
        c = self.canvas
        segs = []
        for eid in self.cmap.road_eids:
            e = self.cmap.edges[eid]
            x1, y1 = self._s(self.cmap.coords[e.u])
            x2, y2 = self._s(self.cmap.coords[e.v])
            segs.append((x1, y1, x2, y2))
        casing = self.pxf(9)
        core = self.pxf(4.5)
        for x1, y1, x2, y2 in segs:
            c.create_line(x1, y1, x2, y2, fill=C_ROAD_CASING, width=casing, capstyle=tk.ROUND)
        for x1, y1, x2, y2 in segs:
            c.create_line(x1, y1, x2, y2, fill=C_ROAD, width=core, capstyle=tk.ROUND)

    def _draw_road_labels(self) -> None:
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        for desc, (x, y) in self.cmap.labels:
            px, py = self._s((x, y))
            if -60 <= px <= w + 60 and -20 <= py <= h + 20:
                self._text_bg(
                    px, py, desc, fg="#7c7366", bg=C_BG,
                    font=(FONT_UI, 8), pad=(3, 1),
                )

    def _draw_route(self, r: RouteResult) -> None:
        c = self.canvas

        # Temporary links AC and BD: orange dashed lines (white underlay keeps them readable).
        for p, q in ((r.a_xy, r.c_xy), (r.d_xy, r.b_xy)):
            if math.dist(p, q) < 0.05:
                continue
            coords = (*self._s(p), *self._s(q))
            c.create_line(
                *coords, fill="#ffffff", width=self.pxf(7),
                dash=(7, 4), capstyle=tk.ROUND,
            )
            c.create_line(
                *coords, fill=C_TEMP, width=self.pxf(3.5),
                dash=(7, 4), capstyle=tk.ROUND,
            )

        # Shortest campus route C -> D
        if len(r.path) >= 2:
            coords = self._flat(r.path)
            c.create_line(
                *coords, fill="#ffffff", width=self.pxf(11),
                capstyle=tk.ROUND, joinstyle=tk.ROUND,
            )
            c.create_line(
                *coords, fill=C_ROUTE, width=self.pxf(6),
                capstyle=tk.ROUND, joinstyle=tk.ROUND,
            )

        # C、D
        rad = self.pxf(5)
        for p, label in ((r.c_xy, "C"), (r.d_xy, "D")):
            px, py = self._s(p)
            c.create_oval(px - rad, py - rad, px + rad, py + rad,
                          fill="#ffffff", outline=C_ROUTE, width=self.pxf(2))
            self._text_bg(px + self.px(11), py + self.px(11), label,
                          fg=C_ROUTE, bg="#ffffff",
                          font=(FONT_UI, 9, "bold"), pad=(3, 1))

        # Distance badge
        mid = polyline_midpoint([r.a_xy] + r.path + [r.b_xy])
        px, py = self._s(mid)
        self._badge_at(px, py, fmt_len(r.total_m))

    def _draw_run(self, run: PointRun) -> None:
        c = self.canvas
        if len(run.path) >= 2:
            coords = self._flat(run.path)
            c.create_line(
                *coords, fill="#ffffff", width=self.pxf(11),
                capstyle=tk.ROUND, joinstyle=tk.ROUND,
            )
            c.create_line(
                *coords, fill=C_ROUTE, width=self.pxf(6),
                capstyle=tk.ROUND, joinstyle=tk.ROUND,
            )

        # Place the distance badge below the numbered points so it does not
        # cover the numbers.
        mid = polyline_midpoint(run.path)
        px, py = self._s(mid)
        self._badge_at(px, py, fmt_len(run.total_m))

        # Numbers show the order.
        for idx, p in enumerate(run.points_xy, 1):
            px, py = self._s(p)
            outer = self.pxf(14)
            inner = self.pxf(11.5)
            c.create_oval(px - outer, py - outer, px + outer, py + outer,
                          fill="#ffffff", outline="", tags="endpoint")
            c.create_oval(px - inner, py - inner, px + inner, py + inner,
                          fill="#ffffff", outline=C_TEMP,
                          width=self.pxf(2.5), tags="endpoint")
            c.create_text(px, py, text=str(idx), fill=C_TEMP,
                          font=(FONT_UI, 9, "bold"), tags="endpoint")

    def _draw_immersive(self, run: PointRun) -> None:
        c = self.canvas
        start = run.start_xy

        # Temporary connector from the start to the nearest road.
        if start is not None and run.legs:
            c_xy = run.legs[0].c_xy
            if math.dist(start, c_xy) >= 0.05:
                coords = (*self._s(start), *self._s(c_xy))
                c.create_line(
                    *coords, fill="#ffffff", width=self.pxf(7),
                    dash=(7, 4), capstyle=tk.ROUND,
                )
                c.create_line(
                    *coords, fill=C_TEMP, width=self.pxf(3.5),
                    dash=(7, 4), capstyle=tk.ROUND,
                )

        # Revealed route: if the start is far from a road, the blue route
        # begins at the projection point.
        road_path = run.path
        if (start is not None and run.legs
                and math.dist(start, run.legs[0].c_xy) >= 1.0
                and len(road_path) >= 2):
            road_path = road_path[1:]
        if len(road_path) >= 2:
            coords = self._flat(road_path)
            c.create_line(
                *coords, fill="#ffffff", width=self.pxf(11),
                capstyle=tk.ROUND, joinstyle=tk.ROUND,
            )
            c.create_line(
                *coords, fill=C_ROUTE, width=self.pxf(6),
                capstyle=tk.ROUND, joinstyle=tk.ROUND,
            )

        mid = polyline_midpoint(run.path)
        px, py = self._s(mid)
        self._badge_at(px, py, fmt_len(run.total_m))

        # Start point
        if start is not None:
            px, py = self._s(start)
            outer = self.pxf(8.5)
            inner = self.pxf(6.5)
            c.create_oval(px - outer, py - outer, px + outer, py + outer,
                          fill="#ffffff", outline="", tags="endpoint")
            c.create_oval(px - inner, py - inner, px + inner, py + inner,
                          fill=C_A, outline="#ffffff", width=self.pxf(1.5),
                          tags="endpoint")
            self._text_bg(px + self.px(12), py - self.px(13), "Start",
                          fg=C_A, bg="#ffffff",
                          font=(FONT_UI, 10, "bold"), pad=(3, 0),
                          tags="endpoint")

        # Revealed checkpoints: completed ones are filled; the current target is outlined.
        last = len(run.points_xy) - 1
        for idx, p in enumerate(run.points_xy):
            px, py = self._s(p)
            number = str(idx + 1)
            outer = self.pxf(14)
            inner = self.pxf(11.5)
            c.create_oval(px - outer, py - outer, px + outer, py + outer,
                          fill="#ffffff", outline="", tags="endpoint")
            if idx < run.completed:
                c.create_oval(px - inner, py - inner, px + inner, py + inner,
                              fill=C_TEMP, outline="#ffffff",
                              width=self.pxf(2.5), tags="endpoint")
                c.create_text(px, py, text=number, fill="#ffffff",
                              font=(FONT_UI, 9, "bold"), tags="endpoint")
            else:
                c.create_oval(px - inner, py - inner, px + inner, py + inner,
                              fill="#ffffff", outline=C_TEMP,
                              width=self.pxf(2.5), tags="endpoint")
                c.create_text(px, py, text=number, fill=C_TEMP,
                              font=(FONT_UI, 9, "bold"), tags="endpoint")
            if idx == last and not run.finished:
                halo = outer + self.pxf(3)
                c.create_oval(px - halo, py - halo, px + halo, py + halo,
                              outline=C_TEMP, width=self.pxf(1),
                              dash=(2, 3), tags="endpoint")

    def _draw_points(self) -> None:
        c = self.canvas
        for idx, pt in enumerate(self.points):
            px, py = self._s(self.cmap.proj.xy(*pt))
            color = C_A if idx == 0 else C_B
            label = "A" if idx == 0 else "B"
            outer = self.pxf(8.5)
            inner = self.pxf(6.5)
            c.create_oval(px - outer, py - outer, px + outer, py + outer,
                          fill="#ffffff", outline="", tags="endpoint")
            c.create_oval(px - inner, py - inner, px + inner, py + inner,
                          fill=color, outline="#ffffff", width=self.pxf(1.5),
                          tags="endpoint")
            self._text_bg(px + self.px(12), py - self.px(13), label,
                          fg=color, bg="#ffffff",
                          font=(FONT_UI, 11, "bold"), pad=(3, 0),
                          tags="endpoint")

    def _draw_pace_marker(self) -> None:
        c = self.canvas
        c.delete("pace_arrow")
        if (not self._anim_pts or self._anim_total <= 0.0
                or self._anim_total_time <= 0.0):
            return
        frac = min(1.0, max(0.0, self._sim_time / self._anim_total_time))
        got = self._point_on_route(frac * self._anim_total)
        if got is None:
            return
        pos, direction = got
        sx, sy = self._s(pos)
        dx = direction[0] * self.scale
        dy = -direction[1] * self.scale
        norm = math.hypot(dx, dy)
        if norm < 1e-9:
            dx, dy = 1.0, 0.0
        else:
            dx, dy = dx / norm, dy / norm
        px, py = -dy, dx
        size = self.pxf(10.5)
        tip = (sx + dx * size * 1.35, sy + dy * size * 1.35)
        back = (sx - dx * size * 0.55, sy - dy * size * 0.55)
        left = (back[0] + px * size * 0.95, back[1] + py * size * 0.95)
        right = (back[0] - px * size * 0.95, back[1] - py * size * 0.95)
        c.create_polygon(
            tip[0], tip[1], left[0], left[1], right[0], right[1],
            fill="#ffffff", outline="", tags="pace_arrow",
        )
        tip2 = (sx + dx * size * 1.08, sy + dy * size * 1.08)
        back2 = (sx - dx * size * 0.38, sy - dy * size * 0.38)
        left2 = (back2[0] + px * size * 0.7, back2[1] + py * size * 0.7)
        right2 = (back2[0] - px * size * 0.7, back2[1] - py * size * 0.7)
        c.create_polygon(
            tip2[0], tip2[1], left2[0], left2[1], right2[0], right2[1],
            fill=C_PACE, outline="", tags="pace_arrow",
        )
        if c.find_withtag("endpoint"):
            c.tag_lower("pace_arrow", "endpoint")

    def _text_bg(self, x: float, y: float, text: str, *, fg: str, bg: str,
                 font, pad=(3, 1), tags="") -> int:
        c = self.canvas
        tid = c.create_text(x, y, text=text, fill=fg, font=font,
                            anchor="center", tags=tags)
        box = c.bbox(tid)
        if box:
            padx, pady = self.pxf(pad[0]), self.pxf(pad[1])
            rid = c.create_rectangle(
                box[0] - padx, box[1] - pady, box[2] + padx, box[3] + pady,
                fill=bg, outline="", tags=tags,
            )
            c.tag_lower(rid, tid)
        return tid

    def _badge(self, x: float, y: float, text: str) -> None:
        c = self.canvas
        tid = c.create_text(x, y, text=text, fill=C_ROUTE,
                            font=(FONT_UI, 10, "bold"), anchor="center")
        box = c.bbox(tid)
        if box:
            padx, pady = self.pxf(7), self.pxf(4)
            rid = c.create_rectangle(
                box[0] - padx, box[1] - pady, box[2] + padx, box[3] + pady,
                fill="#ffffff", outline="#a9c9ef",
            )
            c.tag_lower(rid, tid)

    def _badge_at(self, x: float, y: float, text: str) -> None:
        """Place the distance badge near the route midpoint, clamped to the canvas."""
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        mx, my = self.pxf(70), self.pxf(22)
        px = min(max(x, mx), max(mx, w - mx))
        py = min(max(y - self.pxf(26), my), max(my, h - my))
        self._badge(px, py, text)

    def _nice_scale_m(self) -> float:
        target = self.pxf(105) / max(self.scale, 1e-9)
        candidates = (5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 5000)
        return min(candidates, key=lambda v: abs(math.log(v / target)))

    def _draw_scalebar(self) -> None:
        c = self.canvas
        h = self.canvas.winfo_height()
        meters = self._nice_scale_m()
        length = meters * self.scale
        x0 = self.pxf(26)
        y0 = h - self.pxf(30)
        c.create_line(x0, y0, x0 + length, y0, fill="#ffffff",
                      width=self.pxf(7), capstyle=tk.ROUND)
        c.create_line(x0, y0, x0 + length, y0, fill="#5a5348",
                      width=self.pxf(2.5), capstyle=tk.ROUND)
        for xx in (x0, x0 + length):
            c.create_line(xx, y0 - self.pxf(6), xx, y0 + self.pxf(1),
                          fill="#5a5348", width=self.pxf(2.5))
        self._text_bg(x0 + length / 2, y0 - self.pxf(14), fmt_len(meters),
                      fg="#5a5348", bg=C_BG, font=(FONT_MONO, 8), pad=(3, 0))

    def _draw_north(self) -> None:
        c = self.canvas
        w = self.canvas.winfo_width()
        x = w - self.pxf(38)
        y = self.pxf(36)
        c.create_line(x, y + self.pxf(16), x, y - self.pxf(4),
                      fill="#6b6357", width=self.pxf(2),
                      arrow=tk.LAST, arrowshape=(9, 11, 4))
        self._text_bg(x, y + self.pxf(28), "N", fg="#6b6357", bg=C_BG,
                      font=("Segoe UI", 9, "bold"), pad=(2, 0))
