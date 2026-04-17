"""Pipeline orchestrator — the 6-cluster flow from the Miro diagram.

  1. MC CR data pull       (disqualification gates applied here)
  2. KSA Presence + Fit    (LinkedIn + website)
  3. Growth + Market       (LinkedIn Jobs + Crunchbase + Google News + jobs)
  4. Champion ID           (LinkedIn People)
  5. Scoring + Routing
  6. CRM write
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .classification import classify
from .disqualification import apply_gates
from .models import Champions, Classification, QualificationResult, WebIntelligence
from .scoring import score
from .sources.base import ChampionSourceP, CRMWriterP, MCSourceP, WebIntelligenceSourceP
from .sources.v2030 import classify_isic

log = logging.getLogger(__name__)


@dataclass
class Pipeline:
    mc: MCSourceP
    web_sources: list[WebIntelligenceSourceP]
    champion_source: ChampionSourceP
    crm: CRMWriterP | None = None

    def run(self, unified_number: str) -> QualificationResult:
        log.info("qualifying %s", unified_number)

        # 1. MC CR pull + hard gates
        mc_data = self.mc.fetch(unified_number)
        gate = apply_gates(mc_data)
        if gate.disqualified:
            log.info("disqualified: %s", gate.disqualification_reason)
            result = QualificationResult(
                unified_number=unified_number,
                mc_data=mc_data,
                web=None,
                champions=None,
                disqualified=True,
                disqualification_reason=gate.disqualification_reason,
                classification=Classification.DISQUALIFIED,
                recommended_action="Do not pursue Post-Setup; see disqualification reason.",
            )
            self._write(result)
            return result

        # 2-3. Web intelligence enrichment (order matters only for #22 V2030,
        # which depends on the ISIC code from MC — set that first).
        web = WebIntelligence()
        web.v2030_tier = classify_isic(mc_data.business_activity_isic)
        for source in self.web_sources:
            try:
                source.enrich(mc_data.company_legal_name, web)
            except Exception as exc:
                log.warning("source %s crashed: %s", source.name, exc)
                web.sources_failed.append(source.name)

        # 4. Champions
        try:
            champions = self.champion_source.find(mc_data.company_legal_name)
        except Exception as exc:
            log.warning("champion lookup failed: %s", exc)
            champions = Champions()

        # 5. Score + classify
        breakdown = score(mc_data, web, champions)
        total = sum(line.points for line in breakdown)
        routing = classify(total)

        result = QualificationResult(
            unified_number=unified_number,
            mc_data=mc_data,
            web=web,
            champions=champions,
            disqualified=False,
            deprioritized=gate.deprioritized,
            deprioritized_reason=gate.deprioritized_reason,
            breakdown=breakdown,
            total_score=total,
            classification=routing.classification,
            recommended_action=routing.recommended_action,
            outreach_approach=routing.outreach_approach,
            timeline=routing.timeline,
            expected_conversion=routing.expected_conversion,
        )

        # 6. CRM write
        self._write(result)
        return result

    def _write(self, result: QualificationResult) -> None:
        if self.crm is None:
            return
        try:
            record_id = self.crm.write(result)
            log.info("crm record: %s", record_id)
        except Exception as exc:
            log.error("crm write failed: %s", exc)


# ---------------------------------------------------------------------------
# Champion adapter — bridges LinkedIn source to ChampionSourceP.
# ---------------------------------------------------------------------------

@dataclass
class LinkedInChampionAdapter:
    """Delegate to a LinkedIn source's find_champions. Kept as an adapter so
    the pipeline's champion slot doesn't hard-depend on LinkedIn — a future
    source (Apollo, ZoomInfo) could plug in here instead."""

    inner: object  # something with find_champions(company_name) -> Champions

    def find(self, company_name: str) -> Champions:
        return self.inner.find_champions(company_name)
