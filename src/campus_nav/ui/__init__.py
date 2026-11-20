# -*- coding: utf-8 -*-
"""Tkinter user interface."""

from __future__ import annotations

__all__ = ["CampusNavApp"]


def __getattr__(name: str):
    """Lazy import so headless paths such as ``--check`` do not require tkinter."""
    if name == "CampusNavApp":
        from .app import CampusNavApp

        return CampusNavApp
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
