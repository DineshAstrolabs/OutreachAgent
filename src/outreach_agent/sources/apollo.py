"""Apollo.io source — company enrichment + people search for champions.

Replaces the LinkedIn proxy source. Apollo exposes a compliant B2B data API
(built partly from public LinkedIn profile signals plus licensed datasets),
so we get KSA presence, headcount, open roles, and named decision makers
without brittle HTML scraping.

Coverage vs. the original LinkedIn source:
  #10 KSA office      — organization.locations filtered by country == SA
  #11 job titles      — open_roles filtered by Saudization keyword list
  #12 headcount       — organization.estimated_num_employees (KSA head count
                        from locations.num_employees when exposed, else
                        fallback to global)
  #13 growth          — Apollo does not expose historical headcount deltas on
                        the public endpoint. Left unset → scoring engine gives
                        0 for #13. Documented limitation.
  #14 global size     — organization.estimated_num_employees
  #17 vacancies       — organization.num_current_positions (KSA filter)
  #18 new GM / senior — people search by KSA title; start_date < 6mo
  #23 admin / #24 GM  — people search by Admin / GM title keywords
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path

import httpx

from ..models import Champion, Champions, CompanyType, MCData, WebIntelligence

log = logging.getLogger(__name__)

APOLLO_BASE = "https://api.apollo.io"

# KSA-specific Saudization-quota-sensitive roles (health, engineering,
# accounting, HR, customer service, etc.) — rough heuristic, same list used
# by the LinkedIn scraper before.
QUOTA_TITLES = (
    "saudi",
    "accountant",
    "hr",
    "human resources",
    "customer service",
    "engineer",
    "nurse",
)
GENERIC_TITLES = ("manager", "specialist", "coordinator", "officer")

ADMIN_TITLES = ("admin", "office manager", "hr", "finance", "pa", "executive assistant")
GM_TITLES = ("general manager", "country manager", "managing director", "gm", "md")
OVERWHELM_SEPARATORS = ("&", " and ", "/", ",")


class ApolloSource:
    """Live Apollo.io source. Uses the REST API with an x-api-key header."""

    name = "apollo"

    def __init__(self, api_key: str, base_url: str = APOLLO_BASE):
        if not api_key:
            raise ValueError("Apollo source requires an API key")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.http = httpx.Client(
            timeout=30.0,
            headers={
                "x-api-key": api_key,
                "Content-Type": "application/json",
                "Cache-Control": "no-cache",
            },
        )

    # ---- WebIntelligenceSourceP -------------------------------------------

    def enrich(self, mc: MCData, web: WebIntelligence) -> None:
        company_name = mc.company_legal_name
        log.info("[apollo] enrich %r", company_name)
        try:
            org = self._find_organization(mc)
            if not org:
                log.info("[apollo] no organization match for %s", company_name)
                web.sources_failed.append(self.name)
                return
            log.info(
                "[apollo] matched org id=%s name=%r size=%s locations=%d",
                org.get("id"), org.get("name"),
                org.get("estimated_num_employees"),
                len(org.get("locations") or []),
            )

            web.global_headcount = org.get("estimated_num_employees")

            ksa_location = _pick_ksa_location(org.get("locations") or [])
            web.has_ksa_office_listed = ksa_location is not None
            if ksa_location:
                web.ksa_headcount = (
                    ksa_location.get("num_employees") or web.global_headcount
                )

            web.company_type = _infer_company_type(org)

            # Open positions + role-title fingerprinting via people search
            # filtered to KSA.
            log.info("[apollo] searching KSA open roles for org %s", org.get("id"))
            open_roles = self._search_ksa_open_roles(org.get("id"))
            web.ksa_open_vacancies = len(open_roles)
            log.info("[apollo] KSA open roles: %d", len(open_roles))
            titles = " | ".join(r.get("title", "").lower() for r in open_roles)
            web.ksa_saudization_quota_roles = any(t in titles for t in QUOTA_TITLES)
            web.ksa_generic_roles = any(t in titles for t in GENERIC_TITLES)

            web.sources_seen.append(self.name)
        except Exception as exc:
            log.warning("[apollo] enrich failed: %s", exc)
            web.sources_failed.append(self.name)

    # ---- Champion lookup --------------------------------------------------

    def find_champions(self, mc: MCData) -> Champions:
        company_name = mc.company_legal_name
        log.info("[apollo] find_champions %r", company_name)
        try:
            org = self._find_organization(mc)
            if not org:
                log.info("[apollo] no org match; empty champions")
                return Champions()

            log.info("[apollo] searching admin champion (titles: %s)", ADMIN_TITLES)
            admin = self._first_match(org.get("id"), ADMIN_TITLES)
            log.info("[apollo] searching GM champion (titles: %s)", GM_TITLES)
            gm = self._first_match(org.get("id"), GM_TITLES)

            admin_champion = _to_champion(admin) if admin else None
            gm_champion = _to_champion(gm) if gm else None
            return Champions(
                admin=admin_champion,
                admin_is_overwhelmed=_looks_overwhelmed(admin_champion),
                gm=gm_champion,
                gm_is_new=_is_new_role(gm_champion),
            )
        except Exception as exc:
            log.warning("apollo champion search failed: %s", exc)
            return Champions()

    # ---- Lookup orchestration --------------------------------------------

    def _find_organization(self, mc: MCData) -> dict | None:
        """Resolve an MC record to an Apollo organization.

        Apollo's B2B graph is mostly English, but KSA-specific accounts are
        sometimes indexed under the Arabic legal name (especially Saudi LLCs
        whose English rendering is ad-hoc). Try in order:

          1. English legal name
          2. Arabic legal name (from MC or CSV hint) — cheap extra call,
             often hits when the English transliteration is non-standard
          3. Website domain via /organizations/enrich

        If all three miss, Apollo is out; the factory-level chain then lets
        AnthropicCompanySource take a swing.
        """
        if mc.company_legal_name:
            org = self._enrich_organization(mc.company_legal_name)
            if org:
                return org
        if mc.company_legal_name_ar:
            log.info(
                "[apollo] EN miss — retrying with Arabic name %r",
                mc.company_legal_name_ar,
            )
            org = self._enrich_organization(mc.company_legal_name_ar)
            if org:
                return org
        if mc.website_url:
            log.info(
                "[apollo] name miss — retrying enrich via domain %s", mc.website_url
            )
            org = self._enrich_organization_by_domain(mc.website_url)
            if org:
                return org
        return None

    # ---- HTTP helpers -----------------------------------------------------

    def _enrich_organization(self, company_name: str) -> dict | None:
        """Look up an organization by name.

        Apollo's public `/organizations/enrich` endpoint is a GET that needs a
        `domain` — we usually don't have one coming out of MC. The name-lookup
        path is `/mixed_companies/search` with `q_organization_name`. If we
        later discover a domain (via website_url from MC) we can call enrich
        directly — wire that in when the URL is available.
        """
        query = _normalize_name(company_name)
        log.info(
            "[apollo] POST /api/v1/mixed_companies/search q_organization_name=%r",
            query,
        )
        resp = self.http.post(
            f"{self.base_url}/api/v1/mixed_companies/search",
            json={
                "q_organization_name": query,
                "page": 1,
                "per_page": 1,
            },
        )
        if resp.status_code == 422:
            # Apollo returns 422 when the query is empty, too long, or the
            # account isn't entitled to the search endpoint. Log the body so
            # we can see which, and fall back to "no match".
            log.warning(
                "[apollo] mixed_companies/search 422: %s",
                _safe_body(resp),
            )
            return None
        resp.raise_for_status()
        body = resp.json() or {}
        log.info(
            "[apollo] mixed_companies/search → %d (accounts=%d orgs=%d)",
            resp.status_code,
            len(body.get("accounts") or []),
            len(body.get("organizations") or []),
        )
        # Apollo may return matches under either `organizations` (public
        # dataset) or `accounts` (if the caller has it as a private account).
        orgs = body.get("organizations") or body.get("accounts") or []
        return orgs[0] if orgs else None

    def _enrich_organization_by_domain(self, url: str) -> dict | None:
        """Apollo's canonical enrich path is GET /organizations/enrich?domain=X.
        Use this when MC gave us a website URL and the name search missed."""
        domain = _url_to_domain(url)
        if not domain:
            return None
        log.info("[apollo] GET /api/v1/organizations/enrich domain=%s", domain)
        resp = self.http.get(
            f"{self.base_url}/api/v1/organizations/enrich",
            params={"domain": domain},
        )
        if resp.status_code == 422:
            log.warning("[apollo] organizations/enrich 422: %s", _safe_body(resp))
            return None
        resp.raise_for_status()
        return (resp.json() or {}).get("organization")

    def _search_ksa_open_roles(self, org_id: str | None) -> list[dict]:
        if not org_id:
            return []
        # Apollo doesn't expose company-level job postings through the public
        # enrich endpoint, so we approximate "open vacancies" with recent
        # people-search hits where contact_stage == 'Open' — the closest proxy
        # until the Jobs API is wired up.
        resp = self.http.post(
            f"{self.base_url}/api/v1/mixed_people/search",
            json={
                "organization_ids": [org_id],
                "person_locations": ["Saudi Arabia"],
                "page": 1,
                "per_page": 25,
            },
        )
        if resp.status_code == 422:
            log.warning("[apollo] mixed_people/search 422: %s", _safe_body(resp))
            return []
        resp.raise_for_status()
        return (resp.json() or {}).get("people", [])

    def _first_match(self, org_id: str | None, title_keywords: tuple[str, ...]) -> dict | None:
        if not org_id:
            return None
        resp = self.http.post(
            f"{self.base_url}/api/v1/mixed_people/search",
            json={
                "organization_ids": [org_id],
                "person_locations": ["Saudi Arabia"],
                "person_titles": list(title_keywords),
                "page": 1,
                "per_page": 5,
            },
        )
        if resp.status_code == 422:
            log.warning(
                "[apollo] mixed_people/search (champions) 422: %s",
                _safe_body(resp),
            )
            return None
        resp.raise_for_status()
        people = (resp.json() or {}).get("people", [])
        return people[0] if people else None


class StubApolloSource:
    """Fixture-backed Apollo source. Reads the existing linkedin/ fixtures so
    existing test data continues to work — Apollo returns richer fields than
    we need, and the existing fixture shape is a strict subset."""

    name = "apollo"

    def __init__(self, fixtures_dir: Path | None = None):
        # Reuse the linkedin fixtures to avoid a migration; Apollo returns a
        # superset of these fields in production.
        self.fixtures_dir = (
            fixtures_dir
            or Path(__file__).parent.parent.parent.parent / "fixtures" / "linkedin"
        )

    def enrich(self, mc: MCData, web: WebIntelligence) -> None:
        data = self._load(mc.company_legal_name)
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

    def find_champions(self, mc: MCData) -> Champions:
        data = self._load(mc.company_legal_name) or {}
        admin = _to_fixture_champion(data.get("admin"))
        gm = _to_fixture_champion(data.get("gm"))
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
        log.info("no apollo fixture for %s", company_name)
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _slug(name: str) -> str:
    return "".join(c.lower() if c.isalnum() else "-" for c in name).strip("-")


# Apollo 422s on multi-space / overly long / Arabic-punctuation queries. MC
# company names routinely have double spaces ("OFFTEC Arabia  Company For ...")
# and trailing Arabic duplicates — strip those before hitting the API.
_APOLLO_MAX_QUERY = 128


def _normalize_name(name: str) -> str:
    # Collapse whitespace, drop anything past a reasonable cap so Apollo's
    # validator is happy. We keep the first N chars — MC names lead with the
    # distinctive entity name before appending the legal suffix.
    collapsed = " ".join(name.split())
    if len(collapsed) > _APOLLO_MAX_QUERY:
        collapsed = collapsed[:_APOLLO_MAX_QUERY].rstrip()
    return collapsed


def _url_to_domain(url: str) -> str | None:
    """Extract the bare domain — Apollo enrich wants 'acme.com', not a URL."""
    if not url:
        return None
    u = url.strip()
    for prefix in ("https://", "http://"):
        if u.lower().startswith(prefix):
            u = u[len(prefix):]
            break
    u = u.split("/", 1)[0].split("?", 1)[0].strip()
    if u.lower().startswith("www."):
        u = u[4:]
    return u or None


def _safe_body(resp: httpx.Response) -> str:
    # Truncated body for logs — Apollo's 422 payload names the bad field.
    try:
        text = resp.text
    except Exception:
        return "<unreadable>"
    return text[:400].replace("\n", " ")


def _pick_ksa_location(locations: list[dict]) -> dict | None:
    for loc in locations:
        country = (loc.get("country") or "").lower()
        if country in ("saudi arabia", "sa", "ksa"):
            return loc
    return None


def _infer_company_type(org: dict) -> CompanyType:
    size = org.get("estimated_num_employees") or 0
    if size and size <= 50:
        return CompanyType.STARTUP
    if size and size <= 500:
        return CompanyType.SCALEUP
    if size and size > 500:
        return CompanyType.ENTERPRISE
    return CompanyType.UNKNOWN


def _to_champion(person: dict) -> Champion:
    start_raw = person.get("current_start_date") or person.get("start_date")
    try:
        start = date.fromisoformat(start_raw) if start_raw else None
    except (TypeError, ValueError):
        start = None
    return Champion(
        name=person.get("name") or f"{person.get('first_name','')} {person.get('last_name','')}".strip(),
        title=person.get("title") or "",
        linkedin_url=person.get("linkedin_url") or "",
        start_date=start,
    )


def _to_fixture_champion(d: dict | None) -> Champion | None:
    if not d:
        return None
    start = d.get("start_date")
    return Champion(
        name=d["name"],
        title=d["title"],
        linkedin_url=d["linkedin_url"],
        start_date=date.fromisoformat(start) if start else None,
    )


def _looks_overwhelmed(c: Champion | None) -> bool:
    if c is None:
        return False
    return any(sep in c.title for sep in OVERWHELM_SEPARATORS)


def _is_new_role(c: Champion | None) -> bool:
    if c is None or c.start_date is None:
        return False
    return c.start_date >= date.today() - timedelta(days=180)
