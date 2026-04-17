"""Hard disqualification gates and deprioritization flags.

Applied BEFORE web scraping so we do not waste resources on companies that
will be disqualified anyway (Solution Principle: hard gates first).
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import CRStatus, EntityType, MCData


@dataclass
class GateResult:
    disqualified: bool
    disqualification_reason: str | None
    deprioritized: bool
    deprioritized_reason: str | None


GOVERNMENT_TYPES = {EntityType.GOVERNMENT, EntityType.SEMI_GOVERNMENT}
INACTIVE_STATUSES = {
    CRStatus.EXPIRED,
    CRStatus.CANCELLED,
    CRStatus.STRUCK_OFF,
    CRStatus.NOT_FOUND,
}


def apply_gates(mc: MCData) -> GateResult:
    """Apply the three hard/soft gates from the qualification matrix.

    Gates (in evaluation order):
      1. CR Status != Active         → DISQUALIFY (route to Setup services)
      2. Entity is Gov / Semi-Gov    → DISQUALIFY (not a Post-Setup target)
      3. Entity is 100% Saudi domestic → DEPRIORITIZE (continue scoring, flag)
    """
    if mc.cr_status in INACTIVE_STATUSES:
        return GateResult(
            disqualified=True,
            disqualification_reason=(
                f"CR Status is {mc.cr_status.value}. Company does not legally exist "
                "to the Saudi government. Route to Setup services if re-establishing."
            ),
            deprioritized=False,
            deprioritized_reason=None,
        )

    if mc.entity_type in GOVERNMENT_TYPES:
        return GateResult(
            disqualified=True,
            disqualification_reason=(
                f"Entity type is {mc.entity_type.value}. Government and semi-government "
                "entities manage compliance through internal channels."
            ),
            deprioritized=False,
            deprioritized_reason=None,
        )

    if mc.entity_type == EntityType.LLC_SAUDI:
        return GateResult(
            disqualified=False,
            disqualification_reason=None,
            deprioritized=True,
            deprioritized_reason=(
                "100% Saudi domestic entity. Typically has in-house knowledge of Saudi "
                "systems. Continue scoring but move to bottom of queue."
            ),
        )

    return GateResult(False, None, False, None)
