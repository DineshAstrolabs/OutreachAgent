"""Claude-powered fallbacks for Apollo (company intel + champions).

Used when ``APOLLO_ENABLED=false``. Each class calls Claude with the
server-side ``web_search`` tool to pull publicly available LinkedIn /
company-page signals, then maps them onto the same WebIntelligence /
Champions shape the Apollo source produces — so the downstream scoring
engine is source-agnostic.

Compliance note: the prompt instructs the model to use *publicly
available* LinkedIn profile pages (and any other open web data). We are
not scraping behind-login content; Claude reads what it finds via the
hosted search tool.
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path

from pydantic import BaseModel

from ..models import BusinessModel, Champion, Champions, CompanyType, WebIntelligence

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Company intelligence (replaces Apollo organization enrich)
# ---------------------------------------------------------------------------


COMPANY_SYSTEM_PROMPT = """\
You are a B2B research analyst for AstroLabs, a KSA business-setup firm.
For each company you receive, use the web_search tool to look up public
company pages (LinkedIn company page, the company's own careers page, Bayt,
Indeed, Crunchbase public profile) and return a strict JSON signal bundle
about their Saudi Arabia footprint.

Search strategy (run each as a separate web_search as needed):
  1. "<company>" site:linkedin.com/company      -> LinkedIn company page
  2. "<company>" careers OR jobs "Saudi Arabia" -> KSA open roles
  3. "<company>" headquarters OR "office" "Riyadh" OR "Jeddah"
  4. "<company>" about us                       -> company type / model

Scoring rules (keep every boolean FALSE unless you have concrete evidence):
  - has_ksa_office_listed: TRUE iff the LinkedIn page / website explicitly
    lists a Saudi Arabia office (Riyadh, Jeddah, Dammam, NEOM).
  - ksa_saudization_quota_roles: TRUE iff open roles include titles in
    Saudization-sensitive functions (HR, accountants, customer service,
    engineers, nurses) explicitly based in KSA.
  - ksa_generic_roles: TRUE iff open KSA roles include generic support
    titles (manager, specialist, coordinator, officer).
  - ksa_headcount: integer estimate of KSA-based employees. Use LinkedIn
    location-filtered employee count if visible. Null if unknown.
  - ksa_headcount_growth_6mo_pct: % change in KSA headcount over the last
    6 months. Null if you cannot confirm from historical mentions.
  - global_headcount: integer LinkedIn company-size estimate worldwide.
  - ksa_open_vacancies: integer count of open roles explicitly in KSA.
    0 if none visible.
  - company_type: one of "Startup" (<50), "Scale-up" (50-500), "Enterprise"
    (>500), or "Unknown". Base on global headcount + funding stage.
  - business_model: one of "Product-Led" (SaaS, self-serve), "Sales-Led"
    (enterprise sales), "Hybrid", or "Unknown".

Be conservative. Unclear or absent evidence → FALSE / null / "Unknown".
"""


class CompanyIntel(BaseModel):
    """Flat schema — uses 0 / "" sentinels so we can ship it as JSON without
    hitting Anthropic's strict-mode schema complexity cap."""

    has_ksa_office_listed: bool = False
    ksa_saudization_quota_roles: bool = False
    ksa_generic_roles: bool = False
    ksa_headcount: int = 0
    ksa_headcount_growth_6mo_pct: float = 0.0
    global_headcount: int = 0
    ksa_open_vacancies: int = 0
    company_type: str = "Unknown"
    business_model: str = "Unknown"


COMPANY_JSON_INSTRUCTIONS = """\
Respond with ONE JSON object and nothing else — no markdown fence, no prose.
Use exactly these keys (required, use false / 0 / "Unknown" when unknown):
  has_ksa_office_listed         boolean
  ksa_saudization_quota_roles   boolean
  ksa_generic_roles             boolean
  ksa_headcount                 integer    (0 if unknown)
  ksa_headcount_growth_6mo_pct  number     (0 if unknown)
  global_headcount              integer    (0 if unknown)
  ksa_open_vacancies            integer    (0 if unknown)
  company_type                  string     "Startup"|"Scale-up"|"Enterprise"|"Unknown"
  business_model                string     "Product-Led"|"Sales-Led"|"Hybrid"|"Unknown"
"""


