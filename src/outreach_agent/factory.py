"""Wire the pipeline up from Config. Single place where stub/live is decided."""

from __future__ import annotations

import logging

from .captcha import TwoCaptchaClient
from .config import Config
from .crm.hubspot import HubSpotWriter, StubHubSpotWriter
from .pipeline import LinkedInChampionAdapter, Pipeline
from .sources.crunchbase import CrunchbaseSource, StubCrunchbaseSource
from .sources.google_news import SerpAPISource, StubGoogleNewsSource
from .sources.jobs import StubJobsSource
from .sources.linkedin import LinkedInSource, StubLinkedInSource
from .sources.mc import MCSource, StubMCSource
from .sources.website import StubWebsiteSource

log = logging.getLogger(__name__)


def build_pipeline(config: Config) -> Pipeline:
    if config.is_live:
        return _build_live(config)
    return _build_stub()


def _build_stub() -> Pipeline:
    linkedin = StubLinkedInSource()
    return Pipeline(
        mc=StubMCSource(),
        web_sources=[
            linkedin,
            StubCrunchbaseSource(),
            StubGoogleNewsSource(),
            StubJobsSource(),
            StubWebsiteSource(),
        ],
        champion_source=LinkedInChampionAdapter(linkedin),
        crm=StubHubSpotWriter(),
    )


def _build_live(config: Config) -> Pipeline:
    if not config.twocaptcha_api_key:
        raise RuntimeError("live mode requires TWOCAPTCHA_API_KEY")
    if not config.linkedin_proxy_url:
        raise RuntimeError("live mode requires LINKEDIN_PROXY_URL")
    if not config.crunchbase_api_key:
        raise RuntimeError("live mode requires CRUNCHBASE_API_KEY")
    if not config.serpapi_key:
        raise RuntimeError("live mode requires SERPAPI_KEY")
    if not config.hubspot_api_token:
        raise RuntimeError("live mode requires HUBSPOT_API_TOKEN")

    captcha = TwoCaptchaClient(config.twocaptcha_api_key)
    linkedin = LinkedInSource(
        config.linkedin_proxy_url,
        config.linkedin_proxy_user,
        config.linkedin_proxy_pass,
    )
    return Pipeline(
        mc=MCSource(captcha),
        web_sources=[
            linkedin,
            CrunchbaseSource(config.crunchbase_api_key),
            SerpAPISource(config.serpapi_key),
            StubJobsSource(),  # TODO: implement live Bayt/Indeed adapter
            StubWebsiteSource(),  # TODO: implement live website scraper
        ],
        champion_source=LinkedInChampionAdapter(linkedin),
        crm=HubSpotWriter(config.hubspot_api_token),
    )
