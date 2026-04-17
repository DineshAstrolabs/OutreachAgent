"""Crunchbase source — funding rounds, company type, revenue band.

Covers #15 company type and #19 funding (partial — KSA mention comes from news).

Live client uses the Crunchbase REST API v4. The basic tier does not include
revenue estimates; tier negotiation is a project-level decision captured in
the spec's open questions.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx

from ..models import CompanyType, WebIntelligence

log = logging.getLogger(__name__)

CB_BASE = "https://api.crunchbase.com/api/v4"


class CrunchbaseSource:
    name = "crunchbase"

    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError("Crunchbase requires an API key")
        self.api_key = api_key
        self.http = httpx.Client(timeout=30.0)

    def enrich(self, company_name: str, web: WebIntelligence) -> None:
        try:
            resp = self.http.get(
                f"{CB_BASE}/searches/organizations",
                params={"user_key": self.api_key, "query": company_name, "limit": 1},
            )
            resp.raise_for_status()
            entities = resp.json().get("entities", [])
            if not entities:
                return
            props = entities[0].get("properties", {})
            web.funding_last_12mo = bool(props.get("last_funding_at"))
            # Classify company type from employee band + categories.
            size = props.get("num_employees_enum", "")
            if size in ("c_00001_00010", "c_00011_00050", "c_00051_00100"):
                web.company_type = CompanyType.STARTUP
            elif size in ("c_00101_00250", "c_00251_00500"):
                web.company_type = CompanyType.SCALEUP
            elif size:
                web.company_type = CompanyType.ENTERPRISE
            web.sources_seen.append(self.name)
        except Exception as exc:
            log.warning("crunchbase failed: %s", exc)
            web.sources_failed.append(self.name)


class StubCrunchbaseSource:
    name = "crunchbase"

    def __init__(self, fixtures_dir: Path | None = None):
        self.fixtures_dir = fixtures_dir or Path(__file__).parent.parent.parent.parent / "fixtures" / "crunchbase"

    def enrich(self, company_name: str, web: WebIntelligence) -> None:
        path = self.fixtures_dir / f"{_slug(company_name)}.json"
        if not path.exists():
            return
        data = json.loads(path.read_text())
        web.funding_last_12mo = data.get("funding_last_12mo", False)
        if "company_type" in data:
            web.company_type = CompanyType(data["company_type"])
        web.sources_seen.append(self.name)


def _slug(name: str) -> str:
    return "".join(c.lower() if c.isalnum() else "-" for c in name).strip("-")
