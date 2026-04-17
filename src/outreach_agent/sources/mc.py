"""MC Commercial Registration public lookup.

https://mc.gov.sa/en/eservices/Pages/Commercial-data.aspx

The page is public but gates submissions behind an image captcha. The live
client fetches the page, extracts the captcha image, solves via 2Captcha,
submits the form, and parses the result table.

NOTE: MC occasionally changes page structure and/or switches captcha type.
The parser uses defensive selectors and flags NOT_FOUND on any parse failure
rather than raising — the pipeline will mark the lead disqualified with a
clear reason rather than silently dropping it.

For development and CI we ship a StubMCSource that returns fixture records
keyed by unified number.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

from ..captcha import TwoCaptchaClient
from ..models import CRStatus, EntityType, MCData

log = logging.getLogger(__name__)

MC_URL = "https://mc.gov.sa/en/eservices/Pages/Commercial-data.aspx"


class MCSource:
    """Live MC scraper. Requires a 2Captcha API key."""

    def __init__(self, captcha_client: TwoCaptchaClient, http: httpx.Client | None = None):
        self.captcha = captcha_client
        self.http = http or httpx.Client(
            timeout=30.0,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 OutreachAgent/0.1"},
        )

    def fetch(self, unified_number: str) -> MCData:
        page = self.http.get(MC_URL)
        page.raise_for_status()
        soup = BeautifulSoup(page.text, "html.parser")

        # The MC form uses ASP.NET postback. We need to carry __VIEWSTATE and
        # __EVENTVALIDATION along with a solved captcha token. Exact field IDs
        # are subject to change — keep selectors tolerant.
        viewstate = _input_value(soup, "__VIEWSTATE")
        event_validation = _input_value(soup, "__EVENTVALIDATION")
        captcha_img = soup.select_one("img[id*='Captcha'], img[src*='captcha']")
        if captcha_img is None:
            log.warning("captcha image not located on page; MC may have changed")
            return _not_found(unified_number)

        img_src = captcha_img.get("src", "")
        img_url = img_src if img_src.startswith("http") else f"https://mc.gov.sa{img_src}"
        img_bytes = self.http.get(img_url).content
        captcha_answer = self.captcha.solve_image(img_bytes)

        submit = self.http.post(
            MC_URL,
            data={
                "__VIEWSTATE": viewstate,
                "__EVENTVALIDATION": event_validation,
                # Field names need validation against a live page inspection.
                "ctl00$PlaceHolderMain$txtUnifiedNumber": unified_number,
                "ctl00$PlaceHolderMain$txtCaptcha": captcha_answer,
                "ctl00$PlaceHolderMain$btnSearch": "Search",
            },
        )
        submit.raise_for_status()
        return self._parse_result(unified_number, submit.text)

    def _parse_result(self, unified_number: str, html: str) -> MCData:
        soup = BeautifulSoup(html, "html.parser")
        table = soup.select_one("table[id*='Result'], .cr-result")
        if table is None:
            log.info("no result table for %s", unified_number)
            return _not_found(unified_number)

        fields = {_clean(k): _clean(v) for k, v in _rows(table)}
        return MCData(
            unified_number=unified_number,
            company_legal_name=fields.get("company name") or fields.get("legal name", ""),
            cr_status=_parse_status(fields.get("status", "")),
            entity_type=_parse_entity_type(fields.get("entity type", "")),
            cr_expiry_date=_parse_date(fields.get("expiry date")),
            business_activity_isic=fields.get("isic") or fields.get("business activity"),
            registered_capital_sar=_parse_int(fields.get("capital")),
            subsidiary_cr_count=_parse_int(fields.get("subsidiaries")) or 0,
            city=fields.get("city"),
            region=fields.get("region"),
        )


# ---------------------------------------------------------------------------
# Stub for dev / CI / dry-run
# ---------------------------------------------------------------------------

class StubMCSource:
    """Fixture-backed MC source. Looks up `fixtures/mc/<unified_number>.json`.

    Falls back to a reasonable default for unknown numbers so the pipeline
    can still be demoed.
    """

    def __init__(self, fixtures_dir: Path | None = None):
        self.fixtures_dir = fixtures_dir or Path(__file__).parent.parent.parent.parent / "fixtures" / "mc"

    def fetch(self, unified_number: str) -> MCData:
        path = self.fixtures_dir / f"{unified_number}.json"
        if path.exists():
            data = json.loads(path.read_text())
            return MCData(
                unified_number=unified_number,
                company_legal_name=data["company_legal_name"],
                cr_status=CRStatus(data["cr_status"]),
                entity_type=EntityType(data["entity_type"]),
                cr_expiry_date=datetime.strptime(data["cr_expiry_date"], "%Y-%m-%d").date()
                    if data.get("cr_expiry_date") else None,
                business_activity_isic=data.get("business_activity_isic"),
                registered_capital_sar=data.get("registered_capital_sar"),
                subsidiary_cr_count=data.get("subsidiary_cr_count", 0),
                city=data.get("city"),
                region=data.get("region"),
            )
        log.warning("no fixture for %s — returning default stub", unified_number)
        return MCData(
            unified_number=unified_number,
            company_legal_name=f"Demo Foreign LLC {unified_number}",
            cr_status=CRStatus.ACTIVE,
            entity_type=EntityType.LLC_FOREIGN,
            cr_expiry_date=None,
            business_activity_isic="6201",
            registered_capital_sar=500_000,
            city="Riyadh",
            region="Riyadh",
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_STATUS_MAP = {
    "active": CRStatus.ACTIVE,
    "expired": CRStatus.EXPIRED,
    "cancelled": CRStatus.CANCELLED,
    "canceled": CRStatus.CANCELLED,
    "struck off": CRStatus.STRUCK_OFF,
    "struck-off": CRStatus.STRUCK_OFF,
}

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


def _not_found(unified_number: str) -> MCData:
    return MCData(
        unified_number=unified_number,
        company_legal_name="",
        cr_status=CRStatus.NOT_FOUND,
        entity_type=EntityType.UNKNOWN,
    )


def _input_value(soup: BeautifulSoup, name: str) -> str:
    el = soup.select_one(f"input[name='{name}']")
    return el.get("value", "") if el else ""


def _rows(table) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for tr in table.select("tr"):
        cells = tr.select("td, th")
        if len(cells) >= 2:
            out.append((cells[0].get_text(" ", strip=True), cells[1].get_text(" ", strip=True)))
    return out


def _clean(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "").strip()).lower()


def _parse_status(s: str) -> CRStatus:
    return _STATUS_MAP.get(s.lower().strip(), CRStatus.NOT_FOUND)


def _parse_entity_type(s: str) -> EntityType:
    low = s.lower().strip()
    for key, value in _ENTITY_MAP.items():
        if key in low:
            return value
    return EntityType.UNKNOWN


def _parse_date(s: str | None):
    if not s:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _parse_int(s: str | None) -> int | None:
    if not s:
        return None
    digits = re.sub(r"[^\d]", "", s)
    return int(digits) if digits else None
