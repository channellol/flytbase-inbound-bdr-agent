"""Stage 2: Deep account research.

1. Search plan = LLM-planned queries (from the lead + Stage 1 unknowns) + fixed query templates
   per category (annual report letter, earnings call, leadership, 20-F capex), filled with the
   company name from Stage 1. Nothing about SQM is hard-coded.
2. DuckDuckGo searches run in parallel; results are ranked (primary sources first).
3. Pages and PDFs are fetched in parallel; blocked pages are skipped and the next result fills the slot.
4. LLM extracts facts from each page with a verbatim evidence quote (parallel).
5. Code verifies every quote is really on the fetched page; unverified facts are dropped.
6. LLM synthesises the brief and positioning, citing only verified fact ids.
"""
from __future__ import annotations

import datetime as dt
from concurrent.futures import ThreadPoolExecutor

from core.llm import ask_json
from core.log import log
from core.web import domain, fetch, quote_in_text, relevant_excerpt, search

STAGE = "2-research"
CATEGORIES = {
    "org_structure": "Organizational structure, named executives and reporting lines relevant to the lead's division and operations",
    "budget_signals": "Budget signals from public filings: capex, opex, technology or automation investment, expansion spend (annual report, 20-F / 10-K)",
    "news": "Recent news, press releases and strategic announcements from the last 18 months (deals, joint ventures, expansions, incidents)",
    "investor_letters": "Chairman / CEO letters, earnings calls, investor presentations or sustainability reports revealing operational priorities (safety, productivity, automation, cost)",
}
PRIMARY = ("sec.gov", "codelco.com", "cmfchile.cl")
TRUSTED = ("reuters.com", "mining.com", "spglobal.com",
           "mining-technology.com", "fastmarkets.com", "businesswire.com", "prnewswire.com",
           "seekingalpha.com", "fool.com", "investing.com", "minedocs.com", "finance.yahoo.com",
           "marketscreener.com", "nasdaq.com", "df.cl", "latercera.com", "emol.com", "earningscall.biz", "marketbeat.com")
SKIP = ("facebook.com", "instagram.com", "tiktok.com", "youtube.com", "x.com", "twitter.com",
        "pinterest.com", "linkedin.com", "zoominfo.com", "rocketreach.co", "glassdoor.com")
KEYWORDS = ["capex", "capital expenditure", "investment", "safety", "automation", "digital", "technology",
            "operations", "vice president", "president", "chief", "general manager", "organization",
            "productivity", "drone", "inspection", "cost", "us$", "million", "expansion", "letter",
            "shareholders", "priorities", "strategy", "joint venture", "board"]
# per-category keywords: long documents (annual reports, 20-F) are read once per category,
# so a 300-page filing yields its management letter, leadership, capex and news sections separately
CAT_KEYWORDS = {
    "org_structure": ["chief executive", "ceo", "cfo", "vice president", "general manager", "board of directors",
                      "chairman", "chairwoman", "executive committee", "appointed", "division", "organizational"],
    "budget_signals": ["capex", "capital expenditure", "investment plan", "us$", "million", "billion", "budget",
                       "expansion", "maintenance", "guidance", "opex", "cost"],
    "news": ["announced", "agreement", "joint venture", "partnership", "acquisition", "record", "results",
             "quarter", "2026", "approved", "contract"],
    "investor_letters": ["dear shareholders", "letter", "message from", "our priorities", "we will continue",
                         "strategy", "safety", "productivity", "automation", "digital", "innovation", "efficiency",
                         "earnings call", "remarks", "we are focused", "outlook"],
}
LONG_DOC = 30000
MAX_PAGES = 20
WORKERS = 4


def _is_company_domain(d: str, company_domain: str) -> bool:
    """Own domains = same root as the lead's email domain on a common TLD (sqm.cl -> sqm.com, sqm.cl).
    Look-alikes such as sqm-xyz.com or sqmgroup.com do NOT qualify."""
    if not company_domain:
        return False
    root = company_domain.split(".")[0]
    return any(d == f"{root}.{tld}" or d.endswith(f".{root}.{tld}") for tld in ("com", "cl", "net", "org"))


def _rank(url: str, company_domain: str) -> int:
    d = domain(url)
    if _is_company_domain(d, company_domain) or any(d.endswith(p) for p in PRIMARY):
        return 0
    if any(d.endswith(t) for t in TRUSTED):
        return 1
    return 2  # unknown source: never used (see SOURCE GATE below)


def _strip_accents(s: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)).lower()


def _about_company(text: str, legal: str, short: str, country: str) -> bool:
    """IDENTITY GATE: the page must name the legal entity, or the short name together with its country."""
    t = _strip_accents(text)
    legal_core = _strip_accents(legal.split("(")[0]).strip()
    if legal_core and legal_core in t:
        return True
    return bool(short and country and _strip_accents(short) in t and _strip_accents(country) in t)


