"""Job board source — Bayt and Indeed KSA.

Covers #17 vacancy counts (backup to LinkedIn Jobs). Thin stub for now;
production can use Bayt's search endpoint or scrape Indeed KSA listings.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ..models import WebIntelligence

log = logging.getLogger(__name__)


class StubJobsSource:
    name = "jobs"

    def __init__(self, fixtures_dir: Path | None = None):
        self.fixtures_dir = fixtures_dir or Path(__file__).parent.parent.parent.parent / "fixtures" / "jobs"

    def enrich(self, company_name: str, web: WebIntelligence) -> None:
        path = self.fixtures_dir / f"{_slug(company_name)}.json"
        if not path.exists():
            return
        data = json.loads(path.read_text())
        # Prefer the larger count between LinkedIn and job boards — spec
        # treats this as a single data point.
        web.ksa_open_vacancies = max(web.ksa_open_vacancies, data.get("ksa_open_vacancies", 0))
        web.sources_seen.append(self.name)


def _slug(name: str) -> str:
    return "".join(c.lower() if c.isalnum() else "-" for c in name).strip("-")
