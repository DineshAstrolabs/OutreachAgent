# OutreachAgent

**Post-Setup Company Qualification Agent** for AstroLabs.

Takes a Saudi Unified Number (7xxxxxxx), pulls public MC CR data, enriches via
Apollo.io + Crunchbase + Claude-powered news research, identifies internal
champions, scores the company on 17 data points, classifies the lead
(CRITICAL / HOT / WARM / COOL / COLD), and writes the result to HubSpot.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install --upgrade pip setuptools
pip install -e .
cp .env.example .env   # fill in API keys (or leave blank to use stubs)
outreach-agent score 7012345678
outreach-agent batch leads.csv --out results.json
```

Run `outreach-agent score 7012345678 --dry-run` to execute the full pipeline
against fixture data — no external credentials required.

### Troubleshooting: `ModuleNotFoundError: No module named 'outreach_agent'`

This means the console-script shim was installed but the package body isn't
on `sys.path` — usually a stale editable install from before the src-layout
config was finalized. Two fixes, in order:

```bash
# 1. Force-reinstall and rebuild the .pth file.
pip install -e . --force-reinstall --no-deps

# 2. Or bypass the shim entirely — `__main__.py` lets you run the module
#    directly, which works as long as the package imports at all.
python -m outreach_agent score 7012345678
```

Recreating the venv (`rm -rf .venv && python -m venv .venv`) is the
nuclear option and always works.

## Architecture

```
Unified Number ──► MC CR scraper ──► Hard gates ──┬──► DISQUALIFIED
                        │                          │
                        ▼                          ▼
                   Company name            Web intel (parallel):
                        │                   • Apollo.io (org + champions)
                        │                   • Crunchbase (funding)
                        │                   • Claude API + web_search (news)
                        │                   • Bayt / Indeed
                        │                   • Company website
                        │                          │
                        └──────────────┬───────────┘
                                       ▼
                              Champion identification
                                       ▼
                              Scoring engine (17 pts)
                                       ▼
                              Classification + routing
                                       ▼
                              HubSpot CRM write
```

See `src/outreach_agent/` for the implementation.

## Scoring

17 data points, 225 max points:

| Cluster | Data points | Max |
|---|---|---|
| Eligibility (MC) | CR Expiry | 20 |
| KSA Presence & Fit | Office, Roles, Headcount, Growth, Size, Type, Model | 65 |
| Growth & Market | Vacancies, New GM, Funding, News, Partnerships, V2030 | 105 |
| Champions | Admin, GM | 25 |

Thresholds (tunable in `src/outreach_agent/classification.py`):

| Score | Classification | SLA |
|---|---|---|
| 80+ | CRITICAL | Same day — Senior AE |
| 60–79 | HOT | 24 hours |
| 35–59 | WARM | 1 week |
| 15–34 | COOL | Monthly nurture |
| <15 | COLD | Quarterly review |

## Data sources

All sources implement `DataSource` in `sources/base.py`. External integrations
are stubbed with fixture-backed implementations so the pipeline runs end-to-end
without paid API keys. Swap in real clients by setting the corresponding env
vars in `.env`.

| Source | Stub | Real |
|---|---|---|
| MC CR Lookup | `sources.mc.StubMCSource` | `sources.mc.MCSource` (needs `TWOCAPTCHA_API_KEY`) |
| Apollo.io | `sources.apollo.StubApolloSource` | `sources.apollo.ApolloSource` (needs `APOLLO_API_KEY`) |
| Crunchbase | `sources.crunchbase.StubCrunchbaseSource` | `sources.crunchbase.CrunchbaseSource` (needs `CRUNCHBASE_API_KEY`) |
| News / signals | `sources.anthropic_news.StubAnthropicNewsSource` | `sources.anthropic_news.AnthropicNewsSource` (needs `ANTHROPIC_API_KEY`) |
| Job boards | `sources.jobs.StubJobsSource` | `sources.jobs.JobsSource` |
| Website | `sources.website.StubWebsiteSource` | `sources.website.WebsiteSource` |
| HubSpot | `crm.hubspot.StubHubSpotWriter` | `crm.hubspot.HubSpotWriter` (needs `HUBSPOT_API_TOKEN`) |

The news source uses Claude (`claude-opus-4-7`) with the server-side
`web_search` tool and prompt caching on the research rubric — one structured
call per lead replaces the three SerpAPI searches + client-side parsing we
had before. Apollo covers Apollo's native org/people endpoints; growth %
(#13) is left unscored in live mode because Apollo's free tier doesn't expose
historical headcount snapshots.

### Disabling integrations

Two env flags bypass specific integrations without touching code:

| Flag | Default | Effect when `false` |
|---|---|---|
| `APOLLO_ENABLED` | `true` | No Apollo web source, champions bundle is empty, no KSA presence / headcount / vacancies scored |
| `HUBSPOT_ENABLED` | `true` | Qualification still runs and returns a result; CRM write is skipped |

Both flags work in stub and live mode. In live mode, disabling Apollo also
skips the `APOLLO_API_KEY` check; disabling HubSpot skips the
`HUBSPOT_API_TOKEN` check.

## Tests

```bash
pytest
```

Covers scoring engine (all 17 data points), disqualification gates, and
classification thresholds. External sources are not covered — they require
integration tests with real credentials.