def _templates(short: str, year: int) -> dict[str, list[str]]:
    """Generic query templates filled with the company name (no account-specific facts)."""
    return {
        "org_structure": [f"{short} executive team vice president operations", f"{short} board of directors CEO management"],
        "budget_signals": [f"{short} 20-F {year - 1} capital expenditures", f"{short} capex guidance {year}"],
        "news": [f"{short} news {year}", f"{short} press release {year}", f"{short} 6-K earnings release {year}"],
        "investor_letters": [f"{short} annual report {year - 1} letter from the chairman",
                             f"{short} earnings call transcript {year}", f"{short} investor presentation {year}",
                             f"{short} annual report {year - 1} pdf", f"{short} CEO earnings call remarks {year}"],
    }


def run(ctx: dict) -> dict:
    s1 = ctx["s1"]
    lead = s1["lead"]
    company = lead.get("company") or lead.get("company_short")
    short = lead.get("company_short") or company
    company_domain = (lead.get("email_domain") or "").lower().removeprefix("www.")
    year = dt.date.fromisoformat(ctx["today"]).year

    log(f"Planning research queries for {company}", stage=STAGE)
    plan = ask_json(f"""Plan web searches to research this account for an enterprise sales team.
Today is {ctx['today']}. Company: {company} ({short}), country {lead.get('country','')}.
Lead: {lead.get('title')} - {lead.get('use_case')}.
Unknowns from qualification: {s1['unknown']}
For each category give 2 specific search queries (English or the local language), favouring primary
sources (investor relations, annual report / Form 20-F on sec.gov, press releases) and top business press.
Categories: {CATEGORIES}
Return JSON: {{"queries":{{"org_structure":[""],"budget_signals":[""],"news":[""],"investor_letters":[""]}}}}""",
                    STAGE, "plan search queries")

    queries = []
    tmpl = _templates(short, year)
    for cat in CATEGORIES:
        for q in tmpl.get(cat, []) + plan.get("queries", {}).get(cat, [])[:2]:
            queries.append((cat, q))

    log(f"Running {len(queries)} searches in parallel", stage=STAGE)
    with ThreadPoolExecutor(WORKERS) as ex:
        results = list(ex.map(lambda cq: (cq[0], cq[1], search(cq[1], max_results=8)), queries))

    candidates: dict[str, dict] = {}
    for cat, q, res in results:
        log(f"search [{cat}] '{q}' -> {len(res)} results", stage=STAGE)
        for r in res:
            u = r["url"]
            if any(domain(u).endswith(s) for s in SKIP):
                continue
            rank = _rank(u, company_domain)
            if rank > 1:  # SOURCE GATE: only the company's own domain, filings, regulators, established press
                continue
            c = candidates.setdefault(u, {**r, "categories": set(), "rank": rank})
            c["categories"].add(cat)

    # order: guarantee each category its best results, then fill by rank
    ordered = sorted(candidates.values(), key=lambda c: (c["rank"], -len(c["categories"])))
    pool, seen = [], set()
    for cat in CATEGORIES:
        for c in [c for c in ordered if cat in c["categories"]][:4]:
            if c["url"] not in seen:
                pool.append(c); seen.add(c["url"])
    pool += [c for c in ordered if c["url"] not in seen]
    # at most 4 pages per website, so one slow or blocking host cannot take every slot
    per_host, capped = {}, []
    for c in pool:
        h = domain(c["url"])
        if per_host.get(h, 0) < 4:
            per_host[h] = per_host.get(h, 0) + 1
            capped.append(c)
    pool = capped[:40]
    log(f"{len(candidates)} unique results, fetching top {len(pool)} in parallel", stage=STAGE)

    with ThreadPoolExecutor(WORKERS * 2) as ex:
        pages = list(ex.map(lambda c: fetch(c["url"]), pool))

    sources, chosen = [], []
    for c, page in zip(pool, pages):
        if len(chosen) >= MAX_PAGES:
            break
        sources.append({"url": c["url"], "title": page.get("title") or c["title"], "ok": page["ok"],
                        "kind": page.get("kind"), "chars": len(page.get("text", "")),
                        "error": page.get("error", ""), "categories": sorted(c["categories"])})
        if page["ok"] and not _about_company(page["text"], company, short, lead.get("country", "")):
            sources[-1]["ok"] = False
            sources[-1]["error"] = "identity gate: page does not name the company"
            log(f"skip {c['url']} (does not name {short} / {lead.get('country')})", level="warn", stage=STAGE)
            continue
        if page["ok"]:
            chosen.append((c, page, sources[-1]))
        else:
            log(f"skip {c['url']} ({page.get('error')})", level="warn", stage=STAGE)

    def extract_one(c, text, focus):
        cats = [focus] if focus else list(CATEGORIES)
        kws = (CAT_KEYWORDS[focus] if focus else KEYWORDS) + [short.lower()]
        excerpt = relevant_excerpt(text, kws, max_chars=14000)
        got = ask_json(f"""Extract facts about {company} from this page that a FlytBase sales team would use
(drone autonomy for 24/7 industrial site inspection).
Focus on: {"; ".join(CATEGORIES[k] for k in cats)}.
Category guide: org_structure = named people and roles; budget_signals = money figures with years;
news = dated events; investor_letters = what the chairman, CEO or CFO SAY about priorities, strategy,
safety, productivity, automation or outlook (letters, earnings call remarks, presentations). A statement
by an executive about priorities is investor_letters, even inside an annual report or earnings article.
Rules: only facts stated on the page; each fact needs "evidence", an EXACT verbatim sentence
or phrase copied from the page text (15-60 words, no paraphrase); include the period/date
the fact refers to if stated. Max 8 facts. If nothing useful, return an empty list.
Return JSON: {{"facts":[{{"category":"","fact":"","evidence":"","as_of":""}}]}}

PAGE URL: {c['url']}
PAGE TEXT:
{excerpt}""", STAGE, f"extract {focus or 'facts'} from {domain(c['url'])}")
        return got.get("facts", [])

    def extract(item):
        c, page, src = item
        if len(page["text"]) > LONG_DOC:  # long filing: one focused pass per category
            found = []
            for cat in CATEGORIES:
                found += extract_one(c, page["text"], cat)
        else:
            found = extract_one(c, page["text"], None)
        return c, page, src, {"facts": found}

    log(f"Extracting facts from {len(chosen)} pages in parallel", stage=STAGE)
    with ThreadPoolExecutor(WORKERS) as ex:
        extracted = list(ex.map(extract, chosen))

    facts, fid, seen_ev = [], 1, set()
    for c, page, src, got in extracted:
        kept = 0
        for f in got.get("facts", []):
            key = f.get("evidence", "")[:80].lower()
            if key in seen_ev:
                continue
            seen_ev.add(key)
            if not quote_in_text(f.get("evidence", ""), page["text"]):
                log(f"dropped unverified fact from {domain(c['url'])}: {f.get('fact','')[:70]}", level="warn", stage=STAGE)
                continue
            if f.get("category") not in CATEGORIES:
                f["category"] = "news"
            facts.append({"id": f"F{fid}", **f, "source_url": c["url"], "source_title": src["title"],
                          "evidence_verified": True})
            fid += 1
            kept += 1
        log(f"{domain(c['url'])}: kept {kept} verified facts", stage=STAGE)

    by_cat = {k: [f for f in facts if f.get("category") == k] for k in CATEGORIES}
    gaps = [k for k, v in by_cat.items() if not v]
    for k in gaps:
        log(f"No verified facts for {k}: marked 'not found'", level="warn", stage=STAGE)

    log("Synthesising research brief from verified facts", stage=STAGE)
    synth = ask_json(f"""You are writing the research brief on {company} for a FlytBase enterprise team.
Use ONLY the verified facts below; cite fact ids like [F3] after every claim. If a topic has no facts,
write "not found" for it. Prefer the most recent facts; say the year. Lead context: {lead}.
Return JSON: {{
 "org_structure_summary":"", "named_people":[{{"name":"","role":"","fact_id":""}}],
 "reporting_line_hypothesis":"(clearly labelled as hypothesis, cite facts)",
 "budget_summary":"", "news_summary":"", "operational_priorities":"",
 "positioning_recommendation":["3-5 bullets: how FlytBase should position itself, each citing facts"],
 "top_findings":[{{"finding":"","why_it_matters_for_the_deal":"","fact_ids":[""]}}]
}}
Give exactly 5 top_findings ranked by usefulness to the deal.
FACTS: {facts}""", STAGE, "synthesise research brief")

    valid = {f["id"] for f in facts}
    for tf in synth.get("top_findings", []):
        tf["fact_ids"] = [i for i in tf.get("fact_ids", []) if i in valid]
    synth["top_findings"] = [t for t in synth.get("top_findings", []) if t["fact_ids"]]
    synth["named_people"] = [p for p in synth.get("named_people", []) if p.get("fact_id") in valid]

    return {
        "company": company,
        "queries": [{"category": c, "query": q} for c, q in queries],
        "sources": sources,
        "facts": facts,
        "facts_by_category": {k: (v if v else "not found") for k, v in by_cat.items()},
        "brief": synth,
        "stats": {"results": len(candidates), "pages_fetched": len(sources),
                  "pages_ok": sum(s["ok"] for s in sources), "verified_facts": len(facts),
                  "categories_not_found": gaps},
    }
