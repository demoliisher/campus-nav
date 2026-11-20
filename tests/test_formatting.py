# -*- coding: utf-8 -*-
"""Distance formatting."""

from campus_nav.formatting import fmt_len, fmt_walked


def test_fmt_len():
    assert fmt_len(0.0) == "0.0 m"
    assert fmt_len(8.5) == "8.5 m"
    assert fmt_len(840.0) == "840 m"
    assert fmt_len(1530.0) == "1.53 km"


def test_fmt_walked():
    assert fmt_walked(0.0) == "0 m"
    assert fmt_walked(0.04) == "0 m"
    assert fmt_walked(8.5) == "8.5 m"
    assert fmt_walked(840.0) == "840 m"
    assert fmt_walked(1530.0) == "1.53 km"
