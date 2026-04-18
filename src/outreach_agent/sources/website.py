"""Company website source — KSA office mention, business model classification.

Covers #10 (signal complement to LinkedIn) and #16 business model.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ..models import BusinessModel, MCData, WebIntelligence

log = logging.getLogger(__name__)


class StubWebsiteSource:
    name = "website"

    def __init__(self, fixtures_dir: Path | None = None):
        self.fixtures_dir = fixtures_dir or Path(__file__).parent.parent.parent.parent / "fixtures" / "website"

    def enrich(self, mc: MCData, web: WebIntelligence) -> None:
        path = self.fixtures_dir / f"{_slug(mc.company_legal_name)}.json"
        if not path.exists():
            return
        data = json.loads(path.read_text())
        if data.get("has_ksa_office_listed"):
            web.has_ksa_office_listed = True
        if "business_model" in data:
            web.business_model = BusinessModel(data["business_model"])
        web.sources_seen.append(self.name)


def _slug(name: str) -> str:
    return "".join(c.lower() if c.isalnum() else "-" for c in name).strip("-")
