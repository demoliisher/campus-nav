# -*- coding: utf-8 -*-
"""Pace parsing and formatting."""

import pytest

from campus_nav.pacing import fmt_duration, parse_pace


@pytest.mark.parametrize(
    "text",
    ["5'00''", '5\'00"', "5:00", "5km", "5 km", "5'", "5"],
)
def test_parse_pace_accepts_common_forms(text):
    assert parse_pace(text) == 300


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("0'05''", 5),
        ("2'20''", 140),
        ("10'00''", 600),
        ("3:15", 195),
        ("4:30", 270),
    ],
)
def test_parse_pace_values(text, expected):
    assert parse_pace(text) == expected


@pytest.mark.parametrize("text", ["0'04''", "10'01''", "5'75''", "abc", ""])
def test_parse_pace_rejects_invalid(text):
    assert parse_pace(text) is None


def test_fmt_duration():
    assert fmt_duration(300) == "5'00''"
    assert fmt_duration(195) == "3'15''"
    assert fmt_duration(0) == "0'00''"
