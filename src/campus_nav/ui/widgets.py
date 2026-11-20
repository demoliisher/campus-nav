# -*- coding: utf-8 -*-
"""Custom pace slider (rheostat style)."""

from __future__ import annotations

import tkinter as tk

from .theme import C_PACE, C_SIDEBAR


class PaceSlider(tk.Canvas):
    """Horizontal pace slider; the value is in seconds per kilometer.

    The track is a rounded line with the selected part filled in theme purple
    and a circular knob. Dragging, clicking the track, the mouse wheel, and
    the left/right arrow keys all adjust the value.
    """

    def __init__(self, master, *, min_sec: int, max_sec: int, value: int,
                 on_change, ui_scale: float = 1.0, **kwargs):
        self.min_sec = int(min_sec)
        self.max_sec = max(int(max_sec), self.min_sec + 1)
        self._value = self._clamp(value)
        self._on_change = on_change
        self._ui = float(ui_scale)
        self._dragging = False
        height = max(18, int(round(24 * self._ui)))
        super().__init__(
            master, height=height, bg=C_SIDEBAR, highlightthickness=0,
            bd=0, cursor="hand2", takefocus=1, **kwargs,
        )
        self.bind("<Configure>", lambda _e: self._draw())
        self.bind("<Button-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<MouseWheel>", self._on_wheel)
        self.bind("<Button-4>", lambda _e: self._step(-1))
        self.bind("<Button-5>", lambda _e: self._step(1))
        self.bind("<Left>", lambda _e: self._step(-1))
        self.bind("<Right>", lambda _e: self._step(1))
        self.bind("<Home>", lambda _e: self.set_value(self.min_sec))
        self.bind("<End>", lambda _e: self.set_value(self.max_sec))

    # ---- Public interface ------------------------------------------------
    def get(self) -> int:
        return self._value

    def set_value(self, seconds: int, notify: bool = True) -> None:
        value = self._clamp(seconds)
        if value == self._value:
            return
        self._value = value
        self._draw()
        if notify and self._on_change is not None:
            self._on_change(value)

    # ---- Geometry --------------------------------------------------------
    def _clamp(self, seconds: float) -> int:
        return max(self.min_sec, min(self.max_sec, int(round(seconds))))

    def _pad(self) -> float:
        return 9.0 * self._ui

    def _knob_radius(self) -> float:
        return 8.0 * self._ui

    def _track_width(self) -> float:
        return 6.0 * self._ui

    def _value_to_x(self, value: int) -> float:
        width = max(self.winfo_width(), 1)
        pad = self._pad()
        span = max(width - 2.0 * pad, 1.0)
        frac = (value - self.min_sec) / (self.max_sec - self.min_sec)
        return pad + span * frac

    def _x_to_value(self, x: float) -> int:
        width = max(self.winfo_width(), 1)
        pad = self._pad()
        span = max(width - 2.0 * pad, 1.0)
        frac = min(1.0, max(0.0, (x - pad) / span))
        return self._clamp(
            self.min_sec + frac * (self.max_sec - self.min_sec)
        )

    # ---- Drawing ---------------------------------------------------------
    def _draw(self) -> None:
        self.delete("all")
        width = self.winfo_width()
        height = max(self.winfo_height(), 1)
        if width <= 2:
            return
        pad = self._pad()
        y = height / 2.0
        knob_x = self._value_to_x(self._value)
        self.create_line(
            pad, y, width - pad, y,
            fill="#e9edf4", width=self._track_width(), capstyle=tk.ROUND,
        )
        if knob_x > pad + 0.5:
            self.create_line(
                pad, y, knob_x, y,
                fill=C_PACE, width=self._track_width(), capstyle=tk.ROUND,
            )
        radius = self._knob_radius()
        self.create_oval(
            knob_x - radius, y - radius,
            knob_x + radius, y + radius,
            fill="#ffffff", outline=C_PACE, width=2.4 * self._ui,
        )
        center = 2.6 * self._ui
        self.create_oval(
            knob_x - center, y - center,
            knob_x + center, y + center,
            fill=C_PACE, outline="",
        )

    # ---- Interaction -----------------------------------------------------
    def _on_press(self, event) -> None:
        self.focus_set()
        self._dragging = True
        self.set_value(self._x_to_value(event.x))

    def _on_drag(self, event) -> None:
        if self._dragging:
            self.set_value(self._x_to_value(event.x))

    def _on_release(self, _event) -> None:
        self._dragging = False

    def _on_wheel(self, event) -> None:
        if not event.delta:
            return
        step = 5 if abs(event.delta) >= 120 else 1
        self._step(step if event.delta > 0 else -step)

    def _step(self, delta: int) -> None:
        self.set_value(self._value + delta)
