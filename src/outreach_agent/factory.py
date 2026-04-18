"""Wire the pipeline up from Config. Single place where stub/live is decided.

Flag → source mapping:
  APOLLO_ENABLED=false     → AnthropicCompanySource + AnthropicChampionSource
                             take over (live) / stub fallbacks read fixtures.
  CRUNCHBASE_ENABLED=false → Crunchbase skipped; AnthropicNewsSource already
                             covers funding_last_12mo / funding_mentions_ksa.
  HUBSPOT_ENABLED=false    → No CRM write (pipeline.crm = None).
"""

from __future__ import annotations

import logging

from .captcha import build_captcha_solver
from .config import Config
from .crm.hubspot import HubSpotWriter, StubHubSpotWriter
from .pipeline import LinkedInChampionAdapter, Pipeline
from .sources.anthropic_fallbacks import (
    AnthropicChampionSource,
    AnthropicCompanySource,
    StubAnthropicChampionSource,
    StubAnthropicCompanySource,
)
from .sources.anthropic_news import AnthropicNewsSource, StubAnthropicNewsSource
from .sources.apollo import ApolloSource, StubApolloSource
from .sources.crunchbase import CrunchbaseSource, StubCrunchbaseSource
from .sources.jobs import StubJobsSource
from .sources.mc import MCSource, StubMCSource
from .sources.website import StubWebsiteSource

log = logging.getLogger(__name__)


def build_pipeline(config: Config) -> Pipeline:
    if config.is_live:
        return _build_live(config)
    return _build_stub(config)


def _build_stub(config: Config) -> Pipeline:
    web_sources = []
    if config.apollo_enabled:
        apollo = StubApolloSource()
        web_sources.append(apollo)
        champion_source = LinkedInChampionAdapter(apollo)
    else:
        log.info("apollo disabled → using Anthropic company/champion fallbacks")
        web_sources.append(StubAnthropicCompanySource())
        champion_source = StubAnthropicChampionSource()

    if config.crunchbase_enabled:
        web_sources.append(StubCrunchbaseSource())
    else:
        log.info("crunchbase disabled → Anthropic news covers funding signals")

    web_sources.extend([
        StubAnthropicNewsSource(),
        StubJobsSource(),
        StubWebsiteSource(),
    ])

    if config.hubspot_enabled:
        crm = StubHubSpotWriter()
    else:
        log.info("hubspot disabled → CRM write skipped")
        crm = None

    return Pipeline(
        mc=StubMCSource(),
        web_sources=web_sources,
        champion_source=champion_source,
        crm=crm,
    )


def _build_live(config: Config) -> Pipeline:
    if not config.anthropic_api_key:
        raise RuntimeError("live mode requires ANTHROPIC_API_KEY")
    if config.apollo_enabled and not config.apollo_api_key:
        raise RuntimeError("live mode with Apollo enabled requires APOLLO_API_KEY")
    if config.crunchbase_enabled and not config.crunchbase_api_key:
        raise RuntimeError(
            "live mode with Crunchbase enabled requires CRUNCHBASE_API_KEY"
        )
    if config.hubspot_enabled and not config.hubspot_api_token:
        raise RuntimeError(
            "live mode with HubSpot enabled requires HUBSPOT_API_TOKEN"
        )

    captcha = build_captcha_solver(
        provider=config.captcha_provider,
        twocaptcha_api_key=config.twocaptcha_api_key,
        anthropic_api_key=config.anthropic_api_key,
    )

    web_sources = []
    if config.apollo_enabled:
        apollo = ApolloSource(config.apollo_api_key)
        web_sources.append(apollo)
        # Chain Anthropic AFTER Apollo so it backfills any fields Apollo left
        # blank (Apollo often returns nothing for MC-only Saudi entities that
        # aren't in Apollo's B2B graph). AnthropicCompanySource.enrich()
        # merges non-destructively — it won't overwrite populated fields.
        log.info(
            "apollo enabled → also running Anthropic company/champion backfill"
        )
        web_sources.append(AnthropicCompanySource(config.anthropic_api_key))
        # Prefer Apollo champions; fall back to Anthropic when Apollo returns
        # nothing. Wrap both in a chained source below.
        champion_source = _ChainedChampionSource(
            primary=LinkedInChampionAdapter(apollo),
            fallback=AnthropicChampionSource(config.anthropic_api_key),
        )
    else:
        log.info(
            "apollo disabled → Anthropic (Claude + web_search) will cover "
            "company intel and champions"
        )
        web_sources.append(AnthropicCompanySource(config.anthropic_api_key))
        champion_source = AnthropicChampionSource(config.anthropic_api_key)

    if config.crunchbase_enabled:
        web_sources.append(CrunchbaseSource(config.crunchbase_api_key))
    else:
        log.info(
            "crunchbase disabled → Anthropic news source will cover funding signals"
        )

    web_sources.extend([
        AnthropicNewsSource(config.anthropic_api_key),
        StubJobsSource(),  # TODO: implement live Bayt/Indeed adapter
        StubWebsiteSource(),  # TODO: implement live website scraper
    ])

    if config.hubspot_enabled:
        crm = HubSpotWriter(config.hubspot_api_token)
    else:
        log.info("hubspot disabled → CRM write skipped")
        crm = None

    return Pipeline(
        mc=MCSource(captcha, anthropic_api_key=config.anthropic_api_key),
        web_sources=web_sources,
        champion_source=champion_source,
        crm=crm,
    )


class _ChainedChampionSource:
    """Call primary first; if it returns no admin AND no GM, fall back.

    Apollo's people-search is stingy for MC-only Saudi entities. Rather than
    accept empty champions on data point #23/#24, ask Claude via web_search
    to take another pass. The two sources are both ChampionSourceP so no
    adapter needed.
    """

    def __init__(self, primary, fallback):
        self.primary = primary
        self.fallback = fallback

    def find(self, mc):
        result = self.primary.find(mc)
        if result.admin is not None or result.gm is not None:
            return result
        log.info(
            "[champions] primary (%s) returned empty; trying fallback (%s)",
            type(self.primary).__name__, type(self.fallback).__name__,
        )
        return self.fallback.find(mc)
