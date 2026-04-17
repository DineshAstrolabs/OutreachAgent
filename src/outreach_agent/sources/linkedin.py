"""LinkedIn source — Company page, Jobs, People Search.

Covers 9 of 17 data points:
  #10 KSA office address, #11 job titles, #12 headcount, #13 growth,
  #14 global size, #17 vacancies, #18 new GM / senior hire, #23 admin, #24 GM.

LinkedIn actively blocks scraping. Production deployment requires a scraping
proxy (Bright Data / ScrapingBee / Apify LinkedIn actor) or an official
partner API. The live client here is a thin wrapper that expects a proxy URL
to be configured; the stub is used for development.
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path

import httpx

from ..models import Champion, Champions, CompanyType, WebIntelligence

log = logging.getLogger(__name__)


class LinkedInSource:
    """Live LinkedIn source via scraping proxy."""

    name = "linkedin"

    def __init__(self, proxy_url: str, proxy_user: str | None = None, proxy_pass: str | None = None):
        self.proxy_url = proxy_url
        self.http = httpx.Client(
            timeout=30.0,
            proxies=proxy_url,
            auth=(proxy_user, proxy_pass) if proxy_user and proxy_pass else None,
        )

    def enrich(self, company_name: str, web: WebIntelligence) -> None:
        try:
            # Real implementation would call the proxy's LinkedIn actor /
            # endpoints. Responses differ by provider; adapter code lives here.
            log.info("live LinkedIn enrich for %s (not implemented)", company_name)
            raise NotImplementedError("Provider-specific adapter required")
        except Exception as exc:
            log.warning("linkedin enrichment failed: %s", exc)
            web.sources_failed.append(self.name)

    def find_champions(self, company_name: str) -> Champions:
        raise NotImplementedError


class StubLinkedInSource:
    """Fixture-backed LinkedIn source for dev/CI."""

    name = "linkedin"

    def __init__(self, fixtures_dir: Path | None = None):
        self.fixtures_dir = fixtures_dir or Path(__file__).parent.parent.parent.parent / "fixtures" / "linkedin"

    def enrich(self, company_name: str, web: WebIntelligence) -> None:
        data = self._load(company_name)
        if not data:
            return

        web.has_ksa_office_listed = data.get("has_ksa_office_listed", False)
        web.ksa_saudization_quota_roles = data.get("ksa_saudization_quota_roles", False)
        web.ksa_generic_roles = data.get("ksa_generic_roles", False)
        web.ksa_headcount = data.get("ksa_headcount")
        web.ksa_headcount_growth_6mo_pct = data.get("ksa_headcount_growth_6mo_pct")
        web.global_headcount = data.get("global_headcount")
        web.ksa_open_vacancies = data.get("ksa_open_vacancies", 0)
        if "company_type" in data:
            web.company_type = CompanyType(data["company_type"])
        web.sources_seen.append(self.name)

    def find_champions(self, company_name: str) -> Champions:
        data = self._load(company_name) or {}
        admin = _to_champion(data.get("admin"))
        gm = _to_champion(data.get("gm"))
        return Champions(
            admin=admin,
            admin_is_overwhelmed=bool(data.get("admin_is_overwhelmed")),
            gm=gm,
            gm_is_new=_is_new_role(gm),
        )

    def _load(self, company_name: str) -> dict | None:
        path = self.fixtures_dir / f"{_slug(company_name)}.json"
        if path.exists():
            return json.loads(path.read_text())
        log.info("no linkedin fixture for %s", company_name)
        return None


def _slug(name: str) -> str:
    return "".join(c.lower() if c.isalnum() else "-" for c in name).strip("-")


def _to_champion(d: dict | None) -> Champion | None:
    if not d:
        return None
    start = d.get("start_date")
    return Champion(
        name=d["name"],
        title=d["title"],
        linkedin_url=d["linkedin_url"],
        start_date=date.fromisoformat(start) if start else None,
    )


def _is_new_role(c: Champion | None) -> bool:
    if c is None or c.start_date is None:
        return False
    return c.start_date >= date.today() - timedelta(days=180)
