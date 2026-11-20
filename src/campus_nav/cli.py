# -*- coding: utf-8 -*-
"""Command-line entry point."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .checks import run_check
from .config import default_fence_path, default_map_path
from .geo.fence import load_fences
from .routing.campus_map import CampusMap


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="GDPU ZS Navigation")
    parser.add_argument(
        "--map",
        default=None,
        help="road GeoJSON path (default: campus_nav/data/map_gcj02.geojson)",
    )
    parser.add_argument(
        "--fence",
        default=None,
        help="fence GeoJSON path (default: campus_nav/data/fence_gcj02.geojson)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="run road/algorithm self-tests only; do not open a window",
    )
    parser.add_argument(
        "--demo",
        metavar="LON1,LAT1,LON2,LAT2",
        help="place points A and B automatically at startup (demo/screenshots)",
    )
    return parser


def parse_demo(text: str) -> list[tuple[float, float]]:
    values = [float(s) for s in text.split(",")]
    if len(values) != 4:
        raise ValueError("--demo requires 4 numbers")
    return [(values[0], values[1]), (values[2], values[3])]


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    path = Path(args.map) if args.map else default_map_path()
    if not path.exists():
        print(f"Map file not found: {path}", file=sys.stderr)
        return 2
    try:
        cmap = CampusMap(path)
    except Exception as exc:
        print(f"Failed to load map: {exc}", file=sys.stderr)
        return 2

    if args.check:
        return 0 if run_check(cmap) else 1

    if args.fence:
        fence_path = Path(args.fence)
        if not fence_path.exists():
            print(f"Fence file not found: {fence_path}", file=sys.stderr)
            return 2
    else:
        fence_path = default_fence_path()
    fences = load_fences(fence_path)

    demo = None
    if args.demo:
        try:
            demo = parse_demo(args.demo)
        except ValueError:
            parser.error("--demo requires 4 numbers: LON1,LAT1,LON2,LAT2")

    # Import the GUI lazily; --check does not need to create a Tk window.
    import tkinter as tk

    from .ui.app import CampusNavApp
    from .ui.dpi import enable_dpi_awareness

    enable_dpi_awareness()
    root = tk.Tk()
    app = CampusNavApp(root, cmap, fences=fences)
    if demo:
        root.after(120, lambda: app.set_points(demo))
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
