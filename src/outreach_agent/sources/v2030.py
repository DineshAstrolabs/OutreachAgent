"""Vision 2030 tier classification from ISIC business activity code.

Covers #22 V2030 alignment. Pure logic — no external fetch needed once we
have the ISIC code from MC.

Reference: https://www.vision2030.gov.sa/ — priority sectors include tech,
fintech, tourism, healthcare, entertainment. ISIC ranges below are a
conservative first pass; should be reviewed with the sales team and
sharpened during calibration (see spec discussion items).
"""

from __future__ import annotations

from ..models import V2030Tier


# ISIC Rev 4 two-digit section prefixes
_PRIORITY_PREFIXES = {
    "58",  # Publishing / software
    "59",  # Media
    "61",  # Telecoms
    "62",  # Computer programming / IT services
    "63",  # Information services
    "64",  # Financial services (fintech)
    "65",  # Insurance
    "66",  # Auxiliary financial
    "72",  # Scientific R&D
    "79",  # Travel / tourism
    "86",  # Human health
    "90",  # Creative / arts / entertainment
    "93",  # Sports / amusement
}

_ADJACENT_PREFIXES = {
    "46",  # Wholesale
    "47",  # Retail
    "55",  # Accommodation
    "56",  # Food service
    "70",  # Management consulting
    "71",  # Architecture / engineering
    "73",  # Advertising
    "74",  # Other professional
    "85",  # Education
}


def classify_isic(isic: str | None) -> V2030Tier:
    if not isic:
        return V2030Tier.NON_PRIORITY
    prefix = isic.strip()[:2]
    if prefix in _PRIORITY_PREFIXES:
        return V2030Tier.PRIORITY
    if prefix in _ADJACENT_PREFIXES:
        return V2030Tier.ADJACENT
    return V2030Tier.NON_PRIORITY
