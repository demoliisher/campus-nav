# -*- coding: utf-8 -*-
"""Main window: multi-point route planning, immersive simulation, and playback."""

from __future__ import annotations

import math
import random
import time
import tkinter as tk
from tkinter import ttk

from ..formatting import fmt_len, fmt_lonlat, fmt_walked
from ..geo.fence import Fence
from ..geo.polygons import point_in_polygon, point_on_boundary
from ..pacing import (
    PACE_DEFAULT_SEC,
    PACE_MAX_SEC,
    PACE_MIN_SEC,
    fmt_duration,
)
from ..routing.campus_map import CampusMap
from ..routing.immersive import ImmersiveSession
from ..routing.models import PointRun
from ..routing.stroll import build_stroll_path
from .drawing import MapDrawingMixin
from .theme import (
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
from .widgets import ValueSlider

MAX_POINTS = 15
DEFAULT_SNAP_RADIUS_M = 25
DEFAULT_ARROW_SPACING_M = 100


class CampusNavApp(MapDrawingMixin):
    """Main window for GDPU ZS Navigation."""

    def __init__(self, root: tk.Tk, cmap: CampusMap,
                 fences: list[Fence] | None = None):
        self.root = root
        self.cmap = cmap
        self.fences = list(fences or [])
        self._fence_rings_xy = [
            [self.cmap.proj.xy(lon, lat) for lon, lat in ring]
            for fence in self.fences for ring in fence.rings
        ]

        # Route planning: an ordered list of map points and the planned legs.
        self.mode = "route"                             # route / immersive
        self.points: list[tuple[float, float]] = []     # lon/lat, at most MAX_POINTS
        self.plan: PointRun | None = None
        self.plan_error: str | None = None
        self.snap_radius_m = DEFAULT_SNAP_RADIUS_M
        self.arrow_spacing_m = DEFAULT_ARROW_SPACING_M

        # Immersive simulation: a scoring run revealed and played back live.
        self.run: PointRun | None = None
        self.run_error: str | None = None
        self.immersive_session: ImmersiveSession | None = None
        self.reduce_backtrack = tk.BooleanVar(value=False)
        self.run_limit = tk.BooleanVar(value=False)
        self.gender = "male"                             # male / female
        self.stroll = tk.BooleanVar(value=False)
        self.gps_period_sec = 1
        self.heading_max_deg = 3
        self._stroll_seed = 0
        self._preview_run: PointRun | None = None

        # Viewport
        self.scale = 0.0
        self.ox = 0.0
        self.oy = 0.0
        self._fit_scale = 0.0
        self._view_locked = False
        self._pan_anchor: tuple[int, int] | None = None
        self._left_press: tuple[int, int] | None = None
        self._left_moved = False
        self._left_pan_anchor: tuple[int, int] | None = None
        self._last_click = 0.0
        self._closing = False

        # Pace playback (immersive only, controlled by the play button)
        self.pace_seconds = PACE_DEFAULT_SEC
        self._playing = False
        self._anim_after: str | None = None
        self._last_tick = 0.0
        self._sim_time = 0.0
        self._walk_preview = False
        self._anim_pts: list[tuple[float, float]] = []
        self._anim_cum: list[float] = []
        self._anim_total = 0.0
        self._anim_total_time = 0.0
        self._trail_items: tuple[int, int] | None = None

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
            head, text="Multi-point routes · Immersive run",
            font=(FONT_UI, 9), fg=C_MUTED, bg=C_SIDEBAR,
        ).pack(side="left", pady=(P(18), P(9)))

        mode_frame = tk.Frame(head, bg=C_SIDEBAR)
        mode_frame.pack(side="right", padx=(P(10), P(20)), pady=(P(10), P(8)))
        self.mode_btns: dict[str, tk.Button] = {}
        for key, label in (("route", "Route"), ("immersive", "Immersive")):
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

        side_scroll = ttk.Scrollbar(body, orient="vertical")
        side_scroll.pack(side="right", fill="y")
        side_canvas = tk.Canvas(
            body, bg=C_SIDEBAR, width=P(340), highlightthickness=0,
            yscrollcommand=side_scroll.set,
        )
        side_canvas.pack(side="right", fill="y")
        side_scroll.configure(command=side_canvas.yview)
        side = tk.Frame(side_canvas, bg=C_SIDEBAR)
        side_window = side_canvas.create_window((0, 0), window=side, anchor="nw")
        side_canvas.bind(
            "<Configure>",
            lambda e: side_canvas.itemconfigure(side_window, width=e.width),
        )
        side.bind(
            "<Configure>",
            lambda _e: side_canvas.configure(scrollregion=side_canvas.bbox("all")),
        )
        self.side_canvas = side_canvas
        self.side_scroll = side_scroll
        self.side_frame = side
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

        def caption(text: str, parent=None) -> None:
            tk.Label(
                parent or side, text=text, font=(FONT_UI, 8), fg=C_MUTED,
                bg=C_SIDEBAR, justify="left", anchor="w", wraplength=P(304),
            ).pack(fill="x", padx=P(18))

        def hr() -> tk.Frame:
            line = tk.Frame(side, bg="#ece7de", height=P(1))
            line.pack(fill="x", padx=P(18), pady=P(8))
            return line

        # Status
        section("Status", 16)
        self.status_lbl = tk.Label(
            side, text="", font=(FONT_UI, 9), fg=C_STATUS_FG, bg=C_STATUS_BG,
            justify="left", anchor="nw", wraplength=P(272),
            padx=P(10), pady=P(9),
        )
        self.status_lbl.pack(fill="x", padx=P(18))

        # Actions
        section("Actions")
        btn_row = tk.Frame(side, bg=C_SIDEBAR)
        btn_row.pack(fill="x", padx=P(18))
        self.action_btn = ttk.Button(btn_row, text="Undo", command=self.undo, width=8)
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

        # Pace (immersive playback only; hidden in route planning)
        self.pace_box = tk.Frame(side, bg=C_SIDEBAR)
        pace_head = tk.Frame(self.pace_box, bg=C_SIDEBAR)
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

        pace_row = tk.Frame(self.pace_box, bg=C_SIDEBAR)
        pace_row.pack(fill="x", padx=P(18))
        self.play_btn = tk.Button(
            pace_row, text="Play", command=self._toggle_play,
            font=(FONT_UI, 9, "bold"), bg="#e3e8ef", fg="#9aa5b4",
            activebackground=C_PACE, activeforeground="#ffffff",
            disabledforeground="#9aa5b4", relief="flat", bd=0,
            padx=P(9), pady=P(3), cursor="hand2", state="disabled",
        )
        self.play_btn.pack(side="right")
        self.walk_btn = tk.Button(
            pace_row, text="New walk", command=self._on_new_walk,
            font=(FONT_UI, 9), bg="#e3e8ef", fg="#39414d",
            activebackground=C_PACE, activeforeground="#ffffff",
            disabledforeground="#9aa5b4", relief="flat", bd=0,
            padx=P(6), pady=P(3), cursor="hand2", state="disabled",
        )
        self.walk_btn.pack(side="right", padx=(0, P(4)))
        self.pace_value_lbl = tk.Label(
            pace_row, text=fmt_duration(self.pace_seconds),
            font=(FONT_MONO, 9), fg=C_TEXT, bg=C_SIDEBAR,
            anchor="e", width=7,
        )
        self.pace_value_lbl.pack(side="right", padx=(P(4), P(6)))
        self.pace_slider = ValueSlider(
            pace_row, min_value=PACE_MIN_SEC, max_value=PACE_MAX_SEC,
            value=self.pace_seconds, on_change=self._on_pace_slide,
            ui_scale=self.ui,
        )
        self.pace_slider.pack(
            side="left", fill="x", expand=True, padx=(0, P(4))
        )

        self.pace_bar = tk.Canvas(
            self.pace_box, height=P(9), bg=C_SIDEBAR, highlightthickness=0,
        )
        self.pace_bar.pack(fill="x", padx=P(18), pady=(P(7), P(1)))
        self.pace_bar.bind("<Configure>", lambda e: self._update_pace_ui())

        info_row = tk.Frame(self.pace_box, bg=C_SIDEBAR)
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

        self.sep_after_pace = hr()

        # Mode content
        self.mode_content = tk.Frame(side, bg=C_SIDEBAR)
        self.mode_content.pack(fill="x")

        self.route_box = tk.Frame(self.mode_content, bg=C_SIDEBAR)
        self.immersive_box = tk.Frame(self.mode_content, bg=C_SIDEBAR)

        # ---- Route planning box -----------------------------------------
        section("Route points", 0, self.route_box)
        count_row = tk.Frame(self.route_box, bg=C_SIDEBAR)
        count_row.pack(fill="x", padx=P(18), pady=(P(1), P(2)))
        tk.Label(
            count_row, text="Selected", font=(FONT_UI, 9), fg=C_TEXT,
            bg=C_SIDEBAR, anchor="w",
        ).pack(side="left")
        self.point_count_lbl = tk.Label(
            count_row, text=f"0 / {MAX_POINTS}", font=(FONT_MONO, 9),
            fg=C_MUTED, bg=C_SIDEBAR, anchor="e",
        )
        self.point_count_lbl.pack(side="right")

        self.point_list_frame = tk.Frame(self.route_box, bg=C_SIDEBAR)
        self.point_list_frame.pack(fill="x", padx=P(18), pady=(0, P(2)))

        plan_row = tk.Frame(self.route_box, bg=C_SIDEBAR)
        plan_row.pack(fill="x", padx=P(18), pady=(P(6), P(1)))
        self.random_btn = ttk.Button(
            plan_row, text="Random 5", command=self._random_route_points, width=9,
        )
        self.random_btn.pack(side="left")
        self.start_btn = tk.Button(
            plan_row, text="Start", command=self._start_plan,
            font=(FONT_UI, 9, "bold"), bg="#e3e8ef", fg="#9aa5b4",
            activebackground=C_ROUTE, activeforeground="#ffffff",
            disabledforeground="#9aa5b4", relief="flat", bd=0,
            padx=P(14), pady=P(3), cursor="hand2", state="disabled",
        )
        self.start_btn.pack(side="right")
        tk.Label(
            plan_row, text="Enter", font=(FONT_UI, 8), fg=C_MUTED, bg=C_SIDEBAR,
        ).pack(side="right", padx=(0, P(7)))

        caption("Click the map to add points; press Start to plan the whole sequence.",
                self.route_box)

        snap_head = tk.Frame(self.route_box, bg=C_SIDEBAR)
        snap_head.pack(fill="x", padx=P(18), pady=(P(11), P(2)))
        tk.Label(
            snap_head, text="Snap radius", font=(FONT_UI, 9, "bold"),
            fg=C_MUTED, bg=C_SIDEBAR, anchor="w",
        ).pack(side="left")
        self.snap_val_lbl = tk.Label(
            snap_head, text=f"{self.snap_radius_m} m", font=(FONT_MONO, 8),
            fg=C_TEXT, bg=C_SIDEBAR, anchor="e",
        )
        self.snap_val_lbl.pack(side="right")
        self.snap_slider = ValueSlider(
            self.route_box, min_value=0, max_value=60,
            value=self.snap_radius_m, on_change=self._on_snap_slide,
            ui_scale=self.ui,
        )
        self.snap_slider.pack(fill="x", padx=P(18), pady=(0, P(3)))
        caption("Points within this distance join the road without a connector.",
                self.route_box)

        arrow_head = tk.Frame(self.route_box, bg=C_SIDEBAR)
        arrow_head.pack(fill="x", padx=P(18), pady=(P(11), P(2)))
        tk.Label(
            arrow_head, text="Arrow spacing", font=(FONT_UI, 9, "bold"),
            fg=C_MUTED, bg=C_SIDEBAR, anchor="w",
        ).pack(side="left")
        self.arrow_val_lbl = tk.Label(
            arrow_head, text=f"{self.arrow_spacing_m} m", font=(FONT_MONO, 8),
            fg=C_TEXT, bg=C_SIDEBAR, anchor="e",
        )
        self.arrow_val_lbl.pack(side="right")
        self.arrow_slider = ValueSlider(
            self.route_box, min_value=20, max_value=200,
            value=self.arrow_spacing_m, on_change=self._on_arrow_slide,
            ui_scale=self.ui,
        )
        self.arrow_slider.pack(fill="x", padx=P(18), pady=(0, P(3)))
        caption("Direction arrows repeat at this spacing along the route.",
                self.route_box)

        # ---- Immersive box ----------------------------------------------
        section("Immersive simulation", 0, self.immersive_box)
        tk.Label(
            self.immersive_box,
            text="Pick a start, then reveal five checkpoints one at a time.",
            font=(FONT_UI, 8), fg=C_MUTED, bg=C_SIDEBAR,
            justify="left", anchor="w", wraplength=P(304),
        ).pack(fill="x", padx=P(18), pady=(P(2), P(4)))

        opt_row = tk.Frame(self.immersive_box, bg=C_SIDEBAR)
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

        cap_row = tk.Frame(self.immersive_box, bg=C_SIDEBAR)
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

        stroll_row = tk.Frame(self.immersive_box, bg=C_SIDEBAR)
        stroll_row.pack(fill="x", padx=P(18), pady=(P(2), P(1)))
        tk.Checkbutton(
            stroll_row, text="Stroll", variable=self.stroll,
            command=self._on_stroll_toggle,
            bg=C_SIDEBAR, fg=C_TEXT, activebackground=C_SIDEBAR,
            selectcolor=C_SIDEBAR, font=(FONT_UI, 9), anchor="w",
            bd=0, highlightthickness=0, padx=0,
        ).pack(side="left")
        tk.Label(
            stroll_row, text="Playback only · stay right within 10 m",
            font=(FONT_UI, 8), fg=C_MUTED, bg=C_SIDEBAR,
        ).pack(side="left", padx=(P(5), 0))
        self.stroll_row = stroll_row

        self.stroll_box = tk.Frame(self.immersive_box, bg=C_SIDEBAR)
        period_head = tk.Frame(self.stroll_box, bg=C_SIDEBAR)
        period_head.pack(fill="x", padx=P(18))
        tk.Label(
            period_head, text="Location interval", font=(FONT_UI, 8),
            fg=C_MUTED, bg=C_SIDEBAR, anchor="w",
        ).pack(side="left")
        self.period_val_lbl = tk.Label(
            period_head, text=f"{self.gps_period_sec} s",
            font=(FONT_MONO, 8), fg=C_TEXT, bg=C_SIDEBAR, anchor="e",
        )
        self.period_val_lbl.pack(side="right")
        self.period_slider = ValueSlider(
            self.stroll_box, min_value=1, max_value=10,
            value=self.gps_period_sec, on_change=self._on_period_slide,
            ui_scale=self.ui,
        )
        self.period_slider.pack(fill="x", padx=P(18), pady=(0, P(2)))

        angle_head = tk.Frame(self.stroll_box, bg=C_SIDEBAR)
        angle_head.pack(fill="x", padx=P(18))
        tk.Label(
            angle_head, text="Heading noise", font=(FONT_UI, 8),
            fg=C_MUTED, bg=C_SIDEBAR, anchor="w",
        ).pack(side="left")
        self.angle_val_lbl = tk.Label(
            angle_head, text=f"±{self.heading_max_deg}°",
            font=(FONT_MONO, 8), fg=C_TEXT, bg=C_SIDEBAR, anchor="e",
        )
        self.angle_val_lbl.pack(side="right")
        self.angle_slider = ValueSlider(
            self.stroll_box, min_value=0, max_value=5,
            value=self.heading_max_deg, on_change=self._on_angle_slide,
            ui_scale=self.ui,
        )
        self.angle_slider.pack(fill="x", padx=P(18), pady=(0, P(2)))
        self.stroll_box.pack_forget()

        hr()

        # Distance
        self.mileage_title = tk.Label(
            side, text="Distance", font=(FONT_UI, 9, "bold"),
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
        self.leg_name_lbls: list[tk.Label] = []
        self.leg_val_lbls: list[tk.Label] = []
        for row in range(MAX_POINTS - 1):
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
            self.leg_name_lbls.append(name)
            self.leg_val_lbls.append(val)

        # Bottom hint
        self.hint_lbl = tk.Label(
            side, text="",
            font=(FONT_UI, 8), fg=C_MUTED, bg=C_SIDEBAR, justify="left",
            wraplength=P(304),
        )
        self.hint_lbl.pack(side="bottom", anchor="w", padx=P(18), pady=P(12))

    # ---- Events ----------------------------------------------------------
    def _bind_events(self) -> None:
        self.canvas.bind("<Button-1>", self._on_left_press)
        self.canvas.bind("<B1-Motion>", self._on_left_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_left_release)
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
        self.root.bind("<Return>", self._on_enter_key)
        self.root.bind("<KP_Enter>", self._on_enter_key)
        self.root.bind_all("<MouseWheel>", self._on_sidebar_wheel, add="+")
        self.root.bind_all("<Button-4>", self._on_sidebar_wheel, add="+")
        self.root.bind_all("<Button-5>", self._on_sidebar_wheel, add="+")
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

    def _on_enter_key(self, _event) -> str | None:
        if self.mode == "route" and len(self.points) >= 2:
            self._start_plan()
            return "break"
        return None

    def _on_sidebar_wheel(self, event):
        """Scroll the sidebar; sliders ignore the wheel, so their values stay put."""
        widget = self.root.winfo_containing(event.x_root, event.y_root)
        w = widget
        while w is not None:
            if w in (self.side_canvas, self.side_scroll, self.side_frame):
                break
            w = w.master
        else:
            return None
        num = getattr(event, "num", None)
        if num == 4:
            step = -1
        elif num == 5:
            step = 1
        else:
            step = -1 if getattr(event, "delta", 0) > 0 else 1
        self.side_canvas.yview_scroll(step, "units")
        return "break"

    def _inside_fence(self, px: float, py: float) -> bool:
        if not self._fence_rings_xy:
            return False
        x, y = self._world_at(px, py)
        for ring in self._fence_rings_xy:
            if point_in_polygon(x, y, ring) or point_on_boundary(x, y, ring):
                return True
        return False

    def _on_left_press(self, event) -> None:
        self._left_press = (event.x, event.y)
        self._left_moved = False
        self._left_pan_anchor = None
        # Outside the fence, a left drag pans the map. Inside the fence the
        # left button stays reserved for picking points.
        if not self._inside_fence(event.x, event.y):
            self._left_pan_anchor = (event.x, event.y)

    def _on_left_drag(self, event) -> None:
        if self._left_press is None:
            return
        if not self._left_moved:
            sx, sy = self._left_press
            if math.hypot(event.x - sx, event.y - sy) < self.px(4):
                return
            self._left_moved = True
            if self._left_pan_anchor is not None:
                self.canvas.configure(cursor="fleur")
        if self._left_pan_anchor is not None:
            ax, ay = self._left_pan_anchor
            self.ox += event.x - ax
            self.oy += event.y - ay
            self._left_pan_anchor = (event.x, event.y)
            self._view_locked = True
            self._redraw()

    def _on_left_release(self, event) -> None:
        moved = self._left_moved
        self._left_press = None
        self._left_moved = False
        self._left_pan_anchor = None
        if moved:
            self.canvas.configure(cursor=self._mode_cursor())
            return
        self._select_at(event.x, event.y)

    def _select_at(self, px: float, py: float) -> None:
        if self.mode == "immersive":
            if self.run is None:
                self._start_immersive_at(px, py)
            return

        now = time.monotonic()
        if now - self._last_click < 0.18:      # avoid picking twice on a double-click
            return
        self._last_click = now

        x, y = self._world_at(px, py)
        lon, lat = self.cmap.proj.lonlat(x, y)

        if len(self.points) >= MAX_POINTS:
            self.plan_error = (
                f"At most {MAX_POINTS} points. Press Clear to start a new route."
            )
            self._sync_side()
            self._redraw()
            return

        self.points.append((lon, lat))
        self.plan = None
        self.plan_error = None
        self._on_route_changed()
        self._sync_side()
        self._redraw()

    def _start_immersive_at(self, px: float, py: float) -> None:
        """While immersive mode waits for a start, convert a canvas click to local metric coordinates."""
        if self.mode != "immersive" or self.run is not None:
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
            self._stroll_seed = random.randrange(1, 1 << 31)
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
        self.canvas.configure(cursor=self._mode_cursor())

    def _mode_cursor(self) -> str:
        if self.mode == "route":
            return "crosshair"
        if self.run is None:
            return "crosshair"
        return "arrow"

    def _on_close(self) -> None:
        self._closing = True
        self._stop_animation()
        self.root.destroy()

    # ---- Pace / motion playback ------------------------------------------
    def _on_pace_slide(self, seconds: int) -> None:
        """Slide the pace bar: keep the walked fraction while changing speed."""
        if self._closing:
            return
        seconds = max(PACE_MIN_SEC, min(PACE_MAX_SEC, int(seconds)))
        if seconds == self.pace_seconds:
            return
        self.pace_seconds = seconds
        self.pace_value_lbl.configure(text=fmt_duration(seconds))
        self._rebuild_anim_keep_position()

    def _sync_stroll_controls(self) -> None:
        if self.stroll.get():
            if not self.stroll_box.winfo_manager():
                self.stroll_box.pack(fill="x", after=self.stroll_row)
        else:
            self.stroll_box.pack_forget()

    def _on_stroll_toggle(self) -> None:
        if not self.stroll.get():
            # The plain route must stay hidden in immersive mode.
            self._walk_preview = False
            self._preview_run = None
        self._sync_stroll_controls()
        self._rebuild_anim_keep_position()

    def _on_new_walk(self) -> None:
        """Re-roll the walk for the same run and preview all five checkpoints."""
        if self.mode != "immersive" or self.run is None:
            return
        if self.immersive_session is None:
            return
        full = self.immersive_session.full_snapshot()
        if full is None:
            return
        if not self.stroll.get():
            self.stroll.set(True)
            self._sync_stroll_controls()
        self._stop_animation()
        self._stroll_seed = random.randrange(1, 1 << 31)
        self._preview_run = full
        self._walk_preview = True
        self._sim_time = 0.0
        self._refresh_anim_path()
        self._update_pace_ui()
        self._update_trail()
        self._draw_pace_marker()
        self._sync_side()
        self._redraw()

    def _on_period_slide(self, value: int) -> None:
        self.gps_period_sec = int(value)
        self.period_val_lbl.configure(text=f"{self.gps_period_sec} s")
        self._rebuild_anim_keep_position()

    def _on_angle_slide(self, value: int) -> None:
        self.heading_max_deg = int(value)
        self.angle_val_lbl.configure(text=f"±{self.heading_max_deg}°")
        self._rebuild_anim_keep_position()

    def _rebuild_anim_keep_position(self) -> None:
        """Regenerate the (possibly strolled) path without losing progress."""
        if self.mode != "immersive":
            return
        old_time = self._anim_total_time
        frac = (self._sim_time / old_time) if old_time > 0.0 else 0.0
        frac = min(1.0, max(0.0, frac))
        self._refresh_anim_path()
        self._sim_time = frac * self._anim_total_time
        self._update_pace_ui()
        self._update_trail()
        self._draw_pace_marker()

    def _stop_animation(self) -> None:
        if self._anim_after is not None:
            try:
                self.root.after_cancel(self._anim_after)
            except Exception:
                pass
            self._anim_after = None
        self._playing = False

    def _view_run(self) -> PointRun | None:
        """Run used for drawing: the full preview while New walk is showing."""
        if self._walk_preview and self._preview_run is not None:
            return self._preview_run
        return self.run

    def _active_track(self):
        """Full route used for drawing/playback in the current mode: (points, distance, areas)."""
        if self.mode == "route":
            if not self.plan:
                return None
            return self.plan.path, self.plan.total_m, self.plan.areas_used
        run = self._view_run()
        if not run:
            return None
        return run.path, run.total_m, run.areas_used

    def _stroll_active(self) -> bool:
        return self.mode == "immersive" and self.stroll.get() and self.run is not None

    def _refresh_anim_path(self) -> None:
        """Rebuild playback samples from the current full route (immersive only)."""
        self._anim_pts = []
        self._anim_cum = []
        self._anim_total = 0.0
        self._anim_total_time = 0.0

        if self.mode != "immersive":
            return
        track = self._active_track()
        if not track:
            return
        pts, total_m, _areas = track
        clean = [pts[0]]
        for p in pts[1:]:
            if math.dist(clean[-1], p) > 1e-9:
                clean.append(p)
        if self._stroll_active():
            run = self._view_run() or self.run
            anchors = list(run.points_xy)
            if run.start_xy is not None:
                anchors.append(run.start_xy)
            stroll = build_stroll_path(
                clean,
                pace_sec=self.pace_seconds,
                period_sec=self.gps_period_sec,
                max_deg=self.heading_max_deg,
                seed=self._stroll_seed,
                anchors=anchors,
            )
            clean = list(stroll.points)
        self._anim_pts = clean
        cum = [0.0]
        for a, b in zip(clean, clean[1:]):
            cum.append(cum[-1] + math.dist(a, b))
        self._anim_cum = cum
        self._anim_total = cum[-1] if cum else 0.0
        if self._anim_total > 0.0:
            base_total = total_m if total_m > 0.0 else self._anim_total
            self._anim_total_time = (
                base_total / 1000.0 * self.pace_seconds
            )

    def _on_route_changed(self) -> None:
        self._stop_animation()
        self._walk_preview = False
        self._preview_run = None
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
        if (self._closing or self.mode != "immersive"
                or not self._anim_pts or self._anim_total_time <= 0.0):
            return
        if self._playing:
            self._stop_animation()
            self._update_pace_ui()
            return
        if self._walk_preview:
            # Play turns the preview back into the immersive reveal: hide the
            # unvisited checkpoints, clear the trace, and start from the top.
            self._walk_preview = False
            self._preview_run = None
            if self.immersive_session is not None:
                self.run = self.immersive_session.restart()
            self._sim_time = 0.0
            self._refresh_anim_path()
            self._sync_side()
            self._redraw()
        if self._sim_time >= self._anim_total_time - 1e-9:
            self._sim_time = 0.0
        self._playing = True
        self._last_tick = time.monotonic()
        self._update_pace_ui()
        self._update_trail()
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
                self._update_trail()
                self._draw_pace_marker()
                self._anim_after = self.root.after(33, self._tick)
                return
        self._update_pace_ui()
        self._update_trail()
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
        if self.mode == "route":
            self._update_route_readout()
            return

        track = self._active_track()
        total_m = track[1] if track else None
        total_t = self._anim_total_time
        has_route = total_m is not None and self._anim_total > 0.0
        can_walk = self.run is not None and self._anim_total > 0.0
        self.walk_btn.configure(
            state="normal" if can_walk else "disabled",
            fg="#39414d" if can_walk else "#9aa5b4",
            cursor="hand2" if can_walk else "arrow",
        )

        if self._walk_preview and self._preview_run is not None and has_route:
            self.total_lbl.configure(text="Preview", fg=C_ROUTE)
            self.mileage_sub_lbl.configure(
                text=f"{len(self._preview_run.points_xy)} checkpoints · "
                     f"Total {fmt_len(total_m)}"
            )
            self.pace_dist_lbl.configure(text="All five checkpoints")
            self.pace_time_lbl.configure(text=fmt_duration(total_t))
            self._draw_pace_bar(0.0)
            self.play_btn.configure(
                state="normal", bg=C_PACE, fg="#ffffff", text="Play"
            )
            return

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

    def _update_route_readout(self) -> None:
        plan = self.plan
        if plan is not None:
            self.total_lbl.configure(text=fmt_len(plan.total_m), fg=C_ROUTE)
            self.mileage_sub_lbl.configure(
                text=f"{len(plan.points_xy)} points · {len(plan.legs)} legs"
            )
        else:
            self.total_lbl.configure(text="—", fg=C_MUTED)
            self.mileage_sub_lbl.configure(
                text=f"{len(self.points)} / {MAX_POINTS} points"
            )
        self.pace_dist_lbl.configure(text="—")
        self.pace_time_lbl.configure(text="—")
        self.play_btn.configure(
            state="disabled", bg="#e3e8ef", fg="#9aa5b4", text="Play"
        )
        self._draw_pace_bar(0.0)

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

    # ---- Route planning --------------------------------------------------
    def set_points(self, pts: list[tuple[float, float]]) -> None:
        """Set route points programmatically and plan immediately (used by --demo)."""
        self.mode = "route"
        self.points = list(pts)[:MAX_POINTS]
        self.plan = None
        self.plan_error = None
        self._start_plan()

    def _start_plan(self) -> None:
        if self.mode != "route":
            return
        self._stop_animation()
        self._sim_time = 0.0
        if len(self.points) < 2:
            self.plan = None
            self.plan_error = "Select at least two points before starting."
            self._on_route_changed()
            self._sync_side()
            self._redraw()
            return
        try:
            plan = self.cmap.plan_point_run(
                points=list(self.points), snap_radius=self.snap_radius_m,
            )
        except Exception as exc:
            plan = None
            self.plan_error = f"Planning failed: {exc}"
        if plan is None:
            self.plan = None
            if not self.plan_error:
                self.plan_error = "No connected route could be planned for these points."
        else:
            self.plan = plan
            self.plan_error = None
        self._on_route_changed()
        self._sync_side()
        self._redraw()

    def _random_route_points(self) -> None:
        self._stop_animation()
        self._sim_time = 0.0
        try:
            plan = self.cmap.plan_point_run(
                5, min_sep_m=60.0, snap_radius=self.snap_radius_m,
            )
        except Exception as exc:
            plan = None
            self.plan_error = f"Generation failed: {exc}"
        if plan is None:
            if not self.plan_error:
                self.plan_error = "Could not generate random points. Please try again."
            self._sync_side()
            self._redraw()
            return
        self.points = [tuple(p) for p in plan.points_lonlat]
        self.plan = plan
        self.plan_error = None
        self._on_route_changed()
        self._sync_side()
        self._fit_view()

    def _on_snap_slide(self, value: int) -> None:
        self.snap_radius_m = int(value)
        self.snap_val_lbl.configure(text=f"{self.snap_radius_m} m")
        if self.mode == "route" and self.plan is not None:
            self._start_plan()
        else:
            self._redraw()

    def _on_arrow_slide(self, value: int) -> None:
        self.arrow_spacing_m = int(value)
        self.arrow_val_lbl.configure(text=f"{self.arrow_spacing_m} m")
        if self.mode == "route":
            self._redraw()

    def undo(self) -> None:
        if self.mode != "route" or not self.points:
            return
        self.points.pop()
        self.plan = None
        self.plan_error = None
        self._on_route_changed()
        self._sync_side()
        self._redraw()

    def clear(self) -> None:
        if self.mode == "immersive":
            self._restart_immersive()
            return
        if not self.points and self.plan is None and not self.plan_error:
            return
        self.points = []
        self.plan = None
        self.plan_error = None
        self._on_route_changed()
        self._sync_side()
        self._redraw()

    def _restart_immersive(self) -> None:
        """Clear the current immersive run and wait for a new start point."""
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
        if self.mode == "route":
            for pt in self.points:
                ai = self.cmap.area_at(*self.cmap.proj.xy(*pt))
                if ai is not None:
                    active.add(ai)
        return active

    # ---- Sidebar sync ----------------------------------------------------
    def _sync_side(self) -> None:
        self._sync_mode_visibility()
        if self.mode == "route":
            self._sync_route_side()
        else:
            self._sync_immersive_side()
        self._update_mode_buttons()
        self._update_pace_ui()

    def _sync_mode_visibility(self) -> None:
        route = self.mode == "route"
        if route:
            self.immersive_box.pack_forget()
            if not self.route_box.winfo_manager():
                self.route_box.pack(fill="x")
            self.pace_box.pack_forget()
            self.clear_btn.grid()
        else:
            self.route_box.pack_forget()
            if not self.immersive_box.winfo_manager():
                self.immersive_box.pack(fill="x")
            if not self.pace_box.winfo_manager():
                # Keep the separator below the pace block in both modes.
                self.pace_box.pack(fill="x", before=self.sep_after_pace)
            self.clear_btn.grid_remove()

    def _sync_point_list(self) -> None:
        for child in self.point_list_frame.winfo_children():
            child.destroy()
        if not self.points:
            tk.Label(
                self.point_list_frame, text="No points yet.",
                font=(FONT_UI, 8), fg=C_MUTED, bg=C_SIDEBAR, anchor="w",
            ).pack(fill="x", pady=self.px(1))
            return
        for idx, pt in enumerate(self.points, 1):
            row = tk.Frame(self.point_list_frame, bg=C_SIDEBAR)
            row.pack(fill="x", pady=self.px(1))
            tk.Label(
                row, text=str(idx), width=3, font=(FONT_MONO, 8, "bold"),
                fg=C_TEMP, bg=C_SIDEBAR, anchor="w",
            ).pack(side="left")
            tk.Label(
                row, text=fmt_lonlat(pt), font=(FONT_MONO, 8),
                fg=C_TEXT, bg=C_SIDEBAR, anchor="w",
            ).pack(side="left", fill="x", expand=True)

    def _set_leg_row(self, index: int, name: str, value: str) -> None:
        self.leg_name_lbls[index].configure(text=name)
        self.leg_val_lbls[index].configure(text=value)
        self.leg_name_lbls[index].grid()
        self.leg_val_lbls[index].grid()

    def _clear_leg_rows(self) -> None:
        for i in range(len(self.leg_name_lbls)):
            self.leg_name_lbls[i].grid_remove()
            self.leg_val_lbls[i].grid_remove()

    def _sync_route_side(self) -> None:
        n = len(self.points)
        if self.plan_error:
            status, color = self.plan_error, "#b3261e"
        elif n == 0:
            status, color = (
                "Click the map to add route points; two or more are needed.", C_STATUS_FG
            )
        elif n == 1:
            status, color = "1 point selected. Add one more, then press Start.", C_STATUS_FG
        elif self.plan is None:
            status, color = (
                f"{n} points selected. Press Start (Enter) to plan the route.",
                C_STATUS_FG,
            )
        else:
            status = (f"{n} points planned, {fmt_len(self.plan.total_m)} total. "
                      f"Direction arrows every {self.arrow_spacing_m} m.")
            if self.plan.areas_used:
                names = ", ".join(
                    self.cmap.areas[i].desc for i in sorted(self.plan.areas_used)
                )
                status += f" Area highlighted: {names}."
            color = C_STATUS_FG
        self.status_lbl.configure(text=status, fg=color)

        self.point_count_lbl.configure(text=f"{n} / {MAX_POINTS}")
        self._sync_point_list()

        ready = n >= 2
        self.start_btn.configure(
            state="normal" if ready else "disabled",
            bg=C_ROUTE if ready else "#e3e8ef",
            fg="#ffffff" if ready else "#9aa5b4",
            cursor="hand2" if ready else "arrow",
        )
        self.action_btn.configure(text="Undo", command=self.undo)
        self.action_btn.state(["!disabled"] if n else ["disabled"])
        self.clear_btn.configure(text="Clear", command=self.clear)
        self.clear_btn.state(
            ["!disabled"] if (n or self.plan or self.plan_error) else ["disabled"]
        )

        self.snap_val_lbl.configure(text=f"{self.snap_radius_m} m")
        self.arrow_val_lbl.configure(text=f"{self.arrow_spacing_m} m")

        self.mileage_title.configure(
            text=f"Distance ({n} points)" if n else "Distance"
        )
        self._clear_leg_rows()
        if self.plan is not None:
            for i, leg in enumerate(self.plan.legs):
                self._set_leg_row(i, f"{i + 1}→{i + 2}", fmt_len(leg.total_m))
        else:
            for i in range(max(0, n - 1)):
                self._set_leg_row(i, f"{i + 1}→{i + 2}", "—")

        self.hint_lbl.configure(
            text=("Click to add points · Start or Enter to plan · "
                  "Wheel to zoom · Drag outside fence to pan")
        )

    def _sync_immersive_side(self) -> None:
        run = self._view_run()
        if self.run_error:
            status, color = self.run_error, "#b3261e"
        elif run is None:
            status, color = (
                "Immersive: click the map to choose a start point (not a checkpoint).",
                C_STATUS_FG,
            )
        elif self._walk_preview:
            status = (f"Preview: {len(run.points_xy)} checkpoints, "
                      f"{fmt_len(run.total_m)} total. Press Play to run them "
                      f"one by one.")
            color = C_STATUS_FG
        else:
            status, color = self._immersive_status(run), C_STATUS_FG
        self.status_lbl.configure(text=status, fg=color)

        active = self.run is not None or self.run_error or self.immersive_session is not None
        self.action_btn.configure(text="New start", command=self._restart_immersive)
        self.action_btn.state(["!disabled"] if active else ["disabled"])

        self._sync_immersive_mileage(run)

        if self._walk_preview:
            hint = ("Preview: all five checkpoints and the full walk path · "
                    "Press Play to run them one by one")
        elif run is None:
            hint = ("Click the map for the start · Wheel to zoom · "
                    "Drag outside fence to pan")
        else:
            hint = ("Press Play to run · Green line is the live trajectory · "
                    "Wheel to zoom")
        self.hint_lbl.configure(text=hint)

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

    def _sync_immersive_mileage(self, run: PointRun | None) -> None:
        if run is None:
            self.mileage_title.configure(text="Distance (immersive)")
            self._clear_leg_rows()
            self._set_leg_row(0, "Start→1", "—")
            return

        cap_rows = run.limit_state in ("transit", "track")
        self.mileage_title.configure(
            text="Distance (immersive + cap)" if cap_rows else "Distance (immersive)"
        )
        self._clear_leg_rows()
        row = 0
        for i in range(len(run.legs)):
            self._set_leg_row(
                row, "Start→1" if i == 0 else f"{i}→{i + 1}",
                fmt_len(run.leg_m[i]),
            )
            row += 1
        if cap_rows and row < len(self.leg_name_lbls):
            self._set_leg_row(
                row, "5→Track + laps",
                f"{fmt_len(run.transit_m)} + {fmt_len(run.track_m)}",
            )

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
        if mode not in ("route", "immersive") or mode == self.mode:
            return
        self.mode = mode
        self._stop_animation()
        self._sim_time = 0.0
        self._update_mode_buttons()
        self.canvas.configure(cursor=self._mode_cursor())
        self._on_route_changed()
        self._sync_side()
        self._redraw()

    # ---- Immersive options ----------------------------------------------
    def _on_reduce_backtrack_toggle(self) -> None:
        if self.mode != "immersive":
            return
        if self.immersive_session is not None:
            self.immersive_session.reduce_backtrack = self.reduce_backtrack.get()
        self._sync_side()

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
        if self.mode != "immersive":
            return
        if self.immersive_session is not None:
            self.immersive_session.daily_cap_m = self._daily_cap_m()
        self._sync_side()

    def _on_run_limit_toggle(self) -> None:
        if self.mode != "immersive":
            return
        if self.immersive_session is not None:
            self.immersive_session.run_limit = self.run_limit.get()
            self.immersive_session.daily_cap_m = self._daily_cap_m()
        self._sync_side()
