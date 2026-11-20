# -*- coding: utf-8 -*-
"""Windows high-DPI adaptation."""

from __future__ import annotations

import sys


def enable_dpi_awareness() -> None:
    """Draw in physical pixels so Windows scaling cannot push the window off-screen."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)   # per-monitor aware
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass
