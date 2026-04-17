"""Scoring engine — 17 data points, 225 max points.

Thresholds taken directly from the qualification matrix in the feature spec.
Every score contribution is recorded as a ScoreLine for the audit trail
(Solution Principle: transparency over black box).

Data point numbering matches the matrix (gaps where MC returns non-scored
metadata like Capital / Subsidiaries / City).
"""

from __future__ import annotations

from datetime import date

from .models import (
    BusinessModel,
    Champions,
    CompanyType,
    MCData,
    ScoreLine,
    V2030Tier,
    WebIntelligence,
)


def score(
    mc: MCData,
    web: WebIntelligence,
    champions: Champions,
    today: date | None = None,
) -> list[ScoreLine]:
    """Return all ScoreLine entries in spec order. Caller sums them."""
    today = today or date.today()
    lines: list[ScoreLine] = []

    lines.append(_score_cr_expiry(mc, today))
    lines.extend(_score_ksa_presence_fit(web))
    lines.extend(_score_growth_market(web))
    lines.extend(_score_champions(champions))

    return lines


# ---------------------------------------------------------------------------
# Eligibility (1 scored point after gates)
# ---------------------------------------------------------------------------

def _score_cr_expiry(mc: MCData, today: date) -> ScoreLine:
    """#3 CR Expiry Date — <=90d +20, <=180d +10, else +0."""
    if mc.cr_expiry_date is None:
        return ScoreLine(3, "CR Expiry Date", "unknown", 0)

    days_to_expiry = (mc.cr_expiry_date - today).days
    if days_to_expiry <= 90:
        points = 20
        label = f"expires in {days_to_expiry}d (≤90)"
    elif days_to_expiry <= 180:
        points = 10
        label = f"expires in {days_to_expiry}d (≤180)"
    else:
        points = 0
        label = f"expires in {days_to_expiry}d (>180)"

    return ScoreLine(3, "CR Expiry Date", label, points)


# ---------------------------------------------------------------------------
# KSA Presence & Fit (7 data points)
# ---------------------------------------------------------------------------

def _score_ksa_presence_fit(w: WebIntelligence) -> list[ScoreLine]:
    return [
        _score_ksa_office(w),
        _score_job_titles(w),
        _score_headcount(w),
        _score_headcount_growth(w),
        _score_global_size(w),
        _score_company_type(w),
        _score_business_model(w),
    ]


def _score_ksa_office(w: WebIntelligence) -> ScoreLine:
    """#10 KSA office listed +5 / missing but active CR +10.

    Missing scores HIGHER because an active CR + no visible KSA presence is a
    strong signal that the company needs Post-Setup help.
    """
    if w.has_ksa_office_listed:
        return ScoreLine(10, "KSA Office Listed", "listed", 5)
    return ScoreLine(10, "KSA Office Listed", "no KSA address but active CR", 10)


def _score_job_titles(w: WebIntelligence) -> ScoreLine:
    """#11 Saudization-quota roles +10, generic +5, none +0."""
    if w.ksa_saudization_quota_roles:
        return ScoreLine(11, "Job Titles in Saudi", "saudization-quota roles", 10)
    if w.ksa_generic_roles:
        return ScoreLine(11, "Job Titles in Saudi", "generic roles", 5)
    return ScoreLine(11, "Job Titles in Saudi", "none found", 0)


def _score_headcount(w: WebIntelligence) -> ScoreLine:
    """#12 KSA headcount: 1-50 +15, 51-200 +10, 201-500 +5, 500+ +0."""
    n = w.ksa_headcount
    if n is None:
        return ScoreLine(12, "KSA Headcount", "unknown", 0)
    if 1 <= n <= 50:
        return ScoreLine(12, "KSA Headcount", f"{n} (1-50 sweet spot)", 15)
    if n <= 200:
        return ScoreLine(12, "KSA Headcount", f"{n} (51-200)", 10)
    if n <= 500:
        return ScoreLine(12, "KSA Headcount", f"{n} (201-500)", 5)
    return ScoreLine(12, "KSA Headcount", f"{n} (500+)", 0)


def _score_headcount_growth(w: WebIntelligence) -> ScoreLine:
    """#13 KSA headcount growth 6mo: 20%+ +20, 10-20% +10, else +0."""
    g = w.ksa_headcount_growth_6mo_pct
    if g is None:
        return ScoreLine(13, "KSA Headcount Growth (6mo)", "unknown", 0)
    if g >= 20:
        return ScoreLine(13, "KSA Headcount Growth (6mo)", f"{g:.0f}% (>=20%)", 20)
    if g >= 10:
        return ScoreLine(13, "KSA Headcount Growth (6mo)", f"{g:.0f}% (10-20%)", 10)
    return ScoreLine(13, "KSA Headcount Growth (6mo)", f"{g:.0f}% (stable/shrinking)", 0)


def _score_global_size(w: WebIntelligence) -> ScoreLine:
    """#14 Global size: SMB 10-200 +10, Mid 200-2K +5, Enterprise 2K+ +3."""
    n = w.global_headcount
    if n is None:
        return ScoreLine(14, "Global Company Size", "unknown", 0)
    if 10 <= n <= 200:
        return ScoreLine(14, "Global Company Size", f"{n} (SMB)", 10)
    if n <= 2000:
        return ScoreLine(14, "Global Company Size", f"{n} (Mid)", 5)
    return ScoreLine(14, "Global Company Size", f"{n} (Enterprise)", 3)


