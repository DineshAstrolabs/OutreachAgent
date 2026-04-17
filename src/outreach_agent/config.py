"""Runtime configuration loaded from environment."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    mode: str  # "stub" | "live"
    log_level: str

    twocaptcha_api_key: str | None
    linkedin_proxy_url: str | None
    linkedin_proxy_user: str | None
    linkedin_proxy_pass: str | None
    crunchbase_api_key: str | None
    serpapi_key: str | None
    hubspot_api_token: str | None
    hubspot_portal_id: str | None

    @classmethod
    def load(cls) -> "Config":
        return cls(
            mode=os.getenv("OUTREACH_AGENT_MODE", "stub").lower(),
            log_level=os.getenv("OUTREACH_AGENT_LOG_LEVEL", "INFO"),
            twocaptcha_api_key=os.getenv("TWOCAPTCHA_API_KEY") or None,
            linkedin_proxy_url=os.getenv("LINKEDIN_PROXY_URL") or None,
            linkedin_proxy_user=os.getenv("LINKEDIN_PROXY_USER") or None,
            linkedin_proxy_pass=os.getenv("LINKEDIN_PROXY_PASS") or None,
            crunchbase_api_key=os.getenv("CRUNCHBASE_API_KEY") or None,
            serpapi_key=os.getenv("SERPAPI_KEY") or None,
            hubspot_api_token=os.getenv("HUBSPOT_API_TOKEN") or None,
            hubspot_portal_id=os.getenv("HUBSPOT_PORTAL_ID") or None,
        )

    @property
    def is_live(self) -> bool:
        return self.mode == "live"