class AnthropicCompanySource:
    """Live company-intel via Claude + web_search. Apollo replacement."""

    name = "anthropic_company"

    def __init__(self, api_key: str, model: str = "claude-opus-4-7"):
        if not api_key:
            raise ValueError("AnthropicCompanySource requires an API key")
        from anthropic import Anthropic

        self.client = Anthropic(api_key=api_key)
        self.model = model

    def enrich(self, company_name: str, web: WebIntelligence) -> None:
        log.info("[anthropic_company] researching %r via Claude + web_search", company_name)
        import time as _t
        t0 = _t.monotonic()
        try:
            intel = self._research(company_name)
        except Exception as exc:
            log.warning("[anthropic_company] failed for %s: %s", company_name, exc)
            web.sources_failed.append(self.name)
            return
        log.info(
            "[anthropic_company] ok in %.2fs: ksa_office=%s ksa_hc=%s global_hc=%s "
            "vacancies=%s type=%s model=%s",
            _t.monotonic() - t0,
            intel.has_ksa_office_listed, intel.ksa_headcount, intel.global_headcount,
            intel.ksa_open_vacancies, intel.company_type, intel.business_model,
        )

        # Merge semantics: only overwrite when Apollo left the field blank.
        # Anthropic runs AFTER Apollo in the factory, so if Apollo already
        # populated headcount/vacancies we keep those.
        if not web.has_ksa_office_listed:
            web.has_ksa_office_listed = intel.has_ksa_office_listed
        if not web.ksa_saudization_quota_roles:
            web.ksa_saudization_quota_roles = intel.ksa_saudization_quota_roles
        if not web.ksa_generic_roles:
            web.ksa_generic_roles = intel.ksa_generic_roles
        if web.ksa_headcount is None and intel.ksa_headcount:
            web.ksa_headcount = intel.ksa_headcount
        if web.ksa_headcount_growth_6mo_pct is None and intel.ksa_headcount_growth_6mo_pct:
            web.ksa_headcount_growth_6mo_pct = intel.ksa_headcount_growth_6mo_pct
        if web.global_headcount is None and intel.global_headcount:
            web.global_headcount = intel.global_headcount
        if not web.ksa_open_vacancies and intel.ksa_open_vacancies:
            web.ksa_open_vacancies = intel.ksa_open_vacancies
        if web.company_type == CompanyType.UNKNOWN:
            web.company_type = _safe_enum(CompanyType, intel.company_type, CompanyType.UNKNOWN)
        if web.business_model == BusinessModel.UNKNOWN:
            web.business_model = _safe_enum(BusinessModel, intel.business_model, BusinessModel.UNKNOWN)
        web.sources_seen.append(self.name)

    def _research(self, company_name: str) -> CompanyIntel:
        # messages.parse enforces a strict JSON schema that Anthropic rejects
        # as "Schema is too complex" for models with >~8 fields and unions.
        # Use messages.create with a JSON-only instruction instead.
        response = self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            thinking={"type": "adaptive"},
            tools=[{"type": "web_search_20260209", "name": "web_search"}],
            system=[
                {
                    "type": "text",
                    "text": COMPANY_SYSTEM_PROMPT + "\n\n" + COMPANY_JSON_INSTRUCTIONS,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Research the company '{company_name}' using publicly "
                        f"available sources (LinkedIn company page, careers site, "
                        f"Bayt, Indeed, Crunchbase) and return the signal bundle "
                        f"as a single JSON object."
                    ),
                }
            ],
        )
        text = "".join(
            b.text for b in response.content if getattr(b, "type", None) == "text"
        ).strip()
        return CompanyIntel.model_validate_json(_extract_json_object(text))


class StubAnthropicCompanySource:
    """Fixture-backed fallback. Reuses the apollo/linkedin fixture shape."""

    name = "anthropic_company"

    def __init__(self, fixtures_dir: Path | None = None):
        self.fixtures_dir = (
            fixtures_dir
            or Path(__file__).parent.parent.parent.parent / "fixtures" / "linkedin"
        )

    def enrich(self, company_name: str, web: WebIntelligence) -> None:
        path = self.fixtures_dir / f"{_slug(company_name)}.json"
        if not path.exists():
            return
        data = json.loads(path.read_text())
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


# ---------------------------------------------------------------------------
# Champion lookup (replaces Apollo people search)
# ---------------------------------------------------------------------------


CHAMPION_SYSTEM_PROMPT = """\
You are a B2B research analyst identifying internal champions at a target
company for AstroLabs outreach. For each company, find two KSA-based
decision makers using publicly available LinkedIn profile data:

  1. ADMIN champion: Office Manager / HR / Finance / Admin lead in KSA.
     Someone overwhelmed by admin work is a stronger champion — look for
     titles that combine multiple functions ("Office Manager & HR",
     "Admin / Finance / PA").
  2. GM champion: General Manager, Country Manager, or Managing Director
     for KSA. New GMs (<6 months in role) are especially strong champions.

Search strategy:
  - site:linkedin.com/in "<company>" "Saudi Arabia" "office manager" OR "HR"
  - site:linkedin.com/in "<company>" "General Manager" OR "Country Manager"

Rules:
  - Only include people you can confirm are currently at the company in KSA.
  - start_date_iso must be YYYY-MM-DD; null if you cannot determine it.
  - admin_is_overwhelmed: TRUE if admin.title contains multiple functions
    (separators like "&", "/", "and", ",").
  - gm_is_new: TRUE if gm.start_date_iso is within the last 180 days.
  - If you cannot confidently identify a person, return null for that slot.
"""


