"""MC Commercial Registration public lookup.

https://mc.gov.sa/en/eservices/Pages/Commercial-data.aspx

The page is a SharePoint ASP.NET form with a BotDetect image captcha. Two
things have to be right for a live query to succeed:

  1. FORM SUBMISSION — SharePoint needs every hidden `__*` field returned
     intact (__VIEWSTATE, __VIEWSTATEGENERATOR, __EVENTVALIDATION,
     __REQUESTVERIFICATIONTOKEN, plus the BotDetect BDC_VCID_* hidden
     field). We collect ALL hidden inputs from the initial page and inject
     our values over the top. Input IDs on the page use long ctl00_ctl71_g_
     prefixes that rotate — we look them up by suffix match
     (UnifiedNumber, txtCaptcha, btnSearch).

  2. RESULT PARSING — the result is NOT a `<table>`. It's a styled "CR
     Records" card with div-based label/value pairs (Business Type,
     CR Status, CR Number, Capital, Issue date, Activities, ...). Fixed
     selectors are brittle; we hand the HTML to Claude (`messages.parse`)
     and let a typed `MCParsed` bundle come back. Claude is resilient to
     layout tweaks and also recognizes the "No Results" case.

NOTE: if MC switches from BotDetect to reCAPTCHA this module needs a
second captcha path (reCAPTCHA requires 2Captcha token-mode or headless
browser — Tesseract/Claude vision won't work for that).
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path

import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel

from ..captcha import CaptchaSolver
from ..models import CRStatus, EntityType, MCData

log = logging.getLogger(__name__)

MC_URL = "https://mc.gov.sa/en/eservices/Pages/Commercial-data.aspx"
MC_ORIGIN = "https://mc.gov.sa"

# Dump failed HTML here so the user can inspect the actual response.
DEBUG_DIR = Path(".outreach-debug")


MC_PARSE_SYSTEM_PROMPT = """\
You are parsing the HTML response from Saudi Arabia's Ministry of Commerce
public CR lookup (mc.gov.sa/en/eservices/Pages/Commercial-data.aspx).

Extract EVERY field on the first CR Record shown. The card uses English
labels (sometimes with Arabic duplicates). Map each label to the schema:

  Label on page              Schema field
  ─────────────────────────  ────────────────────────────
  (title of the card)        company_legal_name
  National No / Unified No   (unified_number — set server-side, ignore)
  Business Type              business_type_raw   (verbatim string)
  CR Status                  cr_status
  Company Duration           company_duration_years   (integer)
  CR Number                  cr_number
  Url Address                website_url         (may be blank)
  Capital                    registered_capital_sar   (integer SAR)
  phone                      phone
  Issue date                 cr_issue_date       (YYYY-MM-DD)
  Expiry date / CR Expiry    cr_expiry_date      (YYYY-MM-DD)
  Activities                 activities          (full text, verbatim)
  City / Region              city / region
  ISIC activity code         business_activity_isic

Also detect these outcome states:
  - If the page shows "no results", "No Records", a validation summary like
    "Please enter the verification code", or the result card is absent ->
    return found=false, status="Not Found", company_legal_name="".
  - If captcha was wrong, MC re-renders the form without a CR Records card
    -> same as NOT_FOUND.

Map cr_status to one of: Active, Expired, Cancelled, Struck Off, Not Found.
Map entity_type to one of: LLC foreign, Branch of foreign co, LLC Saudi,
Sole proprietorship, Government, Semi-government, Unknown. Use MC's
"Business Type" field as the primary signal, combined with activities /
foreign ownership cues. If you cannot tell, return "Unknown".

Always preserve `business_type_raw` exactly as MC prints it, even when you
also fill `entity_type` with a normalized value.

Dates: YYYY-MM-DD. Integers: integers only (strip SAR / commas / currency).
`activities` may be long — preserve it in full, do NOT truncate.
"""


_JSON_INSTRUCTIONS = """\
Respond with ONE JSON object and nothing else — no markdown fence, no prose.

