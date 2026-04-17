"""Scoring engine tests — one per data point + edge cases + total."""

from datetime import date, timedelta

from outreach_agent.models import (
    BusinessModel,
    Champion,
    Champions,
    CompanyType,
    CRStatus,
    EntityType,
    MCData,
    V2030Tier,
    WebIntelligence,
)
from outreach_agent.scoring import score


TODAY = date(2026, 4, 17)


def _mc(expiry: date | None = None) -> MCData:
    return MCData(
        unified_number="7000000001",
        company_legal_name="Test Co",
        cr_status=CRStatus.ACTIVE,
        entity_type=EntityType.LLC_FOREIGN,
        cr_expiry_date=expiry,
    )


def _breakdown(mc, web, champs):
    return {line.data_point_id: line for line in score(mc, web, champs, today=TODAY)}


# ---------- #3 CR Expiry ----------

def test_expiry_within_90d_is_20():
    b = _breakdown(_mc(TODAY + timedelta(days=60)), WebIntelligence(), Champions())
    assert b[3].points == 20


def test_expiry_within_180d_is_10():
    b = _breakdown(_mc(TODAY + timedelta(days=150)), WebIntelligence(), Champions())
    assert b[3].points == 10


def test_expiry_beyond_180d_is_0():
    b = _breakdown(_mc(TODAY + timedelta(days=365)), WebIntelligence(), Champions())
    assert b[3].points == 0


def test_expiry_boundary_90_is_included():
    b = _breakdown(_mc(TODAY + timedelta(days=90)), WebIntelligence(), Champions())
    assert b[3].points == 20


def test_expiry_boundary_91_falls_to_10():
    b = _breakdown(_mc(TODAY + timedelta(days=91)), WebIntelligence(), Champions())
    assert b[3].points == 10


def test_expiry_none_is_0():
    b = _breakdown(_mc(None), WebIntelligence(), Champions())
    assert b[3].points == 0


# ---------- #10 KSA Office ----------

def test_ksa_office_listed_is_5():
    w = WebIntelligence(has_ksa_office_listed=True)
    b = _breakdown(_mc(), w, Champions())
    assert b[10].points == 5


def test_ksa_office_missing_active_cr_is_10():
    w = WebIntelligence(has_ksa_office_listed=False)
    b = _breakdown(_mc(), w, Champions())
    assert b[10].points == 10


# ---------- #11 Job Titles ----------

def test_saudization_quota_roles_is_10():
    w = WebIntelligence(ksa_saudization_quota_roles=True, ksa_generic_roles=True)
    b = _breakdown(_mc(), w, Champions())
    assert b[11].points == 10


def test_generic_roles_only_is_5():
    w = WebIntelligence(ksa_generic_roles=True)
    b = _breakdown(_mc(), w, Champions())
    assert b[11].points == 5


def test_no_roles_is_0():
    b = _breakdown(_mc(), WebIntelligence(), Champions())
    assert b[11].points == 0


# ---------- #12 Headcount ----------

def test_headcount_sweet_spot():
    assert _breakdown(_mc(), WebIntelligence(ksa_headcount=30), Champions())[12].points == 15
    assert _breakdown(_mc(), WebIntelligence(ksa_headcount=1), Champions())[12].points == 15
    assert _breakdown(_mc(), WebIntelligence(ksa_headcount=50), Champions())[12].points == 15


def test_headcount_mid():
    assert _breakdown(_mc(), WebIntelligence(ksa_headcount=51), Champions())[12].points == 10
    assert _breakdown(_mc(), WebIntelligence(ksa_headcount=200), Champions())[12].points == 10


def test_headcount_large():
    assert _breakdown(_mc(), WebIntelligence(ksa_headcount=201), Champions())[12].points == 5
    assert _breakdown(_mc(), WebIntelligence(ksa_headcount=500), Champions())[12].points == 5


def test_headcount_enterprise_is_0():
    assert _breakdown(_mc(), WebIntelligence(ksa_headcount=501), Champions())[12].points == 0


