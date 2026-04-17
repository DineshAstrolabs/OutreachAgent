"""Runtime configuration loaded from environment."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _env_flag(name: str, default: bool = True) -> bool:
    """Parse a boolean env var. Accepts 1/0, true/false, yes/no (case-insensitive).
    Missing or empty → default."""
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Config:
    mode: str  # "stub" | "live"
    log_level: str

    twocaptcha_api_key: str | None
    apollo_api_key: str | None
    crunchbase_api_key: str | None
    anthropic_api_key: str | None
    hubspot_api_token: str | None
    hubspot_portal_id: str | None

    # Integration toggles. When false in live mode, that integration is
    # replaced with a no-op (web source contributes nothing, CRM write skipped).
    apollo_enabled: bool = True
    hubspot_enabled: bool = True

    @classmethod
    def load(cls) -> "Config":
        return cls(
            mode=os.getenv("OUTREACH_AGENT_MODE", "stub").lower(),
            log_level=os.getenv("OUTREACH_AGENT_LOG_LEVEL", "INFO"),
            twocaptcha_api_key=os.getenv("TWOCAPTCHA_API_KEY") or None,
            apollo_api_key=os.getenv("APOLLO_API_KEY") or None,
            crunchbase_api_key=os.getenv("CRUNCHBASE_API_KEY") or None,
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY") or None,
            hubspot_api_token=os.getenv("HUBSPOT_API_TOKEN") or None,
            hubspot_portal_id=os.getenv("HUBSPOT_PORTAL_ID") or None,
            apollo_enabled=_env_flag("APOLLO_ENABLED", default=True),
            hubspot_enabled=_env_flag("HUBSPOT_ENABLED", default=True),
        )

    @property
    def is_live(self) -> bool:
        return self.mode == "live"
