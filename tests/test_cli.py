# -*- coding: utf-8 -*-
"""Command-line entry point."""

from campus_nav.cli import main, parse_demo


def test_check_mode(map_path):
    assert main(["--map", str(map_path), "--check"]) == 0


def test_missing_map(tmp_path, capsys):
    missing = tmp_path / "missing.geojson"
    assert main(["--map", str(missing), "--check"]) == 2
    assert "Map file not found" in capsys.readouterr().err


def test_parse_demo():
    pts = parse_demo("113.41518,22.47824,113.41333,22.47436")
    assert pts == [(113.41518, 22.47824), (113.41333, 22.47436)]


def test_missing_fence(map_path, tmp_path, capsys):
    missing = tmp_path / "missing.geojson"
    assert main(["--map", str(map_path), "--fence", str(missing)]) == 2
    assert "Fence file not found" in capsys.readouterr().err