def _score_company_type(w: WebIntelligence) -> ScoreLine:
    """#15 Company type: Startup/Scale-up +5, Enterprise +3, else +0."""
    if w.company_type in (CompanyType.STARTUP, CompanyType.SCALEUP):
        return ScoreLine(15, "Company Type", w.company_type.value, 5)
    if w.company_type == CompanyType.ENTERPRISE:
        return ScoreLine(15, "Company Type", "Enterprise", 3)
    return ScoreLine(15, "Company Type", "unknown", 0)


def _score_business_model(w: WebIntelligence) -> ScoreLine:
    """#16 Product-Led +5, Hybrid +3, Sales-Led +0."""
    if w.business_model == BusinessModel.PRODUCT_LED:
        return ScoreLine(16, "Business Model", "Product-Led", 5)
    if w.business_model == BusinessModel.HYBRID:
        return ScoreLine(16, "Business Model", "Hybrid", 3)
    if w.business_model == BusinessModel.SALES_LED:
        return ScoreLine(16, "Business Model", "Sales-Led", 0)
    return ScoreLine(16, "Business Model", "unknown", 0)


# ---------------------------------------------------------------------------
# Growth & Market Signals (6 data points)
# ---------------------------------------------------------------------------

def _score_growth_market(w: WebIntelligence) -> list[ScoreLine]:
    return [
        _score_vacancies(w),
        _score_new_gm(w),
        _score_funding(w),
        _score_news(w),
        _score_partnerships(w),
        _score_v2030(w),
    ]


def _score_vacancies(w: WebIntelligence) -> ScoreLine:
    """#17 KSA vacancies: 5+ +20, 1-4 +10, 0 +0."""
    n = w.ksa_open_vacancies
    if n >= 5:
        return ScoreLine(17, "KSA Open Vacancies", f"{n} (5+)", 20)
    if n >= 1:
        return ScoreLine(17, "KSA Open Vacancies", f"{n} (1-4)", 10)
    return ScoreLine(17, "KSA Open Vacancies", "0", 0)


def _score_new_gm(w: WebIntelligence) -> ScoreLine:
    """#18 New GM <6mo +25, senior hire +15, none +0 (GM Amendment trigger)."""
    if w.new_gm_within_6mo:
        return ScoreLine(18, "New GM/Country Manager", "<6mo (GM Amendment trigger)", 25)
    if w.senior_hire_within_6mo:
        return ScoreLine(18, "New GM/Country Manager", "senior hire <6mo", 15)
    return ScoreLine(18, "New GM/Country Manager", "none", 0)


def _score_funding(w: WebIntelligence) -> ScoreLine:
    """#19 Funding + KSA mention +20, funding only +10, none +0."""
    if w.funding_last_12mo and w.funding_mentions_ksa:
        return ScoreLine(19, "Funding (12mo)", "funding + KSA mention", 20)
    if w.funding_last_12mo:
        return ScoreLine(19, "Funding (12mo)", "funding only", 10)
    return ScoreLine(19, "Funding (12mo)", "none", 0)


def _score_news(w: WebIntelligence) -> ScoreLine:
    """#20 KSA-specific news last 6mo +15, general only +5, none +0."""
    if w.ksa_news_last_6mo:
        return ScoreLine(20, "Recent News (6mo)", "KSA-specific", 15)
    if w.general_news_last_6mo:
        return ScoreLine(20, "Recent News (6mo)", "general only", 5)
    return ScoreLine(20, "Recent News (6mo)", "none", 0)


def _score_partnerships(w: WebIntelligence) -> ScoreLine:
    """#21 Saudi partnership +10, social mention +5, none +0."""
    if w.partnership_with_saudi_entity:
        return ScoreLine(21, "Partnerships / Mentions", "partnership with Saudi entity", 10)
    if w.social_mention_by_saudi_partner:
        return ScoreLine(21, "Partnerships / Mentions", "social mention by partner", 5)
    return ScoreLine(21, "Partnerships / Mentions", "none", 0)


def _score_v2030(w: WebIntelligence) -> ScoreLine:
    """#22 Vision 2030 priority +15, adjacent +5, non-priority +0."""
    if w.v2030_tier == V2030Tier.PRIORITY:
        return ScoreLine(22, "Vision 2030 Alignment", "priority sector", 15)
    if w.v2030_tier == V2030Tier.ADJACENT:
        return ScoreLine(22, "Vision 2030 Alignment", "adjacent", 5)
    return ScoreLine(22, "Vision 2030 Alignment", "non-priority", 0)


# ---------------------------------------------------------------------------
# Champion Identification (2 data points)
# ---------------------------------------------------------------------------

def _score_champions(c: Champions) -> list[ScoreLine]:
    return [_score_admin(c), _score_gm(c)]


def _score_admin(c: Champions) -> ScoreLine:
    """#23 Admin overwhelmed +10, dedicated +5, no admin found +10.

    'No admin found' scores high because it signals unmet need — the company
    has no one handling Saudi compliance day-to-day.
    """
    if c.admin is None:
        return ScoreLine(23, "Admin / Office Manager", "none found (unmet need)", 10)
    if c.admin_is_overwhelmed:
        return ScoreLine(23, "Admin / Office Manager", "overwhelmed (multiple roles)", 10)
    return ScoreLine(23, "Admin / Office Manager", "dedicated admin", 5)


def _score_gm(c: Champions) -> ScoreLine:
    """#24 New GM <6mo +15, established +5, no GM +0 (flag for review)."""
    if c.gm is None:
        return ScoreLine(24, "GM / Country Manager", "none found (flag for review)", 0)
    if c.gm_is_new:
        return ScoreLine(24, "GM / Country Manager", "new <6mo (GM Amendment trigger)", 15)
    return ScoreLine(24, "GM / Country Manager", "established GM", 5)
