"""Classification threshold tests — validates the tier boundaries."""

import pytest

from outreach_agent.classification import classify
from outreach_agent.models import Classification


@pytest.mark.parametrize("score,expected", [
    (225, Classification.CRITICAL),
    (100, Classification.CRITICAL),
    (80, Classification.CRITICAL),
    (79, Classification.HOT),
    (60, Classification.HOT),
    (59, Classification.WARM),
    (35, Classification.WARM),
    (34, Classification.COOL),
    (15, Classification.COOL),
    (14, Classification.COLD),
    (0, Classification.COLD),
])
def test_thresholds(score, expected):
    assert classify(score).classification == expected


def test_critical_routes_to_senior_ae():
    r = classify(95)
    assert "Senior AE" in r.recommended_action
    assert r.timeline == "Same day"


def test_cold_parked():
    r = classify(10)
    assert r.classification == Classification.COLD
    assert "Newsletter" in r.outreach_approach
