"""Hard gate tests. Disqualification must happen before any scraping."""

from outreach_agent.disqualification import apply_gates
from outreach_agent.models import CRStatus, EntityType, MCData


def _mc(status=CRStatus.ACTIVE, entity=EntityType.LLC_FOREIGN):
    return MCData(
        unified_number="7000000001",
        company_legal_name="Test Co",
        cr_status=status,
        entity_type=entity,
    )


def test_active_foreign_llc_passes():
    r = apply_gates(_mc())
    assert not r.disqualified
    assert not r.deprioritized


def test_expired_cr_disqualified():
    r = apply_gates(_mc(status=CRStatus.EXPIRED))
    assert r.disqualified
    assert "Expired" in r.disqualification_reason


def test_cancelled_cr_disqualified():
    r = apply_gates(_mc(status=CRStatus.CANCELLED))
    assert r.disqualified


def test_struck_off_cr_disqualified():
    r = apply_gates(_mc(status=CRStatus.STRUCK_OFF))
    assert r.disqualified


def test_not_found_cr_disqualified():
    r = apply_gates(_mc(status=CRStatus.NOT_FOUND))
    assert r.disqualified


def test_government_entity_disqualified():
    r = apply_gates(_mc(entity=EntityType.GOVERNMENT))
    assert r.disqualified
    assert "Government" in r.disqualification_reason


def test_semi_government_entity_disqualified():
    r = apply_gates(_mc(entity=EntityType.SEMI_GOVERNMENT))
    assert r.disqualified


def test_llc_saudi_deprioritized_but_not_disqualified():
    r = apply_gates(_mc(entity=EntityType.LLC_SAUDI))
    assert not r.disqualified
    assert r.deprioritized


def test_disqualification_checked_before_deprioritization():
    """Expired Saudi LLC → disqualified (status check wins over entity check)."""
    r = apply_gates(_mc(status=CRStatus.EXPIRED, entity=EntityType.LLC_SAUDI))
    assert r.disqualified
    assert not r.deprioritized
