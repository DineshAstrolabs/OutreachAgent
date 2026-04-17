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
import time
from contextlib import contextmanager
from dataclasses import dataclass, field

from .classification import classify
from .disqualification import apply_gates
from .models import Champions, Classification, QualificationResult, WebIntelligence
from .scoring import score
from .sources.base import ChampionSourceP, CRMWriterP, MCSourceP, WebIntelligenceSourceP
from .sources.v2030 import classify_isic

log = logging.getLogger(__name__)


@contextmanager
def _step(label: str):
    log.info("▶ %s", label)
    t0 = time.monotonic()
    try:
        yield
    finally:
        log.info("✓ %s (%.2fs)", label, time.monotonic() - t0)


@dataclass
class Pipeline:
    mc: MCSourceP
    web_sources: list[WebIntelligenceSourceP]
    champion_source: ChampionSourceP
    crm: CRMWriterP | None = None

    def run(self, unified_number: str) -> QualificationResult:
        t_start = time.monotonic()
        log.info("=" * 72)
        log.info("QUALIFYING %s", unified_number)
        log.info("=" * 72)

        # 1. MC CR pull + hard gates
        with _step(f"STEP 1/6 — MC CR lookup for {unified_number}"):
            mc_data = self.mc.fetch(unified_number)
        log.info(
            "  MC result: name=%r status=%s entity=%s expiry=%s isic=%s city=%s",
            mc_data.company_legal_name,
            mc_data.cr_status.value if mc_data.cr_status else None,
            mc_data.entity_type.value if mc_data.entity_type else None,
            mc_data.cr_expiry_date,
            mc_data.business_activity_isic,
            mc_data.city,
        )

        with _step("STEP 2/6 — disqualification gates"):
            gate = apply_gates(mc_data)
        if gate.disqualified:
            log.info("  ✗ DISQUALIFIED: %s", gate.disqualification_reason)
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
            log.info("END %s — disqualified in %.2fs", unified_number, time.monotonic() - t_start)
            return result
        log.info(
            "  ✓ gates passed (deprioritized=%s reason=%r)",
            gate.deprioritized,
            gate.deprioritized_reason,
        )

        # 2-3. Web intelligence enrichment (order matters only for #22 V2030,
        # which depends on the ISIC code from MC — set that first).
        web = WebIntelligence()
        web.v2030_tier = classify_isic(mc_data.business_activity_isic)
        log.info("  V2030 tier from ISIC %r: %s", mc_data.business_activity_isic, web.v2030_tier.value)
        with _step(f"STEP 3/6 — web enrichment ({len(self.web_sources)} sources)"):
            for source in self.web_sources:
                with _step(f"  source: {source.name}"):
                    try:
                        source.enrich(mc_data.company_legal_name, web)
                    except Exception as exc:
                        log.warning("  source %s crashed: %s", source.name, exc)
                        web.sources_failed.append(source.name)
        log.info(
            "  web intel: ksa_office=%s ksa_headcount=%s global=%s vacancies=%s "
            "ksa_news=%s funding=%s v2030=%s company_type=%s business_model=%s",
            web.has_ksa_office_listed,
            web.ksa_headcount,
            web.global_headcount,
            web.ksa_open_vacancies,
            web.ksa_news_last_6mo,
            web.funding_last_12mo,
            web.v2030_tier.value if web.v2030_tier else None,
            web.company_type.value if web.company_type else None,
            web.business_model.value if web.business_model else None,
        )
        log.info("  sources seen: %s", web.sources_seen or [])
        if web.sources_failed:
            log.warning("  sources failed: %s", web.sources_failed)

        # 4. Champions
        with _step("STEP 4/6 — champion identification"):
            try:
                champions = self.champion_source.find(mc_data.company_legal_name)
            except Exception as exc:
                log.warning("  champion lookup failed: %s", exc)
                champions = Champions()
        log.info(
            "  admin=%s gm=%s admin_overwhelmed=%s gm_new=%s",
            champions.admin.name if champions.admin else "—",
            champions.gm.name if champions.gm else "—",
            champions.admin_is_overwhelmed,
            champions.gm_is_new,
        )

        # 5. Score + classify
        with _step("STEP 5/6 — scoring (17 data points)"):
            breakdown = score(mc_data, web, champions)
            total = sum(line.points for line in breakdown)
            for line in breakdown:
                log.info(
                    "  #%-2d %-30s %-40s +%d",
                    line.data_point_id, line.name, line.observed_value, line.points,
                )
            log.info("  TOTAL: %d / 225", total)
            routing = classify(total)
            log.info("  → classification: %s", routing.classification.value)

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
        with _step("STEP 6/6 — CRM write"):
            self._write(result)

        log.info(
            "END %s — %s (%d pts) in %.2fs",
            unified_number, routing.classification.value, total, time.monotonic() - t_start,
        )
        return result

    def _write(self, result: QualificationResult) -> None:
        if self.crm is None:
            log.info("  CRM writer is None → skipping write")
            return
        try:
            record_id = self.crm.write(result)
            log.info("  crm record: %s", record_id)
        except Exception as exc:
            log.error("  crm write failed: %s", exc)


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


# ---------------------------------------------------------------------------
# Null sources — used when an integration is disabled via config flags.
# ---------------------------------------------------------------------------


class NullChampionSource:
    """Returns an empty Champions bundle. Used when Apollo is disabled."""

    def find(self, company_name: str) -> Champions:
        return Champions()
