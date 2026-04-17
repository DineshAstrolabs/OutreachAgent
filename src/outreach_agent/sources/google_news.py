"""Google News via SerpAPI — KSA news, funding/KSA overlap, partnerships.

Covers #19 funding+KSA overlap, #20 news, #21 partnerships.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx

from ..models import WebIntelligence

log = logging.getLogger(__name__)

KSA_TERMS = (
    "saudi arabia", "ksa", "riyadh", "jeddah", "dammam", "neom", "vision 2030"
)
PARTNERSHIP_TERMS = ("partnership", "mou", "agreement", "signed with", "collaboration")


class SerpAPISource:
    name = "google_news"

    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError("SerpAPI requires an API key")
        self.api_key = api_key
        self.http = httpx.Client(timeout=30.0)

    def enrich(self, company_name: str, web: WebIntelligence) -> None:
        try:
            articles = self._search(f'"{company_name}" Saudi Arabia', tbs="qdr:m6")
            web.ksa_news_last_6mo = len(articles) > 0
            for a in articles:
                blob = f"{a.get('title','')} {a.get('snippet','')}".lower()
                if "funding" in blob or "raised" in blob or "series" in blob:
                    web.funding_mentions_ksa = True
                if any(term in blob for term in PARTNERSHIP_TERMS):
                    web.partnership_with_saudi_entity = True

            if not web.ksa_news_last_6mo:
                general = self._search(f'"{company_name}"', tbs="qdr:m6")
                web.general_news_last_6mo = len(general) > 0

            web.sources_seen.append(self.name)
        except Exception as exc:
            log.warning("serpapi failed: %s", exc)
            web.sources_failed.append(self.name)

    def _search(self, query: str, tbs: str) -> list[dict]:
        resp = self.http.get(
            "https://serpapi.com/search.json",
            params={
                "api_key": self.api_key, "q": query, "tbm": "nws",
                "tbs": tbs, "hl": "en",
            },
        )
        resp.raise_for_status()
        return resp.json().get("news_results", [])


class StubGoogleNewsSource:
    name = "google_news"

    def __init__(self, fixtures_dir: Path | None = None):
        self.fixtures_dir = fixtures_dir or Path(__file__).parent.parent.parent.parent / "fixtures" / "news"

    def enrich(self, company_name: str, web: WebIntelligence) -> None:
        path = self.fixtures_dir / f"{_slug(company_name)}.json"
        if not path.exists():
            return
        data = json.loads(path.read_text())
        web.ksa_news_last_6mo = data.get("ksa_news_last_6mo", False)
        web.general_news_last_6mo = data.get("general_news_last_6mo", False)
        web.funding_mentions_ksa = data.get("funding_mentions_ksa", False)
        web.partnership_with_saudi_entity = data.get("partnership_with_saudi_entity", False)
        web.social_mention_by_saudi_partner = data.get("social_mention_by_saudi_partner", False)
        web.new_gm_within_6mo = data.get("new_gm_within_6mo", False)
        web.senior_hire_within_6mo = data.get("senior_hire_within_6mo", False)
        web.sources_seen.append(self.name)


def _slug(name: str) -> str:
    return "".join(c.lower() if c.isalnum() else "-" for c in name).strip("-")
