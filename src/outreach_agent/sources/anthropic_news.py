"""News + signal research via Claude API with server-side web_search.

Replaces SerpAPI. One Claude call per company does the searching, reads the
hits, and returns a typed signal bundle — covering #18 new GM / senior hire,
#19 funding+KSA overlap, #20 KSA vs general news, #21 partnerships/social.

Why Claude over SerpAPI:
  * Single structured call vs. three keyword searches + client-side parsing.
  * The model can read the full article snippet and disambiguate (e.g. "Acme
    raised $10M" vs. "Acme raised $10M *and will expand to Riyadh*").
  * Prompt caching amortizes the research-rubric system prompt across a batch.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import BaseModel, Field

from ..models import WebIntelligence

log = logging.getLogger(__name__)


# The research rubric is large, stable, and reused across every lead in a
# batch — prime candidate for prompt caching. Volatile content (company name)
# goes in the user message.
SYSTEM_PROMPT = """\
You are a B2B research analyst for AstroLabs, a KSA business-setup firm.
For each company you will receive, use the web_search tool to look up news
from the last 6 months and return a strict JSON signal bundle.

Search strategy (run each as a separate web_search):
  1. "<company>" "Saudi Arabia" OR "KSA" OR "Riyadh"  -> KSA-specific news
  2. "<company>" funding OR raised OR "Series"        -> funding round news
  3. "<company>" "new general manager" OR "GM" OR "Country Manager" KSA

Scoring rules (map every signal to a boolean, default False if unclear):
  - ksa_news_last_6mo: TRUE only if ≥1 article from the last 6 months mentions
    the company AND contains a KSA geography term (Saudi, KSA, Riyadh, Jeddah,
    Dammam, NEOM, Vision 2030).
  - general_news_last_6mo: TRUE only if there is recent (≤6mo) company news but
    none of it is KSA-specific. Set FALSE when ksa_news_last_6mo is TRUE.
  - funding_last_12mo: TRUE if any article in the last 12 months announces a
    funding round, investment, seed, Series A/B/C, or valuation event.
  - funding_mentions_ksa: TRUE ONLY if a funding article ALSO mentions KSA
    expansion, a KSA office, or naming KSA as a target market. Not just any
    mention of the region.
  - ksa_news_mentions_partnership: TRUE if KSA news includes a partnership, MoU,
    agreement, or joint venture with a Saudi entity.
  - partnership_with_saudi_entity: TRUE if you can name a specific Saudi
    company / ministry / PIF portfolio / VC in a partnership. Higher bar than
    just a mention.
  - social_mention_by_saudi_partner: TRUE if a Saudi-based partner publicly
    mentioned or tagged the company on social media / press.
  - new_gm_within_6mo: TRUE if a General Manager / Country Manager for KSA was
    announced or started in the last 6 months.
  - senior_hire_within_6mo: TRUE if any VP / Director / C-level hire in KSA was
    announced in the last 6 months (use instead of new_gm_within_6mo if there
    is no GM-level hire).

Be conservative. Unclear or absent evidence → FALSE. Do not fabricate hits.
"""


class CompanySignals(BaseModel):
    """What the model returns. Maps 1-1 onto WebIntelligence fields."""

    ksa_news_last_6mo: bool = Field(description="KSA-geo news in last 6 months")
    general_news_last_6mo: bool = Field(description="Non-KSA company news in last 6mo")
    funding_last_12mo: bool = Field(description="Funding round in last 12 months")
    funding_mentions_ksa: bool = Field(description="Funding article also mentions KSA")
    partnership_with_saudi_entity: bool = Field(description="Named Saudi partner")
    social_mention_by_saudi_partner: bool = Field(description="Saudi partner tagged/mentioned publicly")
    new_gm_within_6mo: bool = Field(description="New KSA GM/Country Manager in 6mo")
    senior_hire_within_6mo: bool = Field(description="Senior KSA hire (VP/Dir/C-level) in 6mo")


class AnthropicNewsSource:
    """Live news/signal research via Claude API + web_search."""

    name = "anthropic_news"

    def __init__(self, api_key: str, model: str = "claude-opus-4-7"):
        if not api_key:
            raise ValueError("Anthropic news source requires an API key")
        # Import lazily so the stub path doesn't need anthropic installed.
        from anthropic import Anthropic

        self.client = Anthropic(api_key=api_key)
        self.model = model

    def enrich(self, company_name: str, web: WebIntelligence) -> None:
        try:
            signals = self._research(company_name)
        except Exception as exc:
            log.warning("anthropic_news failed for %s: %s", company_name, exc)
            web.sources_failed.append(self.name)
            return

        web.ksa_news_last_6mo = signals.ksa_news_last_6mo
        # Mutually exclusive per the rubric; enforce client-side too.
        web.general_news_last_6mo = signals.general_news_last_6mo and not signals.ksa_news_last_6mo
        web.funding_last_12mo = signals.funding_last_12mo
        web.funding_mentions_ksa = signals.funding_mentions_ksa
        web.partnership_with_saudi_entity = signals.partnership_with_saudi_entity
        web.social_mention_by_saudi_partner = signals.social_mention_by_saudi_partner
        web.new_gm_within_6mo = signals.new_gm_within_6mo
        web.senior_hire_within_6mo = signals.senior_hire_within_6mo
        web.sources_seen.append(self.name)

    def _research(self, company_name: str) -> CompanySignals:
        response = self.client.messages.parse(
            model=self.model,
            max_tokens=4096,
            thinking={"type": "adaptive"},
            tools=[{"type": "web_search_20260209", "name": "web_search"}],
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Research the company '{company_name}' and return the signal "
                        f"bundle. Focus on KSA (Saudi Arabia) activity in the last 6 "
                        f"months; funding in the last 12 months."
                    ),
                }
            ],
            output_format=CompanySignals,
        )
        return response.parsed_output


class StubAnthropicNewsSource:
    """Fixture-backed news source for dev/CI. Reads the same fixture shape as the
    old SerpAPI stub so existing fixtures keep working."""

    name = "anthropic_news"

    def __init__(self, fixtures_dir: Path | None = None):
        self.fixtures_dir = (
            fixtures_dir
            or Path(__file__).parent.parent.parent.parent / "fixtures" / "news"
        )

    def enrich(self, company_name: str, web: WebIntelligence) -> None:
        path = self.fixtures_dir / f"{_slug(company_name)}.json"
        if not path.exists():
            return
        data = json.loads(path.read_text())
        web.ksa_news_last_6mo = data.get("ksa_news_last_6mo", False)
        web.general_news_last_6mo = data.get("general_news_last_6mo", False)
        web.funding_mentions_ksa = data.get("funding_mentions_ksa", False)
        web.partnership_with_saudi_entity = data.get("partnership_with_saudi_entity", False)
        web.social_mention_by_saudi_partner = data.get(
            "social_mention_by_saudi_partner", False
        )
        web.new_gm_within_6mo = data.get("new_gm_within_6mo", False)
        web.senior_hire_within_6mo = data.get("senior_hire_within_6mo", False)
        web.sources_seen.append(self.name)


def _slug(name: str) -> str:
    return "".join(c.lower() if c.isalnum() else "-" for c in name).strip("-")
