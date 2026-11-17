# FlytBase Inbound BDR Agent

A Python pipeline that takes one raw inbound email as input and automatically produces the work a human inbound BDR would: qualification, account research, an email sequence, a case-study match, a sales-motion call and an AE handoff.

```
python run.py            # CLI, writes everything to output/
streamlit run app.py     # live demo UI, shows each stage as it runs
```

## Architecture

```
 input/inbound_email.txt
          │
          ▼
 ┌────────────────────┐  lead JSON, MEDDPICC knowns/unknowns, scores
 │ S1 Qualification   │──────────────────────────────┐
 └────────────────────┘                              │
          │ unknowns + lead                          │
          ▼                                          │
 ┌────────────────────┐  Google (Apify) / DuckDuckGo ─► SOURCE GATE ─► fetch HTML/PDF
 │ S2 Account research│  ─► IDENTITY GATE ─► LLM extracts facts (long filings read once
 └────────────────────┘  per category) with verbatim evidence ─► CODE checks the quote
                         is really on the page ─► verified facts [F1..Fn]
          │                                          │
          ▼                                          │
 ┌────────────────────┐  flytbase.com sitemap ─► all case studies ─► LLM finds
 │ S4 Case study match│  orgs named in email ─► CODE string-matches them on site
 └────────────────────┘  ─► LLM rubric scores ─► CODE weighted ranking
          │                                          │
          ▼                                          │
 ┌────────────────────┐  sitemap ─► /partner directory ─► LLM parses partners
 │ S5 Sales motion    │  ─► CODE filters LATAM / in-country ─► LLM recommends
 └────────────────────┘  direct AE vs partner-led vs hybrid
          │                                          │
          ▼                                          ▼
 ┌────────────────────┐  uses S1 unknowns, S2 fact ids, S4 case study, S5 motion
 │ S3 Email sequence  │  writing guide ─► CODE checks (length, referral, existing-customer
 │                    │  angle, invented offers, AI phrases, fact use) ─► one auto rewrite
 └────────────────────┘
          │
          ▼
 ┌────────────────────┐
 │ S6 AE handoff      │  built only from S1-S5 JSON
 └────────────────────┘
          │
          ▼
 output/stage*.json, report.md, run_log.txt
```

- `core/pipeline.py`: orchestrator. Shared context dict, one JSON file per stage, events for the UI.
- `core/llm.py`: Gemini wrapper. JSON mode, retries with backoff, automatic fallback to the next model when one is overloaded or rate-limited, key never logged.
- `core/web.py`: search (Google via Apify, DuckDuckGo fallback), fetch (HTML + PDF), disk cache, retries, hosts that time out or block are skipped for the rest of the run, evidence check.
- `prompts/writing_guide.md`: the house style every customer-facing text follows.
- `verify_sources.py`: re-fetches every fact's source live and re-checks its quote.
- `stages/s1..s6`: one module per stage. Each docstring explains its steps.

## Setup

```
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
export GEMINI_API_KEY=...     # or put GEMINI_API_KEY=... in .env
export APIFY_TOKEN=...        # optional: Google search via Apify; without it, free DuckDuckGo is used
.venv/bin/python run.py
```
Optional: `GEMINI_MODEL` (default `gemini-3.5-flash-lite`), `GEMINI_WRITER_MODEL` (default `gemini-3.5-flash`, used for emails and handoff), `GEMINI_FALLBACKS`.

A full run takes about 3 to 5 minutes. `golden/` holds a verified run (outputs, report, run log, live source check).

## Design decisions

1. **The LLM makes judgments, the code holds the line.** Scores (fit, priority, case-study ranking) are computed in code from LLM-graded parts, so the rule is visible and stable.
2. **No unverified facts.** Every research fact must carry a verbatim quote, and code checks that the quote exists on the fetched page. Facts that fail are dropped and logged. Empty categories say "not found".
3. **No hard-coding.** Case studies come from the flytbase.com sitemap and partners from the live partner directory. The referrer (Anglo American) and the lead's company are detected from the email and string-matched against site pages.
4. **Stage 3 runs after 4 and 5**, so the emails can use the matched case study and the partner motion. Output numbering still follows the brief.
5. **Source and identity gates.** Research only uses the company's own domain, filings and regulators (sec.gov, cmfchile.cl) and established, openly readable press. Every page must name the company (legal name, or short name plus country). This stopped look-alike companies (e.g. "SQM Group") from leaking in. Paywalled sites are excluded, so any reader can open every source.
6. **Deep reading of long filings.** A 300-page annual report or 20-F is read once per research category (leadership, capex, news, executive statements), not once overall.
7. **Writing checks in code.** Emails follow `prompts/writing_guide.md`; code rejects drafts that are too long or short, forget the referral, miss the existing-customer angle, offer things FlytBase did not provide (guides, pilots, pricing), use AI-sounding phrases, or use no research fact. A failing draft gets one automatic rewrite.
8. **Parallel and resilient.** Searches, fetches and fact extraction run in parallel; overloaded models rotate; dead hosts are skipped.
9. **Cache + backoff.** Pages, searches and sitemaps are cached in `.cache/`, so reruns are fast and rate limits don't kill a run.

## Why MEDDPICC

This is an enterprise, multi-site mining deal. A Head of Operations is rarely the economic buyer, procurement and safety approval at a listed miner is long, and the email already hints at a champion (referral) and a budget event (Q3). MEDDPICC keeps those threads apart. BANT would collapse them into "budget yes/no".

## Limitations

- Some of the best sources block automated reading (the company's own investor site, Seeking Alpha, investing.com) or are paywalled. The system skips them and relies on SEC filings, earnings calls and open press. A licensed financial data API would fix this.
- Research depth varies run to run with search results and which sites respond; the run log and source list show exactly what was used.
- Animated stat counters on flytbase.com are filled by JavaScript, so their numbers are not in the HTML. They are dropped rather than read as "0"; the case study text still has the numbers.
- Writing quality depends on the model that answers; under load the system falls back to a smaller model.
- The evidence check proves a quote exists on a page, not that the model's paraphrase of it is perfect. A human should skim the fact table.
- The emails are drafts for a human to send. Nothing is sent automatically.
