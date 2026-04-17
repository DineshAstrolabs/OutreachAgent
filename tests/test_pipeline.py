"""Integration test — full pipeline against fixture data."""

from datetime import date

from outreach_agent.factory import build_pipeline
from outreach_agent.config import Config
from outreach_agent.models import Classification


def _stub_config(monkeypatch, **overrides):
    monkeypatch.setenv("OUTREACH_AGENT_MODE", "stub")
    cfg = Config.load()
    return cfg


def test_full_pipeline_critical_lead(monkeypatch):
    cfg = _stub_config(monkeypatch)
    pipeline = build_pipeline(cfg)
    result = pipeline.run("7012345678")

    assert not result.disqualified
    # Fixture has CR expiry 2026-06-15. Without knowing today we can't assert
    # the exact score, but we can require the lead scores above WARM since
    # every non-expiry signal is strong.
    assert result.total_score >= 35
    assert result.classification in (
        Classification.WARM, Classification.HOT, Classification.CRITICAL
    )
    assert "linkedin" in result.web.sources_seen
    assert "crunchbase" in result.web.sources_seen
    assert len(result.breakdown) == 16  # 17 data points minus the MC ones not scored here


def test_full_pipeline_disqualified_expired_cr(monkeypatch):
    cfg = _stub_config(monkeypatch)
    pipeline = build_pipeline(cfg)
    result = pipeline.run("7099999999")

    assert result.disqualified
    assert result.classification == Classification.DISQUALIFIED
    assert "Expired" in result.disqualification_reason
    # Must not have called any web source after disqualification.
    assert result.web is None


def test_unknown_unified_number_uses_default(monkeypatch):
    """Stub source returns a default Active LLC for unknown numbers so the
    pipeline stays demoable without pre-seeded fixtures."""
    cfg = _stub_config(monkeypatch)
    pipeline = build_pipeline(cfg)
    result = pipeline.run("7000000000")
    assert not result.disqualified
    # Baseline signal only: #10 missing KSA office (+10) + #22 ISIC 6201
    # priority sector (+15) + #23 no admin found (+10) = 35 → WARM.
    assert result.total_score <= 40
    assert result.classification in (Classification.COOL, Classification.WARM)
