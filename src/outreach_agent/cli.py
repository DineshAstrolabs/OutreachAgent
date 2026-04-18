"""Command-line entrypoint."""

from __future__ import annotations

import csv
import json
import logging
import sys
from dataclasses import asdict
from pathlib import Path

import click

from .config import Config
from .csv_hint import row_to_mc_hint
from .factory import build_pipeline
from .models import QualificationResult


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # Keep noisy httpx request logs at DEBUG unless user asked for DEBUG.
    if level.upper() != "DEBUG":
        logging.getLogger("httpx").setLevel(logging.WARNING)


def _log_config_summary(config) -> None:
    log = logging.getLogger("outreach_agent.cli")
    log.info(
        "config: mode=%s captcha_provider=%s apollo=%s crunchbase=%s hubspot=%s "
        "anthropic_key=%s apollo_key=%s crunchbase_key=%s hubspot_token=%s twocaptcha_key=%s",
        config.mode,
        config.captcha_provider,
        config.apollo_enabled,
        config.crunchbase_enabled,
        config.hubspot_enabled,
        _mask(config.anthropic_api_key),
        _mask(config.apollo_api_key),
        _mask(config.crunchbase_api_key),
        _mask(config.hubspot_api_token),
        _mask(config.twocaptcha_api_key),
    )


def _mask(value: str | None) -> str:
    if not value:
        return "—"
    if len(value) <= 8:
        return "set"
    return f"{value[:4]}…{value[-2:]}"


def _validate_unified_number(unified_number: str) -> str:
    un = unified_number.strip()
    if not (un.isdigit() and len(un) == 10 and un.startswith("7")):
        raise click.BadParameter(
            "Unified Number must be 10 digits starting with 7 (e.g., 7012345678)"
        )
    return un


@click.group()
@click.option("--log-level", default=None, help="DEBUG/INFO/WARNING/ERROR")
@click.pass_context
def main(ctx: click.Context, log_level: str | None) -> None:
    """AstroLabs Post-Setup Company Qualification Agent."""
    config = Config.load()
    _setup_logging(log_level or config.log_level)
    _log_config_summary(config)
    ctx.ensure_object(dict)
    ctx.obj["config"] = config


@main.command()
@click.argument("unified_number")
@click.option("--dry-run", is_flag=True, help="Force stub mode regardless of env.")
@click.option("--json-out", "json_out", type=click.Path(), help="Write result JSON to path.")
@click.pass_context
def score(ctx: click.Context, unified_number: str, dry_run: bool, json_out: str | None) -> None:
    """Score a single Unified Number."""
    un = _validate_unified_number(unified_number)
    config = ctx.obj["config"]
    if dry_run:
        logging.getLogger("outreach_agent.cli").info("--dry-run flag set; forcing stub mode")
        config = Config(**{**asdict(config), "mode": "stub"})

    pipeline = build_pipeline(config)
    result = pipeline.run(un)

    click.echo(_format_result(result))
    if json_out:
        logging.getLogger("outreach_agent.cli").info("writing result JSON to %s", json_out)
        Path(json_out).write_text(json.dumps(_result_to_dict(result), indent=2, default=str))


@main.command()
@click.argument("csv_path", type=click.Path(exists=True))
@click.option("--out", "out_path", type=click.Path(), default="results/batch.json",
              help="Where to write the batch output JSON.")
@click.option("--dry-run", is_flag=True)
@click.pass_context
def batch(ctx: click.Context, csv_path: str, out_path: str, dry_run: bool) -> None:
    """Score a CSV of Unified Numbers.

    CSV must have a Unified Number column (accepted: `unified_number`,
    `Unified Number`). Any of these additional columns, if present, are
    used as a hint — we still fetch from MC but fall back to your row's
    values for any field MC omits: Company Name (EN), Legal Entity,
    Capital, Registration Status, Registration Number, Registration Type,
    Registration Date, Expiry Date, Location, Phone, Activities.

    Max 50 per spec.
    """
    config = ctx.obj["config"]
    if dry_run:
        config = Config(**{**asdict(config), "mode": "stub"})
    pipeline = build_pipeline(config)

    results: list[QualificationResult] = []
    with open(csv_path) as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            if i >= 50:
                click.echo("stopping at 50 leads (batch cap)", err=True)
                break
            hint = row_to_mc_hint(row)
            raw_un = (
                hint.unified_number if hint is not None
                else row.get("unified_number", "")
            )
            try:
                un = _validate_unified_number(raw_un)
            except click.BadParameter as exc:
                click.echo(f"skipping row {i+1}: {exc}", err=True)
                continue
            results.append(pipeline.run(un, mc_hint=hint))

    # Sort by score desc so the CRITICAL/HOT leads float to the top.
    results.sort(key=lambda r: r.total_score, reverse=True)

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(
        [_result_to_dict(r) for r in results], indent=2, default=str,
    ))
    click.echo(f"wrote {len(results)} results to {out}")
    _print_distribution(results)


def _result_to_dict(r: QualificationResult) -> dict:
    return {
        "unified_number": r.unified_number,
        "company_name": r.company_name,
        "disqualified": r.disqualified,
        "disqualification_reason": r.disqualification_reason,
        "deprioritized": r.deprioritized,
        "total_score": r.total_score,
        "classification": r.classification.value,
        "recommended_action": r.recommended_action,
        "outreach_approach": r.outreach_approach,
        "timeline": r.timeline,
        "expected_conversion": r.expected_conversion,
        "breakdown": [
            {"id": line.data_point_id, "name": line.name,
             "value": line.observed_value, "points": line.points}
            for line in r.breakdown
        ],
        "sources_seen": r.web.sources_seen if r.web else [],
        "sources_failed": r.web.sources_failed if r.web else [],
        "scored_at": r.scored_at.isoformat(),
    }


def _format_result(r: QualificationResult) -> str:
    lines = [
        f"\n=== {r.company_name} ({r.unified_number}) ===",
        f"Classification: {r.classification.value}  |  Score: {r.total_score}",
    ]
    if r.disqualified:
        lines.append(f"Disqualified: {r.disqualification_reason}")
        return "\n".join(lines)
    if r.deprioritized:
        lines.append(f"Deprioritized: {r.deprioritized_reason}")
    lines.append(f"Action: {r.recommended_action}")
    lines.append(f"Approach: {r.outreach_approach}")
    lines.append(f"Timeline: {r.timeline}  |  Expected conversion: {r.expected_conversion}")
    lines.append("\nScore breakdown:")
    for line in r.breakdown:
        lines.append(f"  #{line.data_point_id:>2}  {line.name:<30}  {line.observed_value:<40}  +{line.points}")
    if r.web and r.web.sources_failed:
        lines.append(f"\nSources failed: {', '.join(r.web.sources_failed)}")
    return "\n".join(lines)


def _print_distribution(results: list[QualificationResult]) -> None:
    from collections import Counter
    counts = Counter(r.classification.value for r in results)
    click.echo("\nDistribution:")
    for cls in ("CRITICAL", "HOT", "WARM", "COOL", "COLD", "DISQUALIFIED"):
        click.echo(f"  {cls:<14} {counts.get(cls, 0)}")


if __name__ == "__main__":
    main(obj={})
    sys.exit(0)