Use exactly these keys (all required; use "" or 0 when the field is absent):
  found                  boolean
  company_legal_name     string
  cr_status              string
  entity_type            string
  business_type_raw      string
  cr_number              string
  cr_issue_date          string   (YYYY-MM-DD or "")
  cr_expiry_date         string   (YYYY-MM-DD or "")
  company_duration_years integer
  business_activity_isic string
  activities             string
  registered_capital_sar integer  (SAR, no separators)
  phone                  string
  website_url            string
  city                   string
  region                 string
"""


class MCParsed(BaseModel):
    """Normalized view of the MC CR page result.

    Kept intentionally flat — the Anthropic `messages.parse` JSON schema has
    a complexity ceiling, so each field is a plain scalar with no per-field
    metadata. Label→field semantics are described in MC_PARSE_SYSTEM_PROMPT.
    """

    found: bool = False
    company_legal_name: str = ""
    cr_status: str = "Not Found"
    entity_type: str = "Unknown"
    business_type_raw: str = ""
    cr_number: str = ""
    cr_issue_date: str = ""
    cr_expiry_date: str = ""
    company_duration_years: int = 0
    business_activity_isic: str = ""
    activities: str = ""
    registered_capital_sar: int = 0
    phone: str = ""
    website_url: str = ""
    city: str = ""
    region: str = ""


class MCSource:
    """Live MC scraper with dynamic form-field discovery + Claude HTML parser."""

    def __init__(
        self,
        captcha_solver: CaptchaSolver,
        anthropic_api_key: str | None = None,
        http: httpx.Client | None = None,
        save_debug_html: bool = True,
        captcha_attempts: int = 3,
    ):
        self.captcha = captcha_solver
        self.http = http or httpx.Client(
            timeout=30.0,
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/122.0 Safari/537.36"
                ),
            },
        )
        self.save_debug_html = save_debug_html
        self.captcha_attempts = max(1, captcha_attempts)

        self._claude = None
        if anthropic_api_key:
            try:
                from anthropic import Anthropic

                self._claude = Anthropic(api_key=anthropic_api_key)
            except ImportError:
                log.warning("anthropic SDK missing; MC parser will use regex fallback")

    def fetch(self, unified_number: str) -> MCData:
        # Captchas get misread. Retry up to `captcha_attempts` times with a
        # fresh page + VCID on every attempt, so a single OCR fluke doesn't
        # produce a false NOT_FOUND.
        last_html: str | None = None
        for attempt in range(1, self.captcha_attempts + 1):
            log.info("[MC] ===== attempt %d/%d =====", attempt, self.captcha_attempts)
            parsed, html = self._attempt(unified_number)
            last_html = html
            if parsed is None:
                # Hard failure (missing form fields / captcha image missing).
                return _not_found(unified_number)
            if parsed.found:
                _log_mcparsed(parsed)
                mc_data = _mcparsed_to_mcdata(unified_number, parsed)
                _log_mcdata(mc_data)
                return mc_data
            log.info(
                "[MC] attempt %d: no CR record card — likely bad captcha, will retry",
                attempt,
            )

        log.info("[MC] exhausted %d captcha attempts; giving up", self.captcha_attempts)
        if last_html is not None:
            self._maybe_dump(unified_number, last_html)
        return _not_found(unified_number)

    def _attempt(self, unified_number: str) -> tuple[MCParsed | None, str | None]:
        """Single GET → solve → POST → parse cycle. Returns (parsed, html).
        parsed is None for hard failures; html is None when we didn't POST."""
        log.info("[MC] step 1: GET initial form page %s", MC_URL)
        t0 = time.monotonic()
        page = self.http.get(MC_URL)
        page.raise_for_status()
        log.info("[MC] form page: %d bytes in %.2fs", len(page.text), time.monotonic() - t0)
        soup = BeautifulSoup(page.text, "html.parser")

        log.info("[MC] step 2: parsing hidden inputs + locating form fields")
        hidden_fields = _collect_hidden_inputs(soup)
        form_fields = _locate_form_fields(soup)
        log.info(
            "[MC] hidden fields collected: %d (keys: %s)",
            len(hidden_fields),
            sorted(hidden_fields.keys())[:12],
        )
        log.info(
            "[MC] form fields resolved: unified=%s captcha=%s submit=%s",
            form_fields.unified_number,
            form_fields.captcha,
            form_fields.submit,
        )
        _log_form_landscape(soup)
        vcid = _find_vcid(hidden_fields)
        if vcid:
            log.info("[MC] BotDetect VCID from hidden field: %s=%s", vcid[0], vcid[1])

        if not form_fields.unified_number or not form_fields.captcha or not form_fields.submit:
            log.warning(
                "[MC] could not locate all form fields (unified=%s, captcha=%s, submit=%s) — "
                "page structure likely changed",
                form_fields.unified_number,
                form_fields.captcha,
                form_fields.submit,
            )
            self._maybe_dump(unified_number, page.text, suffix="form")
            return None, page.text

        log.info("[MC] step 3: locating captcha image")
        captcha_img = soup.select_one("img[src*='BotDetectCaptcha'], img[id*='Captcha']")
        if captcha_img is None:
            log.warning("[MC] captcha image not located on page; MC may have changed")
            return None, page.text

        img_src = captcha_img.get("src", "")
        img_url = img_src if img_src.startswith("http") else f"{MC_ORIGIN}{img_src}"
        log.info("[MC] captcha image URL: %s", img_url)

        log.info("[MC] step 4: downloading captcha image")
        t0 = time.monotonic()
        img_bytes = self.http.get(img_url, headers={"Referer": MC_URL}).content
        log.info("[MC] captcha image: %d bytes in %.2fs", len(img_bytes), time.monotonic() - t0)

        log.info("[MC] step 5: solving captcha via provider %r", self.captcha.name)
        t0 = time.monotonic()
        captcha_answer = self.captcha.solve_image(img_bytes)
        log.info("[MC] captcha solved to %r in %.2fs", captcha_answer, time.monotonic() - t0)

        log.info("[MC] step 6: composing form payload")
        payload = dict(hidden_fields)
        payload[form_fields.unified_number] = unified_number
        payload[form_fields.captcha] = captcha_answer
        payload[form_fields.submit] = "Search"
        log.info(
            "[MC] payload: %d fields, unified-field=%s, captcha-field=%s, submit-field=%s",
            len(payload),
            form_fields.unified_number,
            form_fields.captcha,
            form_fields.submit,
        )

        log.info("[MC] step 7: POST form to %s", MC_URL)
        t0 = time.monotonic()
        submit = self.http.post(
            MC_URL,
            data=payload,
            headers={
                "Referer": MC_URL,
                "Origin": MC_ORIGIN,
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        submit.raise_for_status()
        log.info(
            "[MC] response: status=%d bytes=%d in %.2fs",
            submit.status_code, len(submit.text), time.monotonic() - t0,
        )

        log.info("[MC] step 8: parsing result HTML")
        parsed = self._parse_html(submit.text)
        return parsed, submit.text

    # ---- result parsing --------------------------------------------------

    def _parse_html(self, html: str) -> MCParsed:
        parsed: MCParsed | None = None

        if self._claude is not None:
            log.info("[MC] parsing via Claude (claude-opus-4-7)")
            t0 = time.monotonic()
            try:
                parsed = self._parse_with_claude(html)
                log.info(
                    "[MC] Claude parse ok in %.2fs: found=%s status=%s name=%r",
                    time.monotonic() - t0,
                    parsed.found, parsed.cr_status, parsed.company_legal_name,
                )
            except Exception as exc:
                log.warning("[MC] Claude MC parser failed: %s — falling back to regex", exc)

        if parsed is None:
            log.info("[MC] parsing via regex fallback")
            parsed = _parse_with_regex(html)
            log.info(
                "[MC] regex parse: found=%s status=%s name=%r",
                parsed.found, parsed.cr_status, parsed.company_legal_name,
            )

        return parsed

    def _parse_with_claude(self, html: str) -> MCParsed:
        # We don't use messages.parse here: strict-schema mode rejects our
        # 16-field model as "Schema is too complex". Use plain messages.create
        # with a JSON-only instruction and validate the response ourselves.
        trimmed = _strip_html_noise(html)
        resp = self._claude.messages.create(
            model="claude-opus-4-7",
            max_tokens=2048,
            system=[
                {
                    "type": "text",
                    "text": MC_PARSE_SYSTEM_PROMPT + "\n\n" + _JSON_INSTRUCTIONS,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Extract the first CR Record from this HTML. Return a "
                        "single JSON object only. Use found=false if no record "
                        "card is present.\n\n"
                        f"<html>\n{trimmed}\n</html>"
                    ),
                }
            ],
        )
        text = "".join(
            b.text for b in resp.content if getattr(b, "type", None) == "text"
        ).strip()
        return MCParsed.model_validate_json(_extract_json_object(text))

    def _maybe_dump(
        self, unified_number: str, html: str, suffix: str = "result"
    ) -> None:
        if not self.save_debug_html:
            return
        try:
            DEBUG_DIR.mkdir(exist_ok=True)
            path = (
                DEBUG_DIR
                / f"mc-{unified_number}-{int(datetime.utcnow().timestamp())}-{suffix}.html"
            )
            path.write_text(html, encoding="utf-8")
            log.info("dumped MC response to %s for inspection", path)
        except OSError as exc:
            log.warning("failed to dump MC debug HTML: %s", exc)


# ---------------------------------------------------------------------------
# Form-field discovery
# ---------------------------------------------------------------------------


class _FormFields:
    __slots__ = ("unified_number", "captcha", "submit")

    def __init__(
        self,
        unified_number: str | None,
        captcha: str | None,
        submit: str | None,
    ):
        self.unified_number = unified_number
        self.captcha = captcha
        self.submit = submit


def _collect_hidden_inputs(soup: BeautifulSoup) -> dict[str, str]:
    """Every hidden input on the page — includes __VIEWSTATE, __VIEWSTATEGENERATOR,
    __EVENTVALIDATION, __EVENTTARGET, __EVENTARGUMENT, __REQUESTVERIFICATIONTOKEN,
    and BotDetect's BDC_VCID_* companion field."""
    out: dict[str, str] = {}
    for inp in soup.select("input[type='hidden']"):
        name = inp.get("name")
        if name:
            out[name] = inp.get("value", "")
    return out


def _locate_form_fields(soup: BeautifulSoup) -> _FormFields:
    """Match by suffix since SharePoint prefixes rotate per page render
    (ctl00_ctl71_g_<guid>_ctl00_...). For the Unified Number input we fall
    back to "the first visible text input that isn't the captcha" since
    MC's form has exactly two visible text fields and the Unified Number
    one has historically had different names per page redesign
    (txtSearch / txtUnifiedNumber / txtNationalNumber / txtCRNumber …)."""
    captcha = _find_input_by_suffix(
        soup,
        (
            "txtCaptcha",
            "CaptchaCodeTextBox",
            "exampleCaptcha$CaptchaCodeTextBox",
        ),
    )
    submit = _find_submit_by_suffix(
        soup, ("btnSearch", "btnSubmit", "Search")
    )
    unified = _find_input_by_suffix(
        soup,
        (
            "UnifiedNumber",
            "txtUnifiedNumber",
            "txtNationalNumber",
            "txtNationalNo",
            "txtCRNumber",
            "NationalNumberTextBox",
            "txtCRName",       # MC's actual field name — accepts CR# / Unified# / name
            "txtSearch",
        ),
    )
    if unified is None:
        # Last-resort heuristic: take the only other visible text input.
        unified = _first_text_input_excluding(soup, captcha)
        if unified is not None:
            log.info("unified-number field discovered by heuristic: %s", unified)

    return _FormFields(unified, captcha, submit)


def _find_input_by_suffix(soup: BeautifulSoup, suffixes: tuple[str, ...]) -> str | None:
    for inp in soup.select("input[type='text'], input:not([type])"):
        name = inp.get("name") or ""
        if any(name.endswith(s) or s in name for s in suffixes):
            return name
    return None


def _find_submit_by_suffix(soup: BeautifulSoup, suffixes: tuple[str, ...]) -> str | None:
    for inp in soup.select("input[type='submit'], input[type='button'], button"):
        name = inp.get("name") or ""
        if any(name.endswith(s) or s in name for s in suffixes):
            return name
    return None


def _log_form_landscape(soup: BeautifulSoup) -> None:
    """Log every visible input, radio group, and select on the page. When MC
    rotates field names or adds a search-type selector, this is the quickest
    way to see what's actually on the page without eyeballing raw HTML."""
    text_inputs = []
    for inp in soup.select("input"):
        t = (inp.get("type") or "text").lower()
        if t in ("hidden", "image"):
            continue
        name = inp.get("name") or ""
        input_id = inp.get("id") or ""
        if not name:
            continue
        label = _label_for(soup, input_id)
        text_inputs.append(f"[{t}] name={name!s} id={input_id!s} label={label!r}")
    if text_inputs:
        log.info("MC form visible inputs:\n  %s", "\n  ".join(text_inputs))

    selects = []
    for sel in soup.select("select"):
        name = sel.get("name") or ""
        options = [o.get_text(strip=True) for o in sel.select("option")]
        if name:
            selects.append(f"name={name!s} options={options!r}")
    if selects:
        log.info("MC form selects:\n  %s", "\n  ".join(selects))


def _label_for(soup: BeautifulSoup, input_id: str) -> str:
    if not input_id:
        return ""
    lbl = soup.select_one(f"label[for='{input_id}']")
    return lbl.get_text(" ", strip=True) if lbl else ""


def _first_text_input_excluding(
    soup: BeautifulSoup, exclude_name: str | None
) -> str | None:
    """Return the first visible text input whose name isn't the captcha's.
    MC renders the Unified Number input above the captcha input; either order
    works because there are only two visible text fields."""
    for inp in soup.select("input[type='text'], input:not([type])"):
        name = inp.get("name") or ""
        input_type = (inp.get("type") or "text").lower()
        if input_type in ("hidden", "submit", "button", "image"):
            continue
        if not name or name == exclude_name:
            continue
        return name
    return None


# ---------------------------------------------------------------------------
# HTML preprocessing for the Claude parser
# ---------------------------------------------------------------------------


def _extract_json_object(text: str) -> str:
    """Return the first balanced {...} block in `text`.

    Claude occasionally wraps JSON output in a ```json fence or adds a short
    explanatory sentence despite instructions. Walk the string and return
    the first top-level object so `model_validate_json` gets a clean input.
    """
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


def _strip_html_noise(html: str) -> str:
    """Keep the MC content area, strip scripts/styles/nav so Claude sees less
    noise. Falls through to raw HTML if trimming throws."""
    try:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript", "svg", "iframe", "link"]):
            tag.decompose()
        main = (
            soup.select_one("#PlaceHolderMain, #s4-workspace, main, #ctl00_PlaceHolderMain")
            or soup.body
            or soup
        )
        text = str(main)
        # Hard cap to keep the request small + cacheable.
        if len(text) > 60_000:
            text = text[:60_000]
        return text
    except Exception:
        return html[:60_000]


# ---------------------------------------------------------------------------
# Regex fallback (no Anthropic key configured)
# ---------------------------------------------------------------------------


_REGEX_LABELS = {
    "cr_status": r"CR\s*Status\s*[:<>]?\s*([^<\n]{1,40})",
    "cr_number": r"CR\s*Number\s*[:<>]?\s*([0-9]{5,})",
    "business_type": r"Business\s*Type\s*[:<>]?\s*([^<\n]{1,60})",
    "capital": r"Capital\s*[:<>]?\s*([0-9,\.]+)",
    "duration": r"Company\s*Duration\s*[:<>]?\s*([0-9]{1,4})",
    "issue": r"Issue\s*date\s*[:<>]?\s*([0-9\-/]{8,12})",
    "expiry": r"(?:Expiry\s*date|CR\s*Expiry)\s*[:<>]?\s*([0-9\-/]{8,12})",
    "phone": r"phone\s*[:<>]?\s*([0-9+\-\s()]{6,20})",
    "website": r"Url\s*Address\s*[:<>]?\s*((?:https?://)?[^\s<]{3,200})",
    "activities": r"Activities\s*[:<>]?\s*([^<]{3,4000})",
    "company_name": r"<h[1-6][^>]*>\s*([^<]{3,200})\s*</h[1-6]>",
}


def _parse_with_regex(html: str) -> MCParsed:
    cleaned = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)

    def grab(key: str) -> str:
        m = re.search(_REGEX_LABELS[key], cleaned, re.IGNORECASE)
        return m.group(1).strip() if m else ""

    status_raw = grab("cr_status")
    has_any = any(
        kw.lower() in cleaned.lower() for kw in ("CR Records", "National No", "CR Details")
    )
    return MCParsed(
        found=has_any and bool(status_raw),
        company_legal_name=grab("company_name"),
        cr_status=status_raw or "Not Found",
        entity_type="Unknown",
        business_type_raw=grab("business_type"),
        cr_number=grab("cr_number"),
        cr_issue_date=grab("issue"),
        cr_expiry_date=grab("expiry"),
        company_duration_years=_first_int(grab("duration")),
        activities=grab("activities"),
        registered_capital_sar=_first_int(grab("capital")),
        phone=grab("phone"),
        website_url=grab("website"),
    )


