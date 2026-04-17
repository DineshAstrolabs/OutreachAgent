# OutreachAgent

**Post-Setup Company Qualification Agent** for AstroLabs.

Takes a Saudi Unified Number (7xxxxxxx), pulls public MC CR data, scrapes web
intelligence (LinkedIn, Crunchbase, Google News, job boards), identifies
internal champions, scores the company on 17 data points, classifies the lead
(CRITICAL / HOT / WARM / COOL / COLD), and writes the result to HubSpot.

## Quick start

```bash
pip install -e .
cp .env.example .env   # fill in API keys (or leave blank to use stubs)
outreach-agent score 7012345678
outreach-agent batch leads.csv --out results.json
```

Run `outreach-agent score 7012345678 --dry-run` to execute the full pipeline
against fixture data — no external credentials required.

## Architecture

```
Unified Number ──► MC CR scraper ──► Hard gates ──┬──► DISQUALIFIED
                        │                          │
                        ▼                          ▼
                   Company name            Web intel (parallel):
                        │                   • LinkedIn Company/Jobs/People
                        │                   • Crunchbase
                        │                   • Google News / SerpAPI
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
| MC CR Lookup | `sources.mc.StubMCSource` | `sources.mc.MCSource` (needs 2Captcha key) |
| LinkedIn | `sources.linkedin.StubLinkedInSource` | `sources.linkedin.LinkedInSource` (needs scraping proxy) |
| Crunchbase | `sources.crunchbase.StubCrunchbaseSource` | `sources.crunchbase.CrunchbaseSource` (needs API key) |
| Google News | `sources.google_news.StubGoogleNewsSource` | `sources.google_news.SerpAPISource` (needs SerpAPI key) |
| Job boards | `sources.jobs.StubJobsSource` | `sources.jobs.JobsSource` |
| Website | `sources.website.StubWebsiteSource` | `sources.website.WebsiteSource` |
| HubSpot | `crm.hubspot.StubHubSpotWriter` | `crm.hubspot.HubSpotWriter` (needs API key) |

## Tests

```bash
pytest
```

Covers scoring engine (all 17 data points), disqualification gates, and
classification thresholds. External sources are not covered — they require
integration tests with real credentials.
