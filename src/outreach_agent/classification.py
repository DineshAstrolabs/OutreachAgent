"""Map total score to classification + routing. Thresholds are tunable.

Calibration plan: run on first 20-30 real leads, validate conversion rates
against expected ranges, adjust thresholds here.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import Classification


@dataclass(frozen=True)
class Routing:
    classification: Classification
    recommended_action: str
    outreach_approach: str
    timeline: str
    expected_conversion: str


# (min_score, Routing) — evaluated top-to-bottom.
_TIERS: list[tuple[int, Routing]] = [
    (80, Routing(
        classification=Classification.CRITICAL,
        recommended_action="Escalate to Senior AE immediately",
        outreach_approach=(
            "Your KSA operation is scaling fast and we can see gaps. "
            "We resolve compliance issues in days, not weeks."
        ),
        timeline="Same day",
        expected_conversion="30-40%",
    )),
    (60, Routing(
        classification=Classification.HOT,
        recommended_action="Priority outreach — assign AE",
        outreach_approach="Compliance audit + growth support pitch",
        timeline="24 hours",
        expected_conversion="20-30%",
    )),
    (35, Routing(
        classification=Classification.WARM,
        recommended_action="Standard outreach",
        outreach_approach="Focus on growth, we handle the paperwork",
        timeline="1 week",
        expected_conversion="10-20%",
    )),
    (15, Routing(
        classification=Classification.COOL,
        recommended_action="Add to nurture sequence",
        outreach_approach=(
            "Educational drip: Saudization changes, compliance deadlines, portal tips"
        ),
        timeline="Monthly",
        expected_conversion="5-10%",
    )),
]

_COLD = Routing(
    classification=Classification.COLD,
    recommended_action="Park",
    outreach_approach="Newsletter only",
    timeline="Quarterly review",
    expected_conversion="<5%",
)


def classify(total_score: int) -> Routing:
    """Cascade through the 5 tiers. Returns the first match."""
    for threshold, routing in _TIERS:
        if total_score >= threshold:
            return routing
    return _COLD
