"""Wire the pipeline up from Config. Single place where stub/live is decided."""

from __future__ import annotations

import logging

from .captcha import TwoCaptchaClient
from .config import Config
from .crm.hubspot import HubSpotWriter, StubHubSpotWriter
from .pipeline import LinkedInChampionAdapter, Pipeline
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
    return _build_stub()


def _build_stub() -> Pipeline:
    apollo = StubApolloSource()
    return Pipeline(
        mc=StubMCSource(),
        web_sources=[
            apollo,
            StubCrunchbaseSource(),
            StubAnthropicNewsSource(),
            StubJobsSource(),
            StubWebsiteSource(),
        ],
        champion_source=LinkedInChampionAdapter(apollo),
        crm=StubHubSpotWriter(),
    )


def _build_live(config: Config) -> Pipeline:
    if not config.twocaptcha_api_key:
        raise RuntimeError("live mode requires TWOCAPTCHA_API_KEY")
    if not config.apollo_api_key:
        raise RuntimeError("live mode requires APOLLO_API_KEY")
    if not config.crunchbase_api_key:
        raise RuntimeError("live mode requires CRUNCHBASE_API_KEY")
    if not config.anthropic_api_key:
        raise RuntimeError("live mode requires ANTHROPIC_API_KEY")
    if not config.hubspot_api_token:
        raise RuntimeError("live mode requires HUBSPOT_API_TOKEN")

    captcha = TwoCaptchaClient(config.twocaptcha_api_key)
    apollo = ApolloSource(config.apollo_api_key)
    return Pipeline(
        mc=MCSource(captcha),
        web_sources=[
            apollo,
            CrunchbaseSource(config.crunchbase_api_key),
            AnthropicNewsSource(config.anthropic_api_key),
            StubJobsSource(),  # TODO: implement live Bayt/Indeed adapter
            StubWebsiteSource(),  # TODO: implement live website scraper
        ],
        champion_source=LinkedInChampionAdapter(apollo),
        crm=HubSpotWriter(config.hubspot_api_token),
    )
