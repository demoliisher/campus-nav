# -*- coding: utf-8 -*-
"""Main window: interaction, sidebar, mode switching, and pace playback."""

from __future__ import annotations

import math
import time
import tkinter as tk
from tkinter import ttk

from ..formatting import fmt_len, fmt_lonlat, fmt_walked
from ..geo.fence import Fence
from ..pacing import (
    PACE_DEFAULT_SEC,
    PACE_MAX_SEC,
    PACE_MIN_SEC,
    fmt_duration,
)
from ..routing.campus_map import CampusMap
from ..routing.immersive import ImmersiveSession
from ..routing.models import PointRun, RouteResult
from .drawing import MapDrawingMixin
from .theme import (
    C_A,
    C_B,
    C_BG,
    C_LINE,
    C_MUTED,
    C_PACE,
    C_ROUTE,
    C_SIDEBAR,
    C_STATUS_BG,
    C_STATUS_FG,
    C_TEMP,
    C_TEXT,
    FONT_MONO,
    FONT_UI,
)
from .widgets import PaceSlider


class CampusNavApp(MapDrawingMixin):
    """Main window for GDPU ZS Navigation."""

    def __init__(self, root: tk.Tk, cmap: CampusMap,
                 fences: list[Fence] | None = None):
        self.root = root
        self.cmap = cmap
        self.fences = list(fences or [])
        self.points: list[tuple[float, float]] = []      # [(lon, lat)], at most 2
        self.result: RouteResult | None = None
        self.error: str | None = None
        self.mode = "point"                              # point / run
        self.run: PointRun | None = None
        self.run_error: str | None = None
        self.immersive = tk.BooleanVar(value=False)
        self.immersive_session: ImmersiveSession | None = None
        self.reduce_backtrack = tk.BooleanVar(value=False)
        self.run_limit = tk.BooleanVar(value=False)
        self.gender = "male"                             # male / female
        self.scale = 0.0
        self.ox = 0.0
        self.oy = 0.0
        self._fit_scale = 0.0
        self._view_locked = False
        self._pan_anchor: tuple[int, int] | None = None
        self._last_click = 0.0
        self._closing = False

        # Pace / motion playback (controlled by the play button, no separate switch)
        self.pace_seconds = PACE_DEFAULT_SEC
        self._playing = False
        self._anim_after: str | None = None
        self._last_tick = 0.0
        self._sim_time = 0.0
        self._anim_pts: list[tuple[float, float]] = []
        self._anim_cum: list[float] = []
        self._anim_total = 0.0
        self._anim_total_time = 0.0

        try:
            self.ui = max(0.75, min(2.5, root.winfo_fpixels("1i") / 96.0))
        except Exception:
            self.ui = 1.0

        self._build_ui()
        self._bind_events()
        self._sync_side()
        self._on_route_changed()

    def px(self, n: float) -> int:
        return max(1, int(round(n * self.ui)))

    def pxf(self, n: float) -> float:
        return n * self.ui

    # ---- UI construction -------------------------------------------------
    def _build_ui(self) -> None:
        P = self.px
        self.root.title("GDPU ZS Navigation")
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        win_w = min(P(1160), sw - P(48))
        win_h = min(P(790), sh - P(90))
        self.root.geometry(
            f"{win_w}x{win_h}+{max(0, (sw - win_w) // 2)}+"
            f"{max(0, (sh - win_h) // 2 - P(8))}"
        )
        self.root.minsize(P(860), P(600))
        self.root.configure(bg=C_SIDEBAR)

        head = tk.Frame(self.root, bg=C_SIDEBAR)
        head.pack(fill="x")
        tk.Label(
            head, text="GDPU ZS Navigation", font=(FONT_UI, 18, "bold"),
            fg="#1f2d3d", bg=C_SIDEBAR,
        ).pack(side="left", padx=(P(20), P(12)), pady=(P(12), P(9)))
        tk.Label(
            head, text="Shortest route · Distance · Pace demo",
            font=(FONT_UI, 9), fg=C_MUTED, bg=C_SIDEBAR,
        ).pack(side="left", pady=(P(18), P(9)))

        mode_frame = tk.Frame(head, bg=C_SIDEBAR)
        mode_frame.pack(side="right", padx=(P(10), P(20)), pady=(P(10), P(8)))
        self.mode_btns: dict[str, tk.Button] = {}
        for key, label in (("point", "Route"), ("run", "Scoring Run")):
            btn = tk.Button(
                mode_frame, text=label,
                command=lambda k=key: self._set_mode(k),
                font=(FONT_UI, 9), relief="flat", bd=0,
                padx=P(12), pady=P(4), cursor="hand2",
            )
            btn.pack(side="left", padx=(P(4), 0))
            self.mode_btns[key] = btn
        tk.Frame(self.root, bg=C_LINE, height=P(1)).pack(fill="x")

        body = tk.Frame(self.root, bg=C_BG)
        body.pack(fill="both", expand=True)

        side = tk.Frame(body, bg=C_SIDEBAR, width=P(286))
        side.pack(side="right", fill="y")
        side.pack_propagate(False)
        tk.Frame(body, bg=C_LINE, width=P(1)).pack(side="right", fill="y")

        self.canvas = tk.Canvas(
            body, bg=C_BG, highlightthickness=0, cursor="crosshair",
        )
        self.canvas.pack(side="left", fill="both", expand=True)

        self.show_labels = tk.BooleanVar(value=False)
        ttk.Style().configure("TButton", font=(FONT_UI, 9))

        def section(title: str, top: int = 16, parent=None) -> None:
            target = parent or side
            tk.Label(
                target, text=title, font=(FONT_UI, 9, "bold"), fg=C_MUTED,
                bg=C_SIDEBAR, anchor="w",
            ).pack(fill="x", padx=P(18), pady=(P(top), P(6)))

        def hr() -> None:
            tk.Frame(side, bg="#ece7de", height=P(1)).pack(
                fill="x", padx=P(18), pady=P(8)
            )

        # Status
        section("Status", 16)
        self.status_lbl = tk.Label(
            side, text="", font=(FONT_UI, 9), fg=C_STATUS_FG, bg=C_STATUS_BG,
            justify="left", anchor="nw", wraplength=P(232),
            padx=P(10), pady=P(9),
        )
        self.status_lbl.pack(fill="x", padx=P(18))

        # Actions
        section("Actions")
        btn_row = tk.Frame(side, bg=C_SIDEBAR)
        btn_row.pack(fill="x", padx=P(18))
        self.action_btn = ttk.Button(btn_row, text="Undo", command=self.undo, width=7)
        self.clear_btn = ttk.Button(btn_row, text="Clear", command=self.clear, width=7)
        self.fit_btn = ttk.Button(btn_row, text="Fit view", command=self._fit_view, width=9)
        for col, btn in enumerate((self.action_btn, self.clear_btn, self.fit_btn)):
            btn.grid(row=0, column=col, sticky="ew", padx=(0, P(5)) if col < 2 else 0)
            btn_row.columnconfigure(col, weight=1)

        tk.Checkbutton(
            side, text="Show road names", variable=self.show_labels, command=self._redraw,
            bg=C_SIDEBAR, fg=C_TEXT, activebackground=C_SIDEBAR, selectcolor=C_SIDEBAR,
            font=(FONT_UI, 9), anchor="w", padx=0, bd=0, highlightthickness=0,
        ).pack(fill="x", padx=P(15), pady=(P(9), 0))

        # Pace
        pace_head = tk.Frame(side, bg=C_SIDEBAR)
        pace_head.pack(fill="x", padx=P(18), pady=(P(13), P(4)))
        tk.Label(
            pace_head, text="Pace", font=(FONT_UI, 9, "bold"),
            fg=C_MUTED, bg=C_SIDEBAR, anchor="w",
        ).pack(side="left")
        self.pace_hint_lbl = tk.Label(
            pace_head, text="0'05''–10'00'' /km", font=(FONT_UI, 8),
            fg=C_MUTED, bg=C_SIDEBAR, anchor="e",
        )
        self.pace_hint_lbl.pack(side="right")

        pace_row = tk.Frame(side, bg=C_SIDEBAR)
        pace_row.pack(fill="x", padx=P(18))
        self.play_btn = tk.Button(
            pace_row, text="Play", command=self._toggle_play,
            font=(FONT_UI, 9, "bold"), bg="#e3e8ef", fg="#9aa5b4",
            activebackground=C_PACE, activeforeground="#ffffff",
            disabledforeground="#9aa5b4", relief="flat", bd=0,
            padx=P(9), pady=P(3), cursor="hand2", state="disabled",
        )
        self.play_btn.pack(side="right")
        self.pace_value_lbl = tk.Label(
            pace_row, text=fmt_duration(self.pace_seconds),
            font=(FONT_MONO, 9), fg=C_TEXT, bg=C_SIDEBAR,
            anchor="e", width=7,
        )
        self.pace_value_lbl.pack(side="right", padx=(P(4), P(6)))
        self.pace_slider = PaceSlider(
            pace_row, min_sec=PACE_MIN_SEC, max_sec=PACE_MAX_SEC,
            value=self.pace_seconds, on_change=self._on_pace_slide,
            ui_scale=self.ui,
        )
        self.pace_slider.pack(
            side="left", fill="x", expand=True, padx=(0, P(4))
        )

        self.pace_bar = tk.Canvas(
            side, height=P(9), bg=C_SIDEBAR, highlightthickness=0,
        )
        self.pace_bar.pack(fill="x", padx=P(18), pady=(P(7), P(1)))
        self.pace_bar.bind("<Configure>", lambda e: self._update_pace_ui())

        info_row = tk.Frame(side, bg=C_SIDEBAR)
        info_row.pack(fill="x", padx=P(18))
        self.pace_dist_lbl = tk.Label(
            info_row, text="—", font=(FONT_UI, 9), fg="#6f675c",
            bg=C_SIDEBAR, anchor="w",
        )
        self.pace_dist_lbl.pack(side="left")
        self.pace_time_lbl = tk.Label(
            info_row, text="—", font=(FONT_UI, 9), fg="#6f675c",
            bg=C_SIDEBAR, anchor="e",
        )
        self.pace_time_lbl.pack(side="right")

        hr()

        # Mode content: two-point route / scoring run
        self.mode_content = tk.Frame(side, bg=C_SIDEBAR)
        self.mode_content.pack(fill="x")

        self.point_box = tk.Frame(self.mode_content, bg=C_SIDEBAR)
        self.run_box = tk.Frame(self.mode_content, bg=C_SIDEBAR)
        self.point_box.pack(fill="x")

        section("Points", 0, self.point_box)
        self.coord_lbls: dict[str, tk.Label] = {}
        for key, color in (("A", C_A), ("B", C_B), ("C", C_ROUTE), ("D", C_ROUTE)):
            row = tk.Frame(self.point_box, bg=C_SIDEBAR)
            row.pack(fill="x", padx=P(18), pady=P(1))
            tk.Label(
                row, text=key, width=2, font=(FONT_UI, 9, "bold"), fg=color,
                bg=C_SIDEBAR, anchor="w",
            ).pack(side="left")
            lbl = tk.Label(
                row, text="—", font=(FONT_MONO, 9), fg=C_TEXT, bg=C_SIDEBAR, anchor="w",
            )
            lbl.pack(side="left", fill="x", expand=True)
            self.coord_lbls[key] = lbl

        # Scoring-run description (shown when run_box is active)
        section("Scoring run (5 points)", 0, self.run_box)
        self.run_desc_lbl = tk.Label(
            self.run_box, text="Five ordered waypoints appear on roads; numbers show the order.",
            font=(FONT_UI, 8), fg=C_MUTED, bg=C_SIDEBAR,
            justify="left", anchor="w", wraplength=P(250),
        )
        self.run_desc_lbl.pack(fill="x", padx=P(18), pady=(P(2), P(2)))

        seq_row = tk.Frame(self.run_box, bg=C_SIDEBAR)
        seq_row.pack(fill="x", padx=P(18), pady=(P(3), P(2)))
        tk.Checkbutton(
            seq_row, text="Immersive", variable=self.immersive,
            command=self._on_immersive_toggle,
            bg=C_SIDEBAR, fg=C_TEXT, activebackground=C_SIDEBAR,
            selectcolor=C_SIDEBAR, font=(FONT_UI, 9), anchor="w",
            bd=0, highlightthickness=0, padx=0,
        ).pack(side="left")
        tk.Label(
            seq_row, text="1 → 2 → 3 → 4 → 5",
            font=(FONT_UI, 9, "bold"), fg=C_TEMP, bg=C_SIDEBAR, anchor="e",
        ).pack(side="right")

        opt_row = tk.Frame(self.run_box, bg=C_SIDEBAR)
        opt_row.pack(fill="x", padx=P(18), pady=(P(4), P(1)))
        tk.Checkbutton(
            opt_row, text="Reduce backtracking", variable=self.reduce_backtrack,
            command=self._on_reduce_backtrack_toggle,
            bg=C_SIDEBAR, fg=C_TEXT, activebackground=C_SIDEBAR,
            selectcolor=C_SIDEBAR, font=(FONT_UI, 9), anchor="w",
            bd=0, highlightthickness=0, padx=0,
        ).pack(side="left")
        tk.Label(
            opt_row, text="Avoid U-turns",
            font=(FONT_UI, 8), fg=C_MUTED, bg=C_SIDEBAR,
        ).pack(side="left", padx=(P(5), 0))

        cap_row = tk.Frame(self.run_box, bg=C_SIDEBAR)
        cap_row.pack(fill="x", padx=P(18), pady=(P(2), P(1)))
        tk.Checkbutton(
            cap_row, text="Daily cap", variable=self.run_limit,
            command=self._on_run_limit_toggle,
            bg=C_SIDEBAR, fg=C_TEXT, activebackground=C_SIDEBAR,
            selectcolor=C_SIDEBAR, font=(FONT_UI, 9), anchor="w",
            bd=0, highlightthickness=0, padx=0,
        ).pack(side="left")
        self.gender_btns: dict[str, tk.Button] = {}
        for key, label in (("male", "Male"), ("female", "Female")):
            gbtn = tk.Button(
                cap_row, text=label,
                command=lambda k=key: self._set_gender(k),
                font=(FONT_UI, 8), relief="flat", bd=0,
                padx=P(6), pady=P(1), cursor="hand2",
            )
            gbtn.pack(side="left", padx=(P(4), 0))
            self.gender_btns[key] = gbtn
        self.cap_lbl = tk.Label(
            cap_row, text="Cap 8 km", font=(FONT_UI, 8),
            fg=C_MUTED, bg=C_SIDEBAR,
        )
        self.cap_lbl.pack(side="left", padx=(P(6), 0))
        self._update_gender_buttons()

        hr()

        # Distance
        self.mileage_title = tk.Label(
            side, text="Distance (with connectors)", font=(FONT_UI, 9, "bold"),
            fg=C_MUTED, bg=C_SIDEBAR, anchor="w",
        )
        self.mileage_title.pack(fill="x", padx=P(18), pady=(0, P(6)))
        self.total_lbl = tk.Label(
            side, text="—", font=(FONT_UI, 26, "bold"), fg=C_ROUTE,
            bg=C_SIDEBAR, anchor="w",
        )
        self.total_lbl.pack(fill="x", padx=P(18))
        self.mileage_sub_lbl = tk.Label(
            side, text="", font=(FONT_UI, 8), fg=C_MUTED,
            bg=C_SIDEBAR, anchor="w",
        )
        self.mileage_sub_lbl.pack(fill="x", padx=P(18))

        grid = tk.Frame(side, bg=C_SIDEBAR)
        grid.pack(fill="x", padx=P(18), pady=(P(4), 0))
        grid.columnconfigure(1, weight=1)
        self.mile_name_lbls: list[tk.Label] = []
        self.mile_val_lbls: list[tk.Label] = []
        for row in range(6):
            name = tk.Label(
                grid, text="", font=(FONT_UI, 8), fg=C_MUTED,
                bg=C_SIDEBAR, anchor="w",
            )
            name.grid(row=row, column=0, sticky="w", pady=1)
            val = tk.Label(
                grid, text="—", font=(FONT_MONO, 10), fg=C_TEXT,
                bg=C_SIDEBAR, anchor="e",
            )
            val.grid(row=row, column=1, sticky="e", pady=1)
            self.mile_name_lbls.append(name)
            self.mile_val_lbls.append(val)

        # Bottom hint
        self.hint_lbl = tk.Label(
            side,
            text="Left-click to pick · Wheel to zoom · Middle/right drag · Ctrl+Z undo",
            font=(FONT_UI, 8), fg=C_MUTED, bg=C_SIDEBAR, justify="left",
            wraplength=P(250),
        )
        self.hint_lbl.pack(side="bottom", anchor="w", padx=P(18), pady=P(12))

    # ---- Events ----------------------------------------------------------
    def _bind_events(self) -> None:
        self.canvas.bind("<Button-1>", self._on_left_click)
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<Button-4>", lambda e: self._zoom_at(e.x, e.y, 1.2))
        self.canvas.bind("<Button-5>", lambda e: self._zoom_at(e.x, e.y, 1 / 1.2))
        for btn in ("2", "3"):
            self.canvas.bind(f"<Button-{btn}>", self._pan_start)
            self.canvas.bind(f"<B{btn}-Motion>", self._pan_move)
            self.canvas.bind(f"<ButtonRelease-{btn}>", self._pan_end)
        self.canvas.bind("<Configure>", self._on_configure)
        self.root.bind("<Control-z>", self._on_undo_key)
        self.root.bind("<Control-Z>", self._on_undo_key)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_configure(self, event) -> None:
        if event.widget is not self.canvas:
            return
        if self._fit_scale <= 0.0 or not self._view_locked:
            self._fit_view()
        else:
            self._redraw()

    def _on_undo_key(self, _event) -> str:
        self.undo()
        return "break"

    def _on_left_click(self, event) -> None:
        if self.mode == "run":
            if self.immersive.get() and self.run is None:
                self._start_immersive_at(event.x, event.y)
            return
        if self.mode != "point":
            return
        now = time.monotonic()
        if now - self._last_click < 0.18:      # avoid picking two points on a double-click
            return
        self._last_click = now

        x, y = self._world_at(event.x, event.y)
        lon, lat = self.cmap.proj.lonlat(x, y)
        pt = (lon, lat)

        if len(self.points) >= 2:              # A and B set: a third click starts a new query
            self.points = [pt]
        else:
            self.points.append(pt)
        self.error = None
        self.result = None
        if len(self.points) == 2:
            self._solve()
        self._on_route_changed()
        self._sync_side()
        self._redraw()

    def _start_immersive_at(self, px: float, py: float) -> None:
        """While immersive mode waits for a start, convert a canvas click to local metric coordinates."""
        if not self.immersive.get() or self.run is not None:
            return
        x, y = self._world_at(px, py)
        self._start_immersive((x, y))

    def _start_immersive(self, start_xy: tuple[float, float]) -> None:
        self._stop_animation()
        self._sim_time = 0.0
        self.run_error = None
        session = ImmersiveSession(
            self.cmap, n=5, min_sep_m=60.0,
            reduce_backtrack=self.reduce_backtrack.get(),
            run_limit=self.run_limit.get(),
            daily_cap_m=self._daily_cap_m(),
        )
        try:
            run = session.start(start_xy)
        except Exception as exc:
            run = None
            self.run_error = f"Generation failed: {exc}"
        if run is None:
            self.run = None
            self.immersive_session = None
            if not self.run_error:
                self.run_error = "Could not generate an immersive route. Pick another start point."
            self.canvas.configure(cursor="crosshair")
        else:
            self.immersive_session = session
            self.run = run
            self.run_error = None
            self.canvas.configure(cursor="arrow")
        self._on_route_changed()
        self._sync_side()
        self._redraw()

    def _on_wheel(self, event) -> None:
        if event.delta:
            self._zoom_at(event.x, event.y, 1.2 if event.delta > 0 else 1 / 1.2)

    def _zoom_at(self, px: int, py: int, factor: float) -> None:
        if self.scale <= 0.0:
            return
        lo = self._fit_scale * 0.6
        hi = 16.0
        new_scale = min(hi, max(lo, self.scale * factor))
        if abs(new_scale - self.scale) < 1e-12:
            return
        wx, wy = self._world_at(px, py)
        self.scale = new_scale
        self.ox = px - wx * new_scale
        self.oy = py + wy * new_scale
        self._view_locked = True
        self._redraw()

    def _pan_start(self, event) -> None:
        self._pan_anchor = (event.x, event.y)
        self.canvas.configure(cursor="fleur")

    def _pan_move(self, event) -> None:
        if self._pan_anchor is None:
            return
        dx = event.x - self._pan_anchor[0]
        dy = event.y - self._pan_anchor[1]
        self.ox += dx
        self.oy += dy
        self._pan_anchor = (event.x, event.y)
        self._view_locked = True
        self._redraw()

    def _pan_end(self, _event) -> None:
        self._pan_anchor = None
        self.canvas.configure(cursor=self._run_cursor())

    def _run_cursor(self) -> str:
        if self.mode == "point":
            return "crosshair"
        if self.immersive.get() and self.run is None:
            return "crosshair"
        return "arrow"

    def _on_close(self) -> None:
        self._closing = True
        self._stop_animation()
        self.root.destroy()

    # ---- Pace / motion playback ------------------------------------------
    def _on_pace_slide(self, seconds: int) -> None:
        """Slide the pace bar: keep the walked position, change only total time and speed."""
        if self._closing:
            return
        seconds = max(PACE_MIN_SEC, min(PACE_MAX_SEC, int(seconds)))
        if seconds == self.pace_seconds:
            return

        total = self._anim_total
        old_time = self._anim_total_time
        old_sim = self._sim_time
        if total > 0.0 and old_time > 0.0:
            walked = total * min(1.0, max(0.0, old_sim / old_time))
        else:
            walked = 0.0

        self.pace_seconds = seconds
        self.pace_value_lbl.configure(text=fmt_duration(seconds))
        if total > 0.0:
            self._anim_total_time = total / 1000.0 * seconds
            if self._anim_total_time > 0.0:
                self._sim_time = min(
                    self._anim_total_time,
                    walked / total * self._anim_total_time,
                )
            else:
                self._sim_time = 0.0
        self._update_pace_ui()
        self._draw_pace_marker()

    def _stop_animation(self) -> None:
        if self._anim_after is not None:
            try:
                self.root.after_cancel(self._anim_after)
            except Exception:
                pass
            self._anim_after = None
        self._playing = False

    def _active_track(self):
        """Full route used for drawing/playback in the current mode: (points, distance, areas)."""
        if self.mode == "run":
            if not self.run:
                return None
            return self.run.path, self.run.total_m, self.run.areas_used
        if not self.result:
            return None
        r = self.result
        return [r.a_xy] + r.path + [r.b_xy], r.total_m, r.areas_used

    def _refresh_anim_path(self) -> None:
        """Rebuild playback samples from the current full route."""
        self._anim_pts = []
        self._anim_cum = []
        self._anim_total = 0.0
        self._anim_total_time = 0.0

        track = self._active_track()
        if not track:
            return
        pts, _total_m, _areas = track
        clean = [pts[0]]
        for p in pts[1:]:
            if math.dist(clean[-1], p) > 1e-9:
                clean.append(p)
        self._anim_pts = clean
        cum = [0.0]
        for a, b in zip(clean, clean[1:]):
            cum.append(cum[-1] + math.dist(a, b))
        self._anim_cum = cum
        self._anim_total = cum[-1] if cum else 0.0
        if self._anim_total > 0.0:
            self._anim_total_time = (
                self._anim_total / 1000.0 * self.pace_seconds
            )

    def _on_route_changed(self) -> None:
        self._stop_animation()
        self._sim_time = 0.0
        self._refresh_anim_path()
        self._update_pace_ui()

    def _extend_animation(self) -> None:
        """Extend the animation after an immersive leg is revealed, keeping the arrow position."""
        old_time = self._sim_time
        self._refresh_anim_path()
        self._sim_time = min(old_time, self._anim_total_time)
        self._update_pace_ui()

    def _advance_immersive_if_needed(self) -> bool:
        if not (self.run is not None and self.run.kind == "immersive"
                and not self.run.finished):
            return False
        return self._advance_immersive()

    def _advance_immersive(self) -> bool:
        """Current target reached: reveal the next checkpoint (or cap extension) and extend the route."""
        if self.immersive_session is None or self.run is None:
            return False
        # Apply options toggled mid-run to subsequent legs.
        self.immersive_session.reduce_backtrack = self.reduce_backtrack.get()
        self.immersive_session.run_limit = self.run_limit.get()
        self.immersive_session.daily_cap_m = self._daily_cap_m()

        old_total = self.run.total_m
        old_path_len = len(self.run.path)
        try:
            run = self.immersive_session.advance()
        except Exception as exc:
            run = None
            self.run_error = f"Failed to reveal the next checkpoint: {exc}"
        if run is None:
            if not self.run_error:
                self.run_error = "Could not reveal the next checkpoint. Please start again."
            self._sync_side()
            self._redraw()
            return False

        self.run = run
        self.run_error = None
        extended = (run.total_m > old_total + 1e-6
                    or len(run.path) > old_path_len)
        self._extend_animation()
        self._sync_side()
        self._redraw()
        return extended

    def _toggle_play(self) -> None:
        if (self._closing or not self._anim_pts
                or self._anim_total_time <= 0.0):
            return
        if self._playing:
            self._stop_animation()
            self._update_pace_ui()
            return
        if self._sim_time >= self._anim_total_time - 1e-9:
            self._sim_time = 0.0
        self._playing = True
        self._last_tick = time.monotonic()
        self._update_pace_ui()
        self._draw_pace_marker()
        self._anim_after = self.root.after(33, self._tick)

    def _tick(self) -> None:
        self._anim_after = None
        if not self._playing:
            return
        now = time.monotonic()
        dt = min(max(now - self._last_tick, 0.0), 0.1)
        self._last_tick = now
        self._sim_time = min(
            self._anim_total_time,
            self._sim_time + dt,
        )
        if self._sim_time >= self._anim_total_time - 1e-9:
            if self._advance_immersive_if_needed():
                # The next leg is attached: keep playing from the checkpoint.
                self._last_tick = now
                self._update_pace_ui()
                self._draw_pace_marker()
                self._anim_after = self.root.after(33, self._tick)
                return
        self._update_pace_ui()
        self._draw_pace_marker()
        if self._sim_time >= self._anim_total_time - 1e-9:
            self._playing = False
            self._update_pace_ui()
            return
        self._anim_after = self.root.after(33, self._tick)

    def _point_on_route(self, dist: float):
        pts = self._anim_pts
        if len(pts) < 2:
            return None
        d = max(0.0, min(self._anim_total, dist))
        cum = self._anim_cum
        for i in range(len(pts) - 1):
            seg = cum[i + 1] - cum[i]
            if seg <= 1e-9:
                continue
            if d <= cum[i + 1] + 1e-9:
                f = (d - cum[i]) / seg
                a, b = pts[i], pts[i + 1]
                pos = (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)
                return pos, (b[0] - a[0], b[1] - a[1])
        for i in range(len(pts) - 2, -1, -1):
            if cum[i + 1] - cum[i] > 1e-9:
                a, b = pts[i], pts[i + 1]
                return pts[-1], (b[0] - a[0], b[1] - a[1])
        return pts[0], (1.0, 0.0)

    def _update_pace_ui(self) -> None:
        track = self._active_track()
        total_m = track[1] if track else None
        total_t = self._anim_total_time
        has_route = total_m is not None and self._anim_total > 0.0

        if total_m is None:
            self.total_lbl.configure(text="No route", fg=C_MUTED)
            self.mileage_sub_lbl.configure(text="")
            self.pace_dist_lbl.configure(text="—")
            self.pace_time_lbl.configure(text="—")
            self.play_btn.configure(state="disabled", bg="#e3e8ef", fg="#9aa5b4")
            self._draw_pace_bar(0.0)
            return

        if not has_route:
            # Route exists but has no playable path (e.g. A=B and both on a road)
            self.total_lbl.configure(text=f"Walked {fmt_walked(0.0)}", fg=C_ROUTE)
            self.mileage_sub_lbl.configure(text=f"Total {fmt_len(total_m)}")
            self.pace_dist_lbl.configure(text=f"Walked {fmt_walked(0.0)}")
            self.pace_time_lbl.configure(text="—")
            self.play_btn.configure(state="disabled", bg="#e3e8ef", fg="#9aa5b4")
            self._draw_pace_bar(0.0)
            return

        elapsed = min(self._sim_time, total_t)
        frac = elapsed / total_t if total_t > 0 else 0.0
        walked = total_m * frac
        self.total_lbl.configure(
            text=f"Walked {fmt_walked(walked)}", fg=C_ROUTE
        )
        self.mileage_sub_lbl.configure(text=f"Total {fmt_len(total_m)}")
        self.pace_dist_lbl.configure(text=f"Walked {fmt_walked(walked)}")
        self.pace_time_lbl.configure(
            text=f"{fmt_duration(elapsed)} / {fmt_duration(total_t)}"
        )
        self._draw_pace_bar(frac)
        self.play_btn.configure(state="normal", bg=C_PACE, fg="#ffffff")
        if self._playing:
            self.play_btn.configure(text="Pause")
        elif self._sim_time >= total_t - 1e-9:
            self.play_btn.configure(text="Replay")
        elif self._sim_time > 0.01:
            self.play_btn.configure(text="Resume")
        else:
            self.play_btn.configure(text="Play")

    def _draw_pace_bar(self, frac: float) -> None:
        c = self.pace_bar
        w = c.winfo_width()
        h = max(c.winfo_height(), self.px(9))
        c.delete("all")
        if w <= 4:
            return
        y = h / 2.0
        c.create_line(0, y, w, y, fill="#e9edf4",
                      width=self.pxf(4), capstyle=tk.ROUND)
        if frac > 0.0:
            x = max(self.pxf(2), w * min(1.0, frac))
            c.create_line(0, y, x, y, fill=C_PACE,
                          width=self.pxf(4), capstyle=tk.ROUND)

    # ---- Point selection / undo / solve ----------------------------------
    def set_points(self, pts: list[tuple[float, float]]) -> None:
        if self.mode != "point":
            return
        self.points = list(pts)[:2]
        self.error = None
        self.result = None
        if len(self.points) == 2:
            self._solve()
        self._on_route_changed()
        self._sync_side()
        self._redraw()

    def _solve(self) -> None:
        a = self.cmap.proj.xy(*self.points[0])
        b = self.cmap.proj.xy(*self.points[1])
        try:
            self.result = self.cmap.shortest_route(a, b)
        except Exception as exc:                       # should not happen; last-resort message
            self.result = None
            self.error = f"Planning failed: {exc}"
            return
        if self.result is None:
            self.error = "The road network is disconnected; no route can be planned."

    def undo(self) -> None:
        if self.mode != "point" or not self.points:
            return
        self.points.pop()
        self.result = None
        self.error = None
        self._on_route_changed()
        self._sync_side()
        self._redraw()

    def clear(self) -> None:
        if self.mode == "run":
            if self.immersive.get():
                if (self.run is None and not self.run_error
                        and self.immersive_session is None):
                    return
                self._restart_immersive()
                return
            if self.run is None and not self.run_error:
                return
            self.run = None
            self.run_error = None
            self._on_route_changed()
            self._sync_side()
            self._redraw()
            return
        if not self.points and self.result is None and not self.error:
            return
        self.points = []
        self.result = None
        self.error = None
        self._on_route_changed()
        self._sync_side()
        self._redraw()

    def _restart_immersive(self) -> None:
        """Clear the current immersive run and wait for a new start point."""
        if not self.immersive.get():
            return
        self._stop_animation()
        self._sim_time = 0.0
        self.immersive_session = None
        self.run = None
        self.run_error = None
        self.canvas.configure(cursor="crosshair")
        self._on_route_changed()
        self._sync_side()
        self._redraw()

    def _active_areas(self) -> set[int]:
        track = self._active_track()
        active: set[int] = set(track[2]) if track else set()
        if self.mode == "point":
            for pt in self.points:
                ai = self.cmap.area_at(*self.cmap.proj.xy(*pt))
                if ai is not None:
                    active.add(ai)
        return active

    # ---- Sidebar sync ----------------------------------------------------
    def _sync_side(self) -> None:
        if self.mode == "run":
            self._sync_run_side()
        else:
            self._sync_point_side()
        self._update_mode_buttons()

    def _sync_point_side(self) -> None:
        n = len(self.points)
        if self.error:
            status, color = self.error, "#b3261e"
        elif n == 0:
            status, color = "Left-click the map to choose start point A.", C_STATUS_FG
        elif n == 1:
            status, color = "A selected. Click the map to choose endpoint B, or use Undo.", C_STATUS_FG
        else:
            status = ("Shortest route planned. Blue is the campus route; orange dashes "
                      "are temporary connectors. Click the map again for a new query.")
            if self.result and self.result.areas_used:
                names = ", ".join(
                    self.cmap.areas[i].desc for i in sorted(self.result.areas_used)
                )
                status += f" Area highlighted: {names} (freely traversable)."
            color = C_STATUS_FG
        self.status_lbl.configure(text=status, fg=color)

        for key, pt in (("A", self.points[0] if n >= 1 else None),
                        ("B", self.points[1] if n >= 2 else None)):
            self.coord_lbls[key].configure(text=fmt_lonlat(pt) if pt else "—")

        self.mileage_title.configure(text="Distance (with connectors)")
        names = ("A→C connector", "C→D campus route", "D→B connector")
        if self.result:
            r = self.result
            self.coord_lbls["C"].configure(text=fmt_lonlat(r.c_lonlat))
            self.coord_lbls["D"].configure(text=fmt_lonlat(r.d_lonlat))
            values = (fmt_len(r.ac_m), fmt_len(r.net_m), fmt_len(r.bd_m))
        else:
            self.coord_lbls["C"].configure(text="—")
            self.coord_lbls["D"].configure(text="—")
            values = ("—", "—", "—")
        for i in range(3):
            self.mile_name_lbls[i].configure(text=names[i])
            self.mile_val_lbls[i].configure(text=values[i])
        for i in range(3, 6):
            self.mile_name_lbls[i].grid_remove()
            self.mile_val_lbls[i].grid_remove()

        self.hint_lbl.configure(
            text="Left-click to pick · Wheel to zoom · Middle/right drag · Ctrl+Z undo"
        )
        if not self.hint_lbl.winfo_manager():
            self.hint_lbl.pack(side="bottom", anchor="w",
                               padx=self.px(18), pady=self.px(12))
        self.action_btn.configure(text="Undo", command=self.undo)
        self.action_btn.state(["!disabled"] if self.points else ["disabled"])
        self.clear_btn.state(
            ["!disabled"] if (self.points or self.result or self.error) else ["disabled"]
        )

    def _sync_run_side(self) -> None:
        self.hint_lbl.pack_forget()
        keep = self.immersive.get()
        run = self.run
        is_immersive = run is not None and run.kind == "immersive"

        self.run_desc_lbl.configure(
            text=("Pick a start, then reveal five checkpoints one at a time." if keep
                  else "Five ordered waypoints appear on roads; numbers show the order.")
        )

        if self.run_error:
            status, color = self.run_error, "#b3261e"
        elif is_immersive:
            status, color = self._immersive_status(run), C_STATUS_FG
        elif keep and run is None:
            status, color = (
                "Immersive: click the map to choose a start point (not a checkpoint).", C_STATUS_FG
            )
        else:
            status, color = self._normal_run_status(run), C_STATUS_FG
        self.status_lbl.configure(text=status, fg=color)

        if is_immersive:
            self._sync_immersive_mileage(run)
        else:
            self._sync_normal_mileage(run)

        if keep:
            self.action_btn.configure(
                text="New start", command=self._restart_immersive
            )
            self.action_btn.state(["!disabled"])
            self.clear_btn.state(
                ["!disabled"]
                if (run is not None or self.run_error
                    or self.immersive_session is not None)
                else ["disabled"]
            )
            self.hint_lbl.configure(
                text=("Click the map to choose a start · Wheel to zoom · Middle/right drag"
                      if run is None
                      else "Press Play to run · next checkpoint appears on arrival")
            )
        else:
            self.action_btn.configure(text="Refresh", command=self._generate_run)
            self.action_btn.state(["!disabled"])
            self.clear_btn.state(
                ["!disabled"] if (run is not None or self.run_error)
                else ["disabled"]
            )
            self.hint_lbl.configure(
                text="Numbers show the order · Wheel to zoom · Middle/right drag · Refresh to randomize"
            )

    def _normal_run_status(self, run: PointRun | None) -> str:
        if run is None:
            return "Scoring run: press Refresh to generate five ordered road points."
        gender = "Male" if self.gender == "male" else "Female"
        base_m = sum(run.leg_m)
        if run.limit_state == "over":
            status = (f"Checkpoint legs {fmt_len(base_m)} exceed the daily cap "
                      f"{fmt_len(run.cap_m)} ({gender}).")
        elif run.limit_state == "transit":
            status = (f"After the checkpoints, the cap {fmt_len(run.cap_m)} "
                      f"is reached on the way to the track ({gender}).")
        elif run.limit_state == "track":
            status = (f"After the checkpoints, {fmt_len(run.track_m)} on the track, "
                      f"total {fmt_len(run.total_m)} ({gender}, cap "
                      f"{fmt_len(run.cap_m)}).")
        elif run.limit_state == "no_track":
            status = "No running track found; no extension added."
        else:
            status = (f"Five ordered road points generated, running 1→2→3→4→5; "
                      f"total {fmt_len(run.total_m)}.")
        if self.reduce_backtrack.get():
            status += " Reduce backtracking is on."
            fallback = any(leg.avoid_fallback for leg in run.legs)
            if run.transit_leg is not None and run.transit_leg.avoid_fallback:
                fallback = True
            if fallback:
                status += " (some legs had no alternative; exclusion dropped)"
        if run.areas_used:
            names = ", ".join(
                self.cmap.areas[i].desc for i in sorted(run.areas_used)
            )
            status += f" Highlighted {names} (freely traversable)."
        return status

    def _immersive_status(self, run: PointRun) -> str:
        gender = "Male" if self.gender == "male" else "Female"
        if run.finished:
            if run.limit_state == "track":
                status = (f"Immersive run complete: 5 points plus "
                          f"{fmt_len(run.track_m)} on the track, total "
                          f"{fmt_len(run.total_m)} ({gender}).")
            elif run.limit_state == "transit":
                status = (f"Immersive run complete: 5 points; cap reached on the "
                          f"way to the track at {fmt_len(run.total_m)} ({gender}).")
            elif run.limit_state == "over":
                status = (f"Immersive run complete: checkpoint legs "
                          f"{fmt_len(sum(run.leg_m))} exceed the cap "
                          f"{fmt_len(run.cap_m)} ({gender}).")
            elif run.limit_state == "no_track":
                status = "Immersive run complete: 5 points; no running track found."
            else:
                status = f"Immersive run complete: 5 points, {fmt_len(run.total_m)} total."
        elif run.completed == 0:
            status = "Immersive: checkpoint 1 is ready. Press Play to start."
            if run.legs and run.legs[0].ac_m >= 1.0:
                status = (f"Immersive: checkpoint 1 is ready. Press Play to start "
                          f"({fmt_len(run.legs[0].ac_m)} connector).")
        else:
            status = (f"Immersive: {run.completed}/5 checkpoints done; checkpoint "
                      f"{len(run.points_xy)} is ready.")
        if self.reduce_backtrack.get():
            fallback = any(leg.avoid_fallback for leg in run.legs)
            if run.transit_leg is not None and run.transit_leg.avoid_fallback:
                fallback = True
            if fallback:
                status += " Reduce backtracking: some legs fell back."
            elif not run.finished:
                status += " Reduce backtracking is on."
        if run.areas_used:
            names = ", ".join(
                self.cmap.areas[i].desc for i in sorted(run.areas_used)
            )
            status += f" Highlighted {names}."
        return status

    def _sync_normal_mileage(self, run: PointRun | None) -> None:
        cap_rows = run is not None and run.limit_state in ("transit", "track")
        self.mileage_title.configure(
            text="Distance (run + cap)" if cap_rows else "Distance (ordered run)"
        )
        if run:
            for i in range(4):
                self.mile_name_lbls[i].configure(text=f"{i + 1}→{i + 2}")
                self.mile_val_lbls[i].configure(text=fmt_len(run.leg_m[i]))
                self.mile_name_lbls[i].grid()
                self.mile_val_lbls[i].grid()
            if cap_rows:
                self.mile_name_lbls[4].configure(text="5→Track + laps")
                self.mile_val_lbls[4].configure(
                    text=(f"{fmt_len(run.transit_m)} + "
                          f"{fmt_len(run.track_m)}")
                )
                self.mile_name_lbls[4].grid()
                self.mile_val_lbls[4].grid()
            else:
                self.mile_name_lbls[4].grid_remove()
                self.mile_val_lbls[4].grid_remove()
            self.mile_name_lbls[5].grid_remove()
            self.mile_val_lbls[5].grid_remove()
        else:
            for i in range(4):
                self.mile_name_lbls[i].configure(text=f"{i + 1}→{i + 2}")
                self.mile_val_lbls[i].configure(text="—")
                self.mile_name_lbls[i].grid()
                self.mile_val_lbls[i].grid()
            for i in (4, 5):
                self.mile_name_lbls[i].grid_remove()
                self.mile_val_lbls[i].grid_remove()

    def _sync_immersive_mileage(self, run: PointRun | None) -> None:
        if run is None:
            self.mileage_title.configure(text="Distance (immersive)")
            self.mile_name_lbls[0].configure(text="Start→1")
            self.mile_val_lbls[0].configure(text="—")
            self.mile_name_lbls[0].grid()
            self.mile_val_lbls[0].grid()
            for i in range(1, 6):
                self.mile_name_lbls[i].grid_remove()
                self.mile_val_lbls[i].grid_remove()
            return

        cap_rows = run.limit_state in ("transit", "track")
        self.mileage_title.configure(
            text="Distance (immersive + cap)" if cap_rows else "Distance (immersive)"
        )
        for i in range(6):
            if i < len(run.legs):
                self.mile_name_lbls[i].configure(
                    text="Start→1" if i == 0 else f"{i}→{i + 1}"
                )
                self.mile_val_lbls[i].configure(text=fmt_len(run.leg_m[i]))
                self.mile_name_lbls[i].grid()
                self.mile_val_lbls[i].grid()
            elif i == 5 and cap_rows:
                self.mile_name_lbls[i].configure(text="5→Track + laps")
                self.mile_val_lbls[i].configure(
                    text=(f"{fmt_len(run.transit_m)} + "
                          f"{fmt_len(run.track_m)}")
                )
                self.mile_name_lbls[i].grid()
                self.mile_val_lbls[i].grid()
            else:
                self.mile_name_lbls[i].grid_remove()
                self.mile_val_lbls[i].grid_remove()

    # ---- Mode switching --------------------------------------------------
    def _update_mode_buttons(self) -> None:
        for key, btn in self.mode_btns.items():
            active = key == self.mode
            btn.configure(
                bg=C_ROUTE if active else "#eef2f7",
                fg="#ffffff" if active else C_TEXT,
                activebackground="#0d47a1" if active else "#e3eaf3",
                activeforeground="#ffffff" if active else C_TEXT,
                font=(FONT_UI, 9, "bold") if active else (FONT_UI, 9),
            )

    def _set_mode(self, mode: str) -> None:
        if mode not in ("point", "run") or mode == self.mode:
            return
        self.mode = mode
        self._stop_animation()
        self._sim_time = 0.0
        self._update_mode_buttons()
        if mode == "run":
            self.point_box.pack_forget()
            self.run_box.pack(fill="x")
            self.canvas.configure(cursor=self._run_cursor())
            if self.run is None:
                if self.immersive.get():
                    self._on_route_changed()
                    self._sync_side()
                    self._fit_view()
                    return
                self._generate_run()
                return
        else:
            self.run_box.pack_forget()
            self.point_box.pack(fill="x")
            self.canvas.configure(cursor="crosshair")
        self._on_route_changed()
        self._sync_side()
        self._fit_view()

    def _on_immersive_toggle(self) -> None:
        if self.mode != "run":
            return
        self._stop_animation()
        self._sim_time = 0.0
        self.immersive_session = None
        self.run = None
        self.run_error = None
        if self.immersive.get():
            self.canvas.configure(cursor="crosshair")
            self._on_route_changed()
            self._sync_side()
            self._redraw()
        else:
            self.canvas.configure(cursor="arrow")
            self._generate_run()

    def _generate_run(self) -> None:
        if self.mode != "run" or self.immersive.get():
            return
        self._stop_animation()
        self._sim_time = 0.0
        self.immersive_session = None
        self.run_error = None
        try:
            run = self.cmap.plan_point_run(
                5, min_sep_m=60.0,
                reduce_backtrack=self.reduce_backtrack.get(),
                run_limit=self.run_limit.get(),
                daily_cap_m=self._daily_cap_m(),
            )
        except Exception as exc:
            run = None
            self.run_error = f"Generation failed: {exc}"
        self.run = run
        if run is None and not self.run_error:
            self.run_error = "Could not generate a scoring run. Please try again."
        self._on_route_changed()
        self._sync_side()
        self._fit_view()

    def _replan_run(self) -> None:
        if self.mode != "run" or self.run is None:
            return
        if self.run.kind == "immersive":
            return
        # Reuse the current five points and only replan with the new options.
        try:
            run = self.cmap.plan_point_run(
                points=self.run.points_lonlat,
                reduce_backtrack=self.reduce_backtrack.get(),
                run_limit=self.run_limit.get(),
                daily_cap_m=self._daily_cap_m(),
            )
        except Exception as exc:
            run = None
            self.run_error = f"Replanning failed: {exc}"
        if run is None:
            self.run = None
            if not self.run_error:
                self.run_error = "Could not replan with the current settings. Refresh the five points."
            self._on_route_changed()
            self._sync_side()
            self._redraw()
            return
        self.run = run
        self.run_error = None
        self._on_route_changed()
        self._sync_side()
        self._redraw()

    def _on_reduce_backtrack_toggle(self) -> None:
        if self.mode != "run":
            return
        if self.run is not None and self.run.kind == "immersive":
            if self.immersive_session is not None:
                self.immersive_session.reduce_backtrack = (
                    self.reduce_backtrack.get()
                )
            self._sync_run_side()
            return
        if self.run is not None:
            self._replan_run()

    def _daily_cap_m(self) -> float:
        return 8000.0 if self.gender == "male" else 5000.0

    def _update_gender_buttons(self) -> None:
        cap_km = self._daily_cap_m() / 1000.0
        for key, gbtn in self.gender_btns.items():
            active = key == self.gender
            gbtn.configure(
                bg=C_TEMP if active else "#eef2f7",
                fg="#ffffff" if active else C_TEXT,
                activebackground="#e65100" if active else "#e3eaf3",
                activeforeground="#ffffff" if active else C_TEXT,
            )
        self.cap_lbl.configure(text=f"Cap {cap_km:.0f} km")

    def _set_gender(self, gender: str) -> None:
        if gender not in ("male", "female") or gender == self.gender:
            return
        self.gender = gender
        self._update_gender_buttons()
        if self.mode != "run":
            return
        if self.run is not None and self.run.kind == "immersive":
            if self.immersive_session is not None:
                self.immersive_session.daily_cap_m = self._daily_cap_m()
            self._sync_run_side()
            return
        if self.run is not None and self.run_limit.get():
            self._replan_run()
        else:
            self._sync_run_side()

    def _on_run_limit_toggle(self) -> None:
        if self.mode != "run":
            return
        if self.run is not None and self.run.kind == "immersive":
            if self.immersive_session is not None:
                self.immersive_session.run_limit = self.run_limit.get()
                self.immersive_session.daily_cap_m = self._daily_cap_m()
            self._sync_run_side()
            return
        if self.run is not None:
            self._replan_run()
