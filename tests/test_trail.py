# -*- coding: utf-8 -*-
"""Live trajectory: the green trail must grow during playback, not only on redraw."""

import pytest

tk = pytest.importorskip("tkinter")

from campus_nav.ui.app import CampusNavApp  # noqa: E402


@pytest.fixture
def app(cmap):
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("Tk display is not available")
    root.withdraw()                 # never show a window in tests
    root.geometry("1200x800")
    instance = CampusNavApp(root, cmap)
    root.update_idletasks()
    instance._set_mode("immersive")
    instance._fit_view()            # withdrawn windows receive no <Configure>
    yield instance
    root.destroy()


def _start_run(app, cmap):
    start = cmap.proj.xy(113.41518, 22.47824)
    app._start_immersive(start)
    assert app.run is not None
    app._redraw()


def _trail_length(app) -> int:
    items = app._trail_items
    if not items:
        return 0
    return len(app.canvas.coords(items[1])) // 2


def test_trail_is_empty_before_the_run_starts(app, cmap):
    _start_run(app, cmap)
    assert app._trail_items is None
    assert _trail_length(app) == 0


def test_trail_grows_with_playback_without_a_redraw(app, cmap):
    _start_run(app, cmap)
    app._sim_time = app._anim_total_time * 0.25
    app._update_trail()
    first = _trail_length(app)
    assert first >= 2

    # Playback only advances the clock; no zoom/pan/redraw happens here.
    app._sim_time = app._anim_total_time * 0.75
    app._update_trail()
    assert _trail_length(app) >= first

    head_before = app.canvas.coords(app._trail_items[1])[-2:]
    app._sim_time = app._anim_total_time
    app._update_trail()
    head_after = app.canvas.coords(app._trail_items[1])[-2:]
    assert head_after != head_before


def test_trail_reuses_one_canvas_item_pair(app, cmap):
    _start_run(app, cmap)
    app._sim_time = app._anim_total_time * 0.4
    app._update_trail()
    assert len(app.canvas.find_withtag("trail")) == 2

    for frac in (0.5, 0.6, 0.7):
        app._sim_time = app._anim_total_time * frac
        app._update_trail()
    assert len(app.canvas.find_withtag("trail")) == 2


def test_redraw_recreates_the_trail_once_at_the_current_position(app, cmap):
    _start_run(app, cmap)
    app._sim_time = app._anim_total_time * 0.5
    app._update_trail()
    app._redraw()
    assert len(app.canvas.find_withtag("trail")) == 2
    assert _trail_length(app) >= 2


def test_new_walk_rerolls_and_previews_the_same_checkpoints(app, cmap):
    _start_run(app, cmap)
    assert app.walk_btn.cget("state") == "normal"
    old_seed = app._stroll_seed
    old_points = list(app._anim_pts)
    checkpoints = list(app.run.points_xy)

    app._on_new_walk()
    assert app.stroll.get()
    assert app._stroll_seed != old_seed
    assert app._walk_preview
    assert app._anim_pts != old_points
    assert list(app.run.points_xy) == checkpoints
    # The preview draws the whole walk and hides the pace arrow.
    app._update_trail()
    assert _trail_length(app) == len(app._anim_pts)
    assert not app.canvas.find_withtag("pace_arrow")

    # A second click gives another walk for the same checkpoints.
    second = list(app._anim_pts)
    app._on_new_walk()
    assert app._anim_pts != second

    # Play drops the preview and starts from the beginning.
    app._toggle_play()
    assert not app._walk_preview
    assert len(app._trail_points()) == 1
    app._stop_animation()


def test_trail_stays_below_the_markers(app, cmap):
    _start_run(app, cmap)
    app._sim_time = app._anim_total_time * 0.5
    app._update_trail()
    app._draw_pace_marker()
    order = list(app.canvas.find_all())
    trail = order.index(app._trail_items[1])
    arrow = order.index(app.canvas.find_withtag("pace_arrow")[0])
    endpoints = app.canvas.find_withtag("endpoint")
    assert order.index(endpoints[0]) > trail
    assert arrow > trail
