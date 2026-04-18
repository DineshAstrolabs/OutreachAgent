"""Tests for the batch-CSV hint parser + merge logic."""

from datetime import date

from outreach_agent.csv_hint import merge_hint_into_mc, row_to_mc_hint
from outreach_agent.models import CRStatus, EntityType, MCData


def _sample_row() -> dict[str, str]:
    # Uses the exact headers the user ships in their sheet.
    return {
        "IR Number":          "42",
        "Company Name (EN)":  "OFFTEC Arabia Company For IT",
        "Company Name (AR)":  "",
        "Legal Entity":       "LLC Saudi",
        "Capital":            "300,000",
        "Registration Status": "Active",
        "Registration Number": "1010379427",
        "Unified Number":     "7001756902",
        "License Number":     "",
        "Registration Type":  "Company",
        "Registration Date":  "2013-06-12",
        "Expiry Date":        "2026-06-11",
        "Location":           "Riyadh",
        "Phone":              "0110000000",
        "Mobile":             "",
        "Shareholders":       "",
        "Activities Count":   "8",
        "Activities":         "Engineering consultations",
        "First Seen":         "",
        "Last Updated":       "",
    }


def test_row_to_mc_hint_reads_every_alias():
    hint = row_to_mc_hint(_sample_row())
    assert hint is not None
    assert hint.unified_number == "7001756902"
    assert hint.company_legal_name == "OFFTEC Arabia Company For IT"
    assert hint.cr_status == CRStatus.ACTIVE
    assert hint.entity_type == EntityType.LLC_SAUDI
    assert hint.cr_number == "1010379427"
    assert hint.business_type_raw == "Company"
    assert hint.cr_issue_date == date(2013, 6, 12)
    assert hint.cr_expiry_date == date(2026, 6, 11)
    assert hint.registered_capital_sar == 300_000
    assert hint.phone == "0110000000"
    assert hint.city == "Riyadh"
    assert hint.activities == "Engineering consultations"


def test_row_to_mc_hint_returns_none_without_unified_number():
    assert row_to_mc_hint({"Company Name (EN)": "x"}) is None


def test_merge_mc_wins_when_populated():
    mc = MCData(
        unified_number="7001756902",
        company_legal_name="MC Official Name",
        cr_status=CRStatus.ACTIVE,
        entity_type=EntityType.LLC_FOREIGN,
        cr_number="9999999999",
        registered_capital_sar=500_000,
    )
    hint = row_to_mc_hint(_sample_row())
    merged = merge_hint_into_mc(mc, hint)

    assert merged.company_legal_name == "MC Official Name"
    assert merged.cr_number == "9999999999"
    assert merged.registered_capital_sar == 500_000
    assert merged.entity_type == EntityType.LLC_FOREIGN


def test_merge_backfills_missing_fields_from_hint():
    mc = MCData(
        unified_number="7001756902",
        company_legal_name="MC Name",
        cr_status=CRStatus.ACTIVE,
        entity_type=EntityType.LLC_FOREIGN,
    )
    hint = row_to_mc_hint(_sample_row())
    merged = merge_hint_into_mc(mc, hint)

    assert merged.company_legal_name == "MC Name"  # MC wins
    assert merged.cr_number == "1010379427"        # filled from hint
    assert merged.phone == "0110000000"
    assert merged.city == "Riyadh"
    assert merged.registered_capital_sar == 300_000
    assert merged.cr_issue_date == date(2013, 6, 12)


def test_merge_hint_replaces_not_found():
    mc = MCData(
        unified_number="7001756902",
        company_legal_name="",
        cr_status=CRStatus.NOT_FOUND,
        entity_type=EntityType.UNKNOWN,
    )
    hint = row_to_mc_hint(_sample_row())
    merged = merge_hint_into_mc(mc, hint)

    assert merged.cr_status == CRStatus.ACTIVE
    assert merged.company_legal_name == "OFFTEC Arabia Company For IT"
    assert merged.cr_number == "1010379427"


def test_merge_with_no_hint_is_noop():
    mc = MCData(
        unified_number="7001756902",
        company_legal_name="X",
        cr_status=CRStatus.ACTIVE,
        entity_type=EntityType.LLC_SAUDI,
    )
    assert merge_hint_into_mc(mc, None) is mc