def test_headcount_unknown_is_0():
    assert _breakdown(_mc(), WebIntelligence(), Champions())[12].points == 0


# ---------- #13 Growth ----------

def test_growth_20_plus_is_20():
    w = WebIntelligence(ksa_headcount_growth_6mo_pct=25)
    assert _breakdown(_mc(), w, Champions())[13].points == 20


def test_growth_10_to_20_is_10():
    w = WebIntelligence(ksa_headcount_growth_6mo_pct=15)
    assert _breakdown(_mc(), w, Champions())[13].points == 10


def test_growth_stable_is_0():
    w = WebIntelligence(ksa_headcount_growth_6mo_pct=5)
    assert _breakdown(_mc(), w, Champions())[13].points == 0


# ---------- #14 Global size ----------

def test_global_size_smb():
    assert _breakdown(_mc(), WebIntelligence(global_headcount=100), Champions())[14].points == 10


def test_global_size_mid():
    assert _breakdown(_mc(), WebIntelligence(global_headcount=1000), Champions())[14].points == 5


def test_global_size_enterprise():
    assert _breakdown(_mc(), WebIntelligence(global_headcount=5000), Champions())[14].points == 3


# ---------- #15 Company type ----------

def test_company_type_startup_or_scaleup():
    w = WebIntelligence(company_type=CompanyType.STARTUP)
    assert _breakdown(_mc(), w, Champions())[15].points == 5
    w = WebIntelligence(company_type=CompanyType.SCALEUP)
    assert _breakdown(_mc(), w, Champions())[15].points == 5


def test_company_type_enterprise():
    w = WebIntelligence(company_type=CompanyType.ENTERPRISE)
    assert _breakdown(_mc(), w, Champions())[15].points == 3


# ---------- #16 Business model ----------

def test_business_model():
    assert _breakdown(_mc(), WebIntelligence(business_model=BusinessModel.PRODUCT_LED), Champions())[16].points == 5
    assert _breakdown(_mc(), WebIntelligence(business_model=BusinessModel.HYBRID), Champions())[16].points == 3
    assert _breakdown(_mc(), WebIntelligence(business_model=BusinessModel.SALES_LED), Champions())[16].points == 0


# ---------- #17 Vacancies ----------

def test_vacancies_thresholds():
    assert _breakdown(_mc(), WebIntelligence(ksa_open_vacancies=5), Champions())[17].points == 20
    assert _breakdown(_mc(), WebIntelligence(ksa_open_vacancies=1), Champions())[17].points == 10
    assert _breakdown(_mc(), WebIntelligence(ksa_open_vacancies=0), Champions())[17].points == 0


# ---------- #18 New GM ----------

def test_new_gm_is_25():
    w = WebIntelligence(new_gm_within_6mo=True)
    assert _breakdown(_mc(), w, Champions())[18].points == 25


def test_senior_hire_is_15():
    w = WebIntelligence(senior_hire_within_6mo=True)
    assert _breakdown(_mc(), w, Champions())[18].points == 15


def test_new_gm_takes_priority_over_senior_hire():
    w = WebIntelligence(new_gm_within_6mo=True, senior_hire_within_6mo=True)
    assert _breakdown(_mc(), w, Champions())[18].points == 25


# ---------- #19 Funding ----------

def test_funding_plus_ksa_is_20():
    w = WebIntelligence(funding_last_12mo=True, funding_mentions_ksa=True)
    assert _breakdown(_mc(), w, Champions())[19].points == 20


def test_funding_only_is_10():
    w = WebIntelligence(funding_last_12mo=True)
    assert _breakdown(_mc(), w, Champions())[19].points == 10


def test_no_funding_is_0():
    assert _breakdown(_mc(), WebIntelligence(), Champions())[19].points == 0


# ---------- #20 News ----------

