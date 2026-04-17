"""HubSpot CRM writer.

The spec requires 20+ custom properties on the HubSpot Company object. This
module centralizes the mapping so the property list is obvious from one
place, and ships a `PROPERTY_SCHEMA` constant that ops can import to create
the properties via HubSpot's API before go-live.

See: https://developers.hubspot.com/docs/api/crm/companies
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx

from ..models import QualificationResult

log = logging.getLogger(__name__)


# Mapping: HubSpot property name -> (label, type, description)
# Create these before first run via HubSpot's "Create a property" endpoint.
PROPERTY_SCHEMA: dict[str, tuple[str, str, str]] = {
    "unified_number":             ("Unified Number", "string", "Saudi CR Unified Number"),
    "cr_number":                  ("CR Number", "string", "Commercial Registration number"),
    "cr_status":                  ("CR Status", "enumeration", "Active/Expired/Cancelled/..."),
    "cr_entity_type":             ("CR Entity Type", "enumeration", "LLC foreign / Branch / ..."),
    "cr_business_type_raw":       ("CR Business Type (raw)", "string", "MC 'Business Type' as printed"),
    "cr_issue_date":              ("CR Issue Date", "date", ""),
    "cr_expiry_date":             ("CR Expiry Date", "date", ""),
    "company_duration_years":     ("Company Duration (years)", "number", ""),
    "business_activity_isic":     ("Business Activity ISIC", "string", ""),
    "activities":                 ("Activities", "string", "Free-text activities list"),
    "registered_capital_sar":     ("Registered Capital (SAR)", "number", ""),
    "subsidiary_cr_count":        ("Subsidiary CRs", "number", ""),
    "phone":                      ("Phone", "string", ""),
    "website_url":                ("Website URL", "string", "MC 'Url Address'"),
    "ksa_city":                   ("KSA City", "string", ""),
    "ksa_region":                 ("KSA Region", "string", ""),

    "score_cr_expiry":            ("Score: CR Expiry", "number", ""),
    "score_ksa_office":           ("Score: KSA Office", "number", ""),
    "score_job_titles":           ("Score: Job Titles", "number", ""),
    "score_ksa_headcount":        ("Score: KSA Headcount", "number", ""),
    "score_headcount_growth":     ("Score: Headcount Growth", "number", ""),
    "score_global_size":          ("Score: Global Size", "number", ""),
    "score_company_type":         ("Score: Company Type", "number", ""),
    "score_business_model":       ("Score: Business Model", "number", ""),
    "score_vacancies":            ("Score: Vacancies", "number", ""),
    "score_new_gm":               ("Score: New GM", "number", ""),
    "score_funding":              ("Score: Funding", "number", ""),
    "score_news":                 ("Score: News", "number", ""),
    "score_partnerships":         ("Score: Partnerships", "number", ""),
    "score_v2030":                ("Score: V2030 Alignment", "number", ""),
    "score_admin":                ("Score: Admin Champion", "number", ""),
    "score_gm":                   ("Score: GM Champion", "number", ""),

    "champion_admin_name":        ("Champion Admin Name", "string", ""),
    "champion_admin_title":       ("Champion Admin Title", "string", ""),
    "champion_admin_linkedin":    ("Champion Admin LinkedIn", "string", ""),
    "champion_gm_name":           ("Champion GM Name", "string", ""),
    "champion_gm_title":          ("Champion GM Title", "string", ""),
    "champion_gm_linkedin":       ("Champion GM LinkedIn", "string", ""),
    "champion_gm_start_date":     ("Champion GM Start Date", "date", ""),

    "qualification_score":        ("Qualification Score", "number", ""),
    "qualification_class":        ("Qualification Class", "enumeration", "CRITICAL/HOT/WARM/COOL/COLD/DISQUALIFIED"),
    "recommended_action":         ("Recommended Action", "string", ""),
    "outreach_approach":          ("Outreach Approach", "string", ""),
    "outreach_timeline":          ("Outreach Timeline", "string", ""),
    "expected_conversion":        ("Expected Conversion", "string", ""),
    "disqualification_reason":    ("Disqualification Reason", "string", ""),
    "deprioritized":              ("Deprioritized", "bool", ""),
    "scored_at":                  ("Scored At", "datetime", ""),
}


def to_hubspot_properties(r: QualificationResult) -> dict[str, object]:
    """Flatten a QualificationResult into HubSpot Company properties."""
    mc = r.mc_data
    c = r.champions
    scores = {line.data_point_id: line.points for line in r.breakdown}

    props: dict[str, object] = {
        "unified_number": r.unified_number,
        "qualification_score": r.total_score,
        "qualification_class": r.classification.value,
        "recommended_action": r.recommended_action,
        "outreach_approach": r.outreach_approach,
        "outreach_timeline": r.timeline,
        "expected_conversion": r.expected_conversion,
        "disqualification_reason": r.disqualification_reason or "",
        "deprioritized": r.deprioritized,
        "scored_at": r.scored_at.isoformat(),
    }

    if mc is not None:
        props.update({
            "name": mc.company_legal_name,
            "cr_number": mc.cr_number,
            "cr_status": mc.cr_status.value,
            "cr_entity_type": mc.entity_type.value,
            "cr_business_type_raw": mc.business_type_raw,
            "cr_issue_date": mc.cr_issue_date.isoformat() if mc.cr_issue_date else None,
            "cr_expiry_date": mc.cr_expiry_date.isoformat() if mc.cr_expiry_date else None,
            "company_duration_years": mc.company_duration_years,
            "business_activity_isic": mc.business_activity_isic,
            "activities": mc.activities,
            "registered_capital_sar": mc.registered_capital_sar,
            "subsidiary_cr_count": mc.subsidiary_cr_count,
            "phone": mc.phone,
            "website_url": mc.website_url,
            "ksa_city": mc.city,
            "ksa_region": mc.region,
        })

    # Map score lines (by data point id) to the HubSpot field names.
    id_to_field = {
        3: "score_cr_expiry",
        10: "score_ksa_office",
        11: "score_job_titles",
        12: "score_ksa_headcount",
        13: "score_headcount_growth",
        14: "score_global_size",
        15: "score_company_type",
        16: "score_business_model",
        17: "score_vacancies",
        18: "score_new_gm",
        19: "score_funding",
        20: "score_news",
        21: "score_partnerships",
        22: "score_v2030",
        23: "score_admin",
        24: "score_gm",
    }
    for data_point_id, field in id_to_field.items():
        props[field] = scores.get(data_point_id, 0)

    if c and c.admin:
        props["champion_admin_name"] = c.admin.name
        props["champion_admin_title"] = c.admin.title
        props["champion_admin_linkedin"] = c.admin.linkedin_url
    if c and c.gm:
        props["champion_gm_name"] = c.gm.name
        props["champion_gm_title"] = c.gm.title
        props["champion_gm_linkedin"] = c.gm.linkedin_url
        props["champion_gm_start_date"] = c.gm.start_date.isoformat() if c.gm.start_date else None

    return {k: v for k, v in props.items() if v is not None}


class HubSpotWriter:
    """Live HubSpot writer. Upserts a Company keyed on `unified_number`."""

    def __init__(self, api_token: str):
        if not api_token:
            raise ValueError("HubSpot requires an API token")
        self.http = httpx.Client(
            timeout=30.0,
            base_url="https://api.hubapi.com",
            headers={
                "Authorization": f"Bearer {api_token}",
                "Content-Type": "application/json",
            },
        )

    def write(self, result: QualificationResult) -> str:
        props = to_hubspot_properties(result)
        log.info(
            "[hubspot] write: unified=%s class=%s score=%s props=%d",
            result.unified_number, result.classification.value,
            result.total_score, len(props),
        )
        log.info("[hubspot] searching existing company by unified_number")
        search = self.http.post(
            "/crm/v3/objects/companies/search",
            json={
                "filterGroups": [{"filters": [{
                    "propertyName": "unified_number",
                    "operator": "EQ",
                    "value": result.unified_number,
                }]}],
                "limit": 1,
            },
        )
        search.raise_for_status()
        matches = search.json().get("results", [])

        if matches:
            company_id = matches[0]["id"]
            log.info("[hubspot] existing company id=%s → PATCH update", company_id)
            resp = self.http.patch(
                f"/crm/v3/objects/companies/{company_id}",
                json={"properties": props},
            )
        else:
            log.info("[hubspot] no existing match → POST create")
            resp = self.http.post(
                "/crm/v3/objects/companies",
                json={"properties": props},
            )
        resp.raise_for_status()
        company_id = resp.json()["id"]
        log.info("[hubspot] ✓ write ok, company_id=%s", company_id)
        return company_id


class StubHubSpotWriter:
    """Writes qualification results to `results/<unified_number>.json` locally."""

    def __init__(self, out_dir: Path | None = None):
        self.out_dir = out_dir or Path("results")
        self.out_dir.mkdir(parents=True, exist_ok=True)

    def write(self, result: QualificationResult) -> str:
        path = self.out_dir / f"{result.unified_number}.json"
        path.write_text(json.dumps(to_hubspot_properties(result), indent=2, default=str))
        log.info("wrote qualification to %s", path)
        return str(path)