class ChampionBundle(BaseModel):
    """Flat champion schema — no nested models, empty strings as sentinels."""

    admin_name: str = ""
    admin_title: str = ""
    admin_linkedin_url: str = ""
    admin_start_date_iso: str = ""
    admin_is_overwhelmed: bool = False
    gm_name: str = ""
    gm_title: str = ""
    gm_linkedin_url: str = ""
    gm_start_date_iso: str = ""
    gm_is_new: bool = False


CHAMPION_JSON_INSTRUCTIONS = """\
Respond with ONE JSON object and nothing else — no markdown fence, no prose.
Use exactly these keys (required, use "" / false when unknown):
  admin_name             string
  admin_title            string
  admin_linkedin_url     string
  admin_start_date_iso   string   (YYYY-MM-DD or "")
  admin_is_overwhelmed   boolean
  gm_name                string
  gm_title               string
  gm_linkedin_url        string
  gm_start_date_iso      string   (YYYY-MM-DD or "")
  gm_is_new              boolean
"""


class AnthropicChampionSource:
    """Live champion lookup via Claude + web_search. Apollo replacement."""

    name = "anthropic_champions"

    def __init__(self, api_key: str, model: str = "claude-opus-4-7"):
        if not api_key:
            raise ValueError("AnthropicChampionSource requires an API key")
        from anthropic import Anthropic

        self.client = Anthropic(api_key=api_key)
        self.model = model

    def find(self, company_name: str) -> Champions:
        log.info("[anthropic_champions] finding champions for %r via Claude + web_search", company_name)
        import time as _t
        t0 = _t.monotonic()
        try:
            bundle = self._research(company_name)
        except Exception as exc:
            log.warning("[anthropic_champions] failed for %s: %s", company_name, exc)
            return Champions()
        log.info(
            "[anthropic_champions] ok in %.2fs: admin=%s gm=%s admin_overwhelmed=%s gm_new=%s",
            _t.monotonic() - t0,
            bundle.admin_name or None, bundle.gm_name or None,
            bundle.admin_is_overwhelmed, bundle.gm_is_new,
        )

        return Champions(
            admin=_flat_to_champion(bundle.admin_name, bundle.admin_title,
                                    bundle.admin_linkedin_url, bundle.admin_start_date_iso),
            admin_is_overwhelmed=bundle.admin_is_overwhelmed,
            gm=_flat_to_champion(bundle.gm_name, bundle.gm_title,
                                 bundle.gm_linkedin_url, bundle.gm_start_date_iso),
            gm_is_new=bundle.gm_is_new,
        )

    def _research(self, company_name: str) -> ChampionBundle:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            thinking={"type": "adaptive"},
            tools=[{"type": "web_search_20260209", "name": "web_search"}],
            system=[
                {
                    "type": "text",
                    "text": CHAMPION_SYSTEM_PROMPT + "\n\n" + CHAMPION_JSON_INSTRUCTIONS,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Find the admin + GM champions at '{company_name}' in "
                        f"Saudi Arabia using publicly available LinkedIn profile "
                        f"data. Return one JSON object."
                    ),
                }
            ],
        )
        text = "".join(
            b.text for b in response.content if getattr(b, "type", None) == "text"
        ).strip()
        return ChampionBundle.model_validate_json(_extract_json_object(text))


class StubAnthropicChampionSource:
    """Fixture-backed champion fallback. Reuses apollo/linkedin fixtures."""

    name = "anthropic_champions"

    def __init__(self, fixtures_dir: Path | None = None):
        self.fixtures_dir = (
            fixtures_dir
            or Path(__file__).parent.parent.parent.parent / "fixtures" / "linkedin"
        )

    def find(self, company_name: str) -> Champions:
        path = self.fixtures_dir / f"{_slug(company_name)}.json"
        if not path.exists():
            return Champions()
        data = json.loads(path.read_text())
        admin = _fixture_champion(data.get("admin"))
        gm = _fixture_champion(data.get("gm"))
        return Champions(
            admin=admin,
            admin_is_overwhelmed=bool(data.get("admin_is_overwhelmed")),
            gm=gm,
            gm_is_new=_is_new_role(gm),
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _slug(name: str) -> str:
    return "".join(c.lower() if c.isalnum() else "-" for c in name).strip("-")


def _safe_enum(enum_cls, raw: str, default):
    try:
        return enum_cls(raw)
    except (ValueError, KeyError):
        return default


def _flat_to_champion(
    name: str, title: str, linkedin_url: str, start_date_iso: str
) -> Champion | None:
    if not name:
        return None
    start = None
    if start_date_iso:
        try:
            start = date.fromisoformat(start_date_iso)
        except ValueError:
            start = None
    return Champion(
        name=name,
        title=title,
        linkedin_url=linkedin_url,
        start_date=start,
    )


def _extract_json_object(text: str) -> str:
    """Return the first balanced {...} block. Claude with web_search sometimes
    wraps the JSON in a short preamble — be tolerant."""
    start = text.find("{")
    if start == -1:
        raise ValueError(f"no JSON object in Claude response: {text[:200]!r}")
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    raise ValueError(f"unterminated JSON object in Claude response: {text[:200]!r}")


def _fixture_champion(d: dict | None) -> Champion | None:
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