def _first_int(s: str) -> int:
    if not s:
        return 0
    digits = re.sub(r"[^\d]", "", s)
    return int(digits) if digits else 0


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

            def _d(key):
                return datetime.strptime(data[key], "%Y-%m-%d").date() if data.get(key) else None

            return MCData(
                unified_number=unified_number,
                company_legal_name=data["company_legal_name"],
                cr_status=CRStatus(data["cr_status"]),
                entity_type=EntityType(data["entity_type"]),
                cr_number=data.get("cr_number"),
                cr_issue_date=_d("cr_issue_date"),
                cr_expiry_date=_d("cr_expiry_date"),
                business_type_raw=data.get("business_type_raw"),
                company_duration_years=data.get("company_duration_years"),
                business_activity_isic=data.get("business_activity_isic"),
                activities=data.get("activities"),
                registered_capital_sar=data.get("registered_capital_sar"),
                phone=data.get("phone"),
                website_url=data.get("website_url"),
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
# Shared helpers
# ---------------------------------------------------------------------------


_STATUS_MAP = {
    "active": CRStatus.ACTIVE,
    "expired": CRStatus.EXPIRED,
    "cancelled": CRStatus.CANCELLED,
    "canceled": CRStatus.CANCELLED,
    "struck off": CRStatus.STRUCK_OFF,
    "struck-off": CRStatus.STRUCK_OFF,
    "not found": CRStatus.NOT_FOUND,
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


def _log_mcparsed(parsed: MCParsed) -> None:
    """One INFO line per MC field so we can eyeball what Claude / regex
    pulled from the CR card. Keeps field labels aligned with what the
    MC page actually prints so a diff against a browser view is trivial."""
    rows = [
        ("company_legal_name",     parsed.company_legal_name),
        ("cr_status",              parsed.cr_status),
        ("entity_type",            parsed.entity_type),
        ("business_type_raw",      parsed.business_type_raw),
        ("cr_number",              parsed.cr_number),
        ("cr_issue_date",          parsed.cr_issue_date),
        ("cr_expiry_date",         parsed.cr_expiry_date),
        ("company_duration_years", parsed.company_duration_years),
        ("business_activity_isic", parsed.business_activity_isic),
        ("activities",             _truncate(parsed.activities, 240)),
        ("registered_capital_sar", parsed.registered_capital_sar),
        ("phone",                  parsed.phone),
        ("website_url",            parsed.website_url),
        ("city",                   parsed.city),
        ("region",                 parsed.region),
    ]
    log.info("[MC] extracted fields (from parser):")
    for name, value in rows:
        log.info("  %-24s = %r", name, value)
    missing = [n for n, v in rows if v in ("", 0, None)]
    if missing:
        log.info("[MC] fields returned empty: %s", ", ".join(missing))


def _log_mcdata(mc: MCData) -> None:
    """Post-normalization snapshot so it's obvious when _parse_date or
    _parse_entity_type coerced a raw value into something different."""
    log.info("[MC] normalized MCData:")
    log.info("  %-24s = %s", "unified_number",         mc.unified_number)
    log.info("  %-24s = %r", "company_legal_name",     mc.company_legal_name)
    log.info("  %-24s = %s", "cr_status",              mc.cr_status.value)
    log.info("  %-24s = %s", "entity_type",            mc.entity_type.value)
    log.info("  %-24s = %r", "business_type_raw",      mc.business_type_raw)
    log.info("  %-24s = %r", "cr_number",              mc.cr_number)
    log.info("  %-24s = %s", "cr_issue_date",          mc.cr_issue_date)
    log.info("  %-24s = %s", "cr_expiry_date",         mc.cr_expiry_date)
    log.info("  %-24s = %r", "company_duration_years", mc.company_duration_years)
    log.info("  %-24s = %r", "business_activity_isic", mc.business_activity_isic)
    log.info("  %-24s = %r", "activities",             _truncate(mc.activities, 240))
    log.info("  %-24s = %r", "registered_capital_sar", mc.registered_capital_sar)
    log.info("  %-24s = %r", "phone",                  mc.phone)
    log.info("  %-24s = %r", "website_url",            mc.website_url)
    log.info("  %-24s = %r", "city",                   mc.city)
    log.info("  %-24s = %r", "region",                 mc.region)


def _truncate(s: str | None, limit: int) -> str | None:
    if s is None:
        return None
    if len(s) <= limit:
        return s
    return s[:limit] + f"…(+{len(s) - limit} chars)"


def _not_found(unified_number: str) -> MCData:
    return MCData(
        unified_number=unified_number,
        company_legal_name="",
        cr_status=CRStatus.NOT_FOUND,
        entity_type=EntityType.UNKNOWN,
    )


def _mcparsed_to_mcdata(unified_number: str, parsed: MCParsed) -> MCData:
    # MCParsed uses empty strings / 0 as sentinels (to keep the JSON schema
    # simple for messages.parse). MCData uses None. Normalize here.
    def _s(v: str) -> str | None:
        return v or None

    def _i(v: int) -> int | None:
        return v if v else None

    # When Claude only returned business_type_raw but not a normalized
    # entity_type, run our own classifier on the raw string so downstream
    # gates still work.
    entity_type = _parse_entity_type(parsed.entity_type or parsed.business_type_raw or "")
    return MCData(
        unified_number=unified_number,
        company_legal_name=parsed.company_legal_name,
        cr_status=_parse_status(parsed.cr_status),
        entity_type=entity_type,
        cr_number=_s(parsed.cr_number),
        cr_issue_date=_parse_date(parsed.cr_issue_date),
        cr_expiry_date=_parse_date(parsed.cr_expiry_date),
        business_type_raw=_s(parsed.business_type_raw),
        company_duration_years=_i(parsed.company_duration_years),
        business_activity_isic=_s(parsed.business_activity_isic),
        activities=_s(parsed.activities),
        registered_capital_sar=_i(parsed.registered_capital_sar),
        phone=_s(parsed.phone),
        website_url=_s(parsed.website_url),
        city=_s(parsed.city),
        region=_s(parsed.region),
    )


def _find_vcid(hidden_fields: dict[str, str]) -> tuple[str, str] | None:
    """BotDetect ships a hidden LBD_VCID_* field; MC must see the same VCID
    on POST that was set by the GET that rendered the captcha image."""
    for name, value in hidden_fields.items():
        if name.startswith("LBD_VCID_") or name.startswith("BDC_VCID_"):
            return (name, value)
    return None


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
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except (TypeError, ValueError):
            continue
    return None
