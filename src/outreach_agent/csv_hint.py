"""Parse a batch-CSV row into an MCData "hint" for the pipeline.

Operators often have a spreadsheet with pre-fetched CR data next to each
Unified Number (exported from Wathq / internal CRM / broker lookups). We
still fetch MC live — MC is authoritative — but fall back to these
columns when MC omits a field, and use the row as a full replacement when
MC lookup fails outright (e.g., captcha exhausted).

Column names in the user's sheet aren't standardized, so every accessor
here tries a small set of aliases (case-insensitive, trimmed).
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime

from .models import CRStatus, EntityType, MCData

log = logging.getLogger(__name__)


# Map MCData field → list of accepted column-header aliases (case folded).
_ALIASES: dict[str, tuple[str, ...]] = {
    "unified_number":         ("unified_number", "unified number", "unifiednumber"),
    "company_legal_name":     ("company name (en)", "company_name_en", "company name", "company_name", "legal name"),
    "cr_status":              ("registration status", "cr status", "status"),
    "entity_type":            ("legal entity", "entity type", "entity_type"),
    "business_type_raw":      ("registration type", "business type", "business_type"),
    "cr_number":              ("registration number", "cr number", "cr_number"),
    "cr_issue_date":          ("registration date", "issue date", "cr issue date"),
    "cr_expiry_date":         ("expiry date", "cr expiry date", "expiration date"),
    "registered_capital_sar": ("capital", "registered capital", "capital (sar)"),
    "phone":                  ("phone", "mobile", "telephone"),
    "activities":             ("activities",),
    "city":                   ("location", "city"),
    "region":                 ("region",),
}


def row_to_mc_hint(row: dict[str, str]) -> MCData | None:
    """Return an MCData built from whichever hint columns the row has, or
    None if the row lacks a usable Unified Number."""
    folded = {_fold(k): (v or "").strip() for k, v in row.items()}

    def get(field: str) -> str:
        for alias in _ALIASES[field]:
            v = folded.get(alias)
            if v:
                return v
        return ""

    un = get("unified_number")
    if not un:
        return None

    status_raw = get("cr_status")
    entity_raw = get("entity_type") or get("business_type_raw")

    return MCData(
        unified_number=un,
        company_legal_name=get("company_legal_name"),
        cr_status=_parse_status(status_raw) if status_raw else CRStatus.NOT_FOUND,
        entity_type=_parse_entity_type(entity_raw) if entity_raw else EntityType.UNKNOWN,
        cr_number=get("cr_number") or None,
        cr_issue_date=_parse_date(get("cr_issue_date")),
        cr_expiry_date=_parse_date(get("cr_expiry_date")),
        business_type_raw=get("business_type_raw") or None,
        registered_capital_sar=_first_int(get("registered_capital_sar")),
        phone=get("phone") or None,
        activities=get("activities") or None,
        city=get("city") or None,
        region=get("region") or None,
    )


def merge_hint_into_mc(mc: MCData, hint: MCData | None) -> MCData:
    """Fill blanks in `mc` from `hint`. Non-destructive — MC wins when set.

    If MC returned NOT_FOUND but the hint has a real status, we swap in the
    hint wholesale (except unified_number, which is authoritative).
    """
    if hint is None:
        return mc

    # MC came back empty — use the hint as the source of truth. Keeps the
    # pipeline from disqualifying a lead just because MC captcha exhausted.
    if mc.cr_status == CRStatus.NOT_FOUND and hint.cr_status != CRStatus.NOT_FOUND:
        log.info(
            "[hint] MC returned NOT_FOUND; falling back to CSV hint (name=%r status=%s)",
            hint.company_legal_name, hint.cr_status.value,
        )
        return MCData(
            unified_number=mc.unified_number,
            company_legal_name=hint.company_legal_name,
            cr_status=hint.cr_status,
            entity_type=hint.entity_type,
            cr_number=hint.cr_number,
            cr_issue_date=hint.cr_issue_date,
            cr_expiry_date=hint.cr_expiry_date,
            business_type_raw=hint.business_type_raw,
            company_duration_years=hint.company_duration_years,
            business_activity_isic=hint.business_activity_isic,
            activities=hint.activities,
            registered_capital_sar=hint.registered_capital_sar,
            phone=hint.phone,
            website_url=hint.website_url,
            subsidiary_cr_count=hint.subsidiary_cr_count,
            city=hint.city,
            region=hint.region,
        )

    # MC is partial — backfill each missing field from the hint.
    def pick(mc_val, hint_val):
        return mc_val if mc_val else hint_val

    merged = MCData(
        unified_number=mc.unified_number,
        company_legal_name=pick(mc.company_legal_name, hint.company_legal_name),
        cr_status=mc.cr_status if mc.cr_status != CRStatus.NOT_FOUND else hint.cr_status,
        entity_type=mc.entity_type if mc.entity_type != EntityType.UNKNOWN else hint.entity_type,
        cr_number=pick(mc.cr_number, hint.cr_number),
        cr_issue_date=pick(mc.cr_issue_date, hint.cr_issue_date),
        cr_expiry_date=pick(mc.cr_expiry_date, hint.cr_expiry_date),
        business_type_raw=pick(mc.business_type_raw, hint.business_type_raw),
        company_duration_years=pick(mc.company_duration_years, hint.company_duration_years),
        business_activity_isic=pick(mc.business_activity_isic, hint.business_activity_isic),
        activities=pick(mc.activities, hint.activities),
        registered_capital_sar=pick(mc.registered_capital_sar, hint.registered_capital_sar),
        phone=pick(mc.phone, hint.phone),
        website_url=pick(mc.website_url, hint.website_url),
        subsidiary_cr_count=mc.subsidiary_cr_count or hint.subsidiary_cr_count,
        city=pick(mc.city, hint.city),
        region=pick(mc.region, hint.region),
    )

    filled = [
        f for f in (
            "company_legal_name", "cr_number", "cr_issue_date", "cr_expiry_date",
            "business_type_raw", "activities", "registered_capital_sar",
            "phone", "city", "region",
        )
        if getattr(mc, f) in (None, "", 0) and getattr(merged, f) not in (None, "", 0)
    ]
    if filled:
        log.info("[hint] backfilled from CSV: %s", ", ".join(filled))
    return merged


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fold(key: str) -> str:
    return (key or "").strip().lower()


_STATUS_MAP = {
    "active": CRStatus.ACTIVE,
    "expired": CRStatus.EXPIRED,
    "cancelled": CRStatus.CANCELLED,
    "canceled": CRStatus.CANCELLED,
    "struck off": CRStatus.STRUCK_OFF,
    "struck-off": CRStatus.STRUCK_OFF,
    "not found": CRStatus.NOT_FOUND,
}


def _parse_status(s: str) -> CRStatus:
    return _STATUS_MAP.get(s.lower().strip(), CRStatus.NOT_FOUND)


_ENTITY_MAP = {
    "llc foreign": EntityType.LLC_FOREIGN,
    "foreign llc": EntityType.LLC_FOREIGN,
    "branch of foreign": EntityType.BRANCH_FOREIGN,
    "branch foreign": EntityType.BRANCH_FOREIGN,
    "llc saudi": EntityType.LLC_SAUDI,
    "llc": EntityType.LLC_SAUDI,
    "sole proprietorship": EntityType.SOLE_PROPRIETORSHIP,
    "government": EntityType.GOVERNMENT,
    "semi-government": EntityType.SEMI_GOVERNMENT,
    "semi government": EntityType.SEMI_GOVERNMENT,
}


def _parse_entity_type(s: str) -> EntityType:
    low = s.lower().strip()
    for key, value in _ENTITY_MAP.items():
        if key in low:
            return value
    return EntityType.UNKNOWN


def _parse_date(s: str) -> date | None:
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except (TypeError, ValueError):
            continue
    return None


def _first_int(s: str) -> int | None:
    if not s:
        return None
    digits = re.sub(r"[^\d]", "", s)
    return int(digits) if digits else None
