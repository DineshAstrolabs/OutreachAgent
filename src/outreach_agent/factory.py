"""Wire the pipeline up from Config. Single place where stub/live is decided."""

from __future__ import annotations

import logging

from .captcha import TwoCaptchaClient
from .config import Config
from .crm.hubspot import HubSpotWriter, StubHubSpotWriter
from .pipeline import LinkedInChampionAdapter, NullChampionSource, Pipeline
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
    apollo = StubApolloSource()
    web_sources = []
    if config.apollo_enabled:
        web_sources.append(apollo)
    else:
        log.info("apollo disabled via APOLLO_ENABLED=false")
    web_sources.extend([
        StubCrunchbaseSource(),
        StubAnthropicNewsSource(),
        StubJobsSource(),
        StubWebsiteSource(),
    ])

    champion_source = (
        LinkedInChampionAdapter(apollo) if config.apollo_enabled else NullChampionSource()
    )

    if config.hubspot_enabled:
        crm = StubHubSpotWriter()
    else:
        log.info("hubspot disabled via HUBSPOT_ENABLED=false")
        crm = None

    return Pipeline(
        mc=StubMCSource(),
        web_sources=web_sources,
        champion_source=champion_source,
        crm=crm,
    )


def _build_live(config: Config) -> Pipeline:
    if not config.twocaptcha_api_key:
        raise RuntimeError("live mode requires TWOCAPTCHA_API_KEY")
    if not config.crunchbase_api_key:
        raise RuntimeError("live mode requires CRUNCHBASE_API_KEY")
    if not config.anthropic_api_key:
        raise RuntimeError("live mode requires ANTHROPIC_API_KEY")
    if config.apollo_enabled and not config.apollo_api_key:
        raise RuntimeError("live mode with Apollo enabled requires APOLLO_API_KEY")
    if config.hubspot_enabled and not config.hubspot_api_token:
        raise RuntimeError("live mode with HubSpot enabled requires HUBSPOT_API_TOKEN")

    captcha = TwoCaptchaClient(config.twocaptcha_api_key)

    web_sources = []
    if config.apollo_enabled:
        apollo = ApolloSource(config.apollo_api_key)
        web_sources.append(apollo)
        champion_source = LinkedInChampionAdapter(apollo)
    else:
        log.info("apollo disabled via APOLLO_ENABLED=false — no KSA presence / champions data")
        champion_source = NullChampionSource()

    web_sources.extend([
        CrunchbaseSource(config.crunchbase_api_key),
        AnthropicNewsSource(config.anthropic_api_key),
        StubJobsSource(),  # TODO: implement live Bayt/Indeed adapter
        StubWebsiteSource(),  # TODO: implement live website scraper
    ])

    if config.hubspot_enabled:
        crm = HubSpotWriter(config.hubspot_api_token)
    else:
        log.info("hubspot disabled via HUBSPOT_ENABLED=false — result will not be written to CRM")
        crm = None

    return Pipeline(
        mc=MCSource(captcha),
        web_sources=web_sources,
        champion_source=champion_source,
        crm=crm,
    )
