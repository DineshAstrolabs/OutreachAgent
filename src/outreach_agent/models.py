"""Domain models for the qualification pipeline.

These are plain dataclasses so the scoring/classification logic can be unit
tested without touching external sources.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum


class CRStatus(str, Enum):
    ACTIVE = "Active"
    EXPIRED = "Expired"
    CANCELLED = "Cancelled"
    STRUCK_OFF = "Struck Off"
    NOT_FOUND = "Not Found"


class EntityType(str, Enum):
    LLC_FOREIGN = "LLC foreign"
    BRANCH_FOREIGN = "Branch of foreign co"
    LLC_SAUDI = "LLC Saudi"
    SOLE_PROPRIETORSHIP = "Sole proprietorship"
    GOVERNMENT = "Government"
    SEMI_GOVERNMENT = "Semi-government"
    UNKNOWN = "Unknown"


class Classification(str, Enum):
    CRITICAL = "CRITICAL"
    HOT = "HOT"
    WARM = "WARM"
    COOL = "COOL"
    COLD = "COLD"
    DISQUALIFIED = "DISQUALIFIED"


class V2030Tier(str, Enum):
    PRIORITY = "priority"       # tech, fintech, tourism, healthcare, entertainment
    ADJACENT = "adjacent"
    NON_PRIORITY = "non-priority"


class BusinessModel(str, Enum):
    PRODUCT_LED = "Product-Led"
    HYBRID = "Hybrid"
    SALES_LED = "Sales-Led"
    UNKNOWN = "Unknown"


class CompanyType(str, Enum):
    STARTUP = "Startup"
    SCALEUP = "Scale-up"
    ENTERPRISE = "Enterprise"
    UNKNOWN = "Unknown"


@dataclass
class MCData:
    """Data extracted from the MC Commercial Registration public lookup.

    Captures every field rendered in MC's CR Records card. `entity_type` is
    our normalized classification; `business_type_raw` is the exact string
    MC prints (e.g., "Company", "Sole Proprietorship"). Both are kept —
    the raw value preserves fidelity for audit / re-classification."""

    unified_number: str
    company_legal_name: str
    cr_status: CRStatus
    entity_type: EntityType
    company_legal_name_ar: str | None = None  # Arabic legal name — from MC or CSV hint
    cr_number: str | None = None
    cr_issue_date: date | None = None
    cr_expiry_date: date | None = None
    business_type_raw: str | None = None      # "Business Type" as MC prints it
    company_duration_years: int | None = None  # "Company Duration"
    business_activity_isic: str | None = None
    activities: str | None = None              # free-text activities list
    registered_capital_sar: int | None = None
    phone: str | None = None
    mobile: str | None = None                  # "Mobile" from CSV hint (distinct from landline phone)
    website_url: str | None = None             # "Url Address"
    subsidiary_cr_count: int = 0
    city: str | None = None
    region: str | None = None


@dataclass
class WebIntelligence:
    """Aggregated signals from LinkedIn, Crunchbase, Google News, job boards, website."""

    # KSA Presence & Fit (#10–16)
    has_ksa_office_listed: bool = False
    ksa_saudization_quota_roles: bool = False
    ksa_generic_roles: bool = False
    ksa_headcount: int | None = None
    ksa_headcount_growth_6mo_pct: float | None = None
    global_headcount: int | None = None
    company_type: CompanyType = CompanyType.UNKNOWN
    business_model: BusinessModel = BusinessModel.UNKNOWN

    # Growth & Market (#17–22)
    ksa_open_vacancies: int = 0
    new_gm_within_6mo: bool = False
    senior_hire_within_6mo: bool = False
    funding_last_12mo: bool = False
    funding_mentions_ksa: bool = False
    ksa_news_last_6mo: bool = False
    general_news_last_6mo: bool = False
    partnership_with_saudi_entity: bool = False
    social_mention_by_saudi_partner: bool = False
    v2030_tier: V2030Tier = V2030Tier.NON_PRIORITY

    # Provenance — which sources succeeded. Missing source = +0 score, flagged.
    sources_seen: list[str] = field(default_factory=list)
    sources_failed: list[str] = field(default_factory=list)


@dataclass
class Champion:
    name: str
    title: str
    linkedin_url: str
    start_date: date | None = None


@dataclass
class Champions:
    admin: Champion | None = None
    admin_is_overwhelmed: bool = False  # multiple roles on profile
    gm: Champion | None = None
    gm_is_new: bool = False  # <6 months in role


@dataclass
class ScoreLine:
    """One row in the score breakdown (audit trail)."""

    data_point_id: int
    name: str
    observed_value: str
    points: int


@dataclass
class QualificationResult:
    unified_number: str
    mc_data: MCData | None
    web: WebIntelligence | None
    champions: Champions | None

    disqualified: bool = False
    disqualification_reason: str | None = None
    deprioritized: bool = False  # Saudi domestic: score but deprioritize
    deprioritized_reason: str | None = None

    breakdown: list[ScoreLine] = field(default_factory=list)
    total_score: int = 0
    classification: Classification = Classification.DISQUALIFIED

    recommended_action: str = ""
    outreach_approach: str = ""
    timeline: str = ""
    expected_conversion: str = ""

    scored_at: datetime = field(default_factory=datetime.utcnow)

    @property
    def company_name(self) -> str:
        return self.mc_data.company_legal_name if self.mc_data else "(unknown)"