def test_ksa_news_is_15():
    assert _breakdown(_mc(), WebIntelligence(ksa_news_last_6mo=True), Champions())[20].points == 15


def test_general_news_only_is_5():
    assert _breakdown(_mc(), WebIntelligence(general_news_last_6mo=True), Champions())[20].points == 5


# ---------- #21 Partnerships ----------

def test_saudi_partnership_is_10():
    w = WebIntelligence(partnership_with_saudi_entity=True)
    assert _breakdown(_mc(), w, Champions())[21].points == 10


def test_social_mention_is_5():
    w = WebIntelligence(social_mention_by_saudi_partner=True)
    assert _breakdown(_mc(), w, Champions())[21].points == 5


# ---------- #22 V2030 ----------

def test_v2030_priority_is_15():
    w = WebIntelligence(v2030_tier=V2030Tier.PRIORITY)
    assert _breakdown(_mc(), w, Champions())[22].points == 15


def test_v2030_adjacent_is_5():
    w = WebIntelligence(v2030_tier=V2030Tier.ADJACENT)
    assert _breakdown(_mc(), w, Champions())[22].points == 5


# ---------- #23 Admin ----------

def test_admin_none_is_10():
    assert _breakdown(_mc(), WebIntelligence(), Champions())[23].points == 10


def test_admin_overwhelmed_is_10():
    admin = Champion("A", "Admin & HR & Finance", "u")
    assert _breakdown(_mc(), WebIntelligence(), Champions(admin=admin, admin_is_overwhelmed=True))[23].points == 10


def test_admin_dedicated_is_5():
    admin = Champion("A", "Office Manager", "u")
    assert _breakdown(_mc(), WebIntelligence(), Champions(admin=admin))[23].points == 5


# ---------- #24 GM ----------

def test_gm_new_is_15():
    gm = Champion("G", "GM", "u", start_date=TODAY)
    assert _breakdown(_mc(), WebIntelligence(), Champions(gm=gm, gm_is_new=True))[24].points == 15


def test_gm_established_is_5():
    gm = Champion("G", "GM", "u")
    assert _breakdown(_mc(), WebIntelligence(), Champions(gm=gm))[24].points == 5


def test_gm_none_is_0():
    assert _breakdown(_mc(), WebIntelligence(), Champions())[24].points == 0


# ---------- Totals ----------

def test_max_theoretical_score_is_225():
    """Best-case lead hits every tier ceiling → 225."""
    mc = _mc(expiry=TODAY + timedelta(days=30))  # 20
    web = WebIntelligence(
        has_ksa_office_listed=False,              # 10 (missing scores higher)
        ksa_saudization_quota_roles=True,         # 10
        ksa_headcount=25,                          # 15
        ksa_headcount_growth_6mo_pct=30,           # 20
        global_headcount=150,                      # 10
        company_type=CompanyType.SCALEUP,          # 5
        business_model=BusinessModel.PRODUCT_LED,  # 5
        ksa_open_vacancies=10,                     # 20
        new_gm_within_6mo=True,                    # 25
        funding_last_12mo=True,
        funding_mentions_ksa=True,                 # 20
        ksa_news_last_6mo=True,                    # 15
        partnership_with_saudi_entity=True,        # 10
        v2030_tier=V2030Tier.PRIORITY,             # 15
    )
    champs = Champions(
        admin=Champion("A", "Admin & HR", "u"),
        admin_is_overwhelmed=True,                 # 10
        gm=Champion("G", "GM", "u", start_date=TODAY),
        gm_is_new=True,                            # 15
    )
    total = sum(line.points for line in score(mc, web, champs, today=TODAY))
    assert total == 225


def test_zero_score_lead():
    """Company with minimal signal — should still produce a number, not crash."""
    mc = _mc()
    # Listing is false → #10 still contributes 10 (missing + active CR).
    # Admin none → #23 contributes 10. So baseline is 20.
    total = sum(line.points for line in score(mc, WebIntelligence(), Champions(), today=TODAY))
    assert total == 20
