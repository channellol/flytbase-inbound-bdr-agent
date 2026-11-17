"""Stage 4: Case study matching.

1. Discover case studies on flytbase.com via its sitemap index (falls back to link crawl).
2. Fetch every English case study page.
3. Detect organisations named in the email (referrer, the lead's own company) with the LLM,
   then string-match them against case study text in code. Nothing about Anglo American is
   hard-coded: whatever company the email names is what gets matched.
4. LLM scores each case study on a fixed rubric; code computes the weighted total and picks the winner.
"""
from __future__ import annotations

import re

from core.llm import ask_json
from core.log import log
from core.web import fetch, fetch_xml_locs, norm

STAGE = "4-case-study"
SITE = "https://flytbase.com"
LANG = re.compile(r"flytbase\.com/(es|de|fr|ja|ko|zh|pt|ru|it|ar)/")
WEIGHTS = {"industry_match": 0.30, "use_case_match": 0.25, "operating_context_match": 0.20,
           "named_org_match": 0.15, "region_match": 0.10}


def discover() -> list[str]:
    urls = []
    for sm in fetch_xml_locs(f"{SITE}/sitemap.xml"):
        if "case" in sm or "stor" in sm or "customer" in sm:
            urls += fetch_xml_locs(sm)
    urls = [u for u in urls if "/case-studies/" in u and not LANG.search(u)]
    if not urls:  # fallback: crawl from the homepage
        log("sitemap gave no case studies, crawling links", level="warn", stage=STAGE)
        home = fetch(SITE)
        hubs = [l for l in home.get("links", []) if "case" in l.lower()]
        for h in hubs[:3]:
            urls += [l for l in fetch(h).get("links", []) if "/case-studies/" in l]
    return sorted(set(urls))


def run(ctx: dict) -> dict:
    lead = ctx["s1"]["lead"]
    log("Discovering case studies on flytbase.com", stage=STAGE)
    urls = discover()
    log(f"Found {len(urls)} English case study URLs", stage=STAGE)

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(8) as ex:
        fetched = list(ex.map(fetch, urls))
    pages = []
    for u, p in zip(urls, fetched):
        if p["ok"]:
            # drop the "related stories" footer so cross-links don't count as mentions
            body = re.split(r"Related customer stories|Related case studies|More case studies", p["text"])[0]
            pages.append({"url": u, "title": p["title"], "text": body})
    log(f"Fetched {len(pages)} case studies", stage=STAGE)

    ents = ask_json(f"""List every organisation named in this inbound email, with its role
(e.g. "sender's company", "referrer", "competitor", "partner") and common name variants/short forms.
Return JSON: {{"orgs":[{{"name":"","role":"","variants":[""]}}]}}
EMAIL:
{ctx['email']}""", STAGE, "detect organisations in email")
    orgs = ents.get("orgs", [])
    log("Organisations in email: " + ", ".join(f"{o['name']} ({o['role']})" for o in orgs), stage=STAGE)

    # code: where is each named org mentioned on the FlytBase site?
    mentions = []
    for o in orgs:
        names = {o["name"], *o.get("variants", [])}
        names = [n for n in names if n and len(n) >= 3]
        for p in pages:
            hay = norm(p["title"] + " " + p["text"])
            hits = [n for n in names if re.search(r"\b" + re.escape(norm(n)) + r"\b", hay)]
            if hits:
                mentions.append({"org": o["name"], "role": o["role"], "url": p["url"],
                                 "title": p["title"], "matched_on": hits})
    for m in mentions:
        log(f"'{m['org']}' ({m['role']}) is mentioned in: {m['title']}", stage=STAGE)

    catalog = [{"id": i, "url": p["url"], "title": p["title"], "excerpt": p["text"][:1800]}
               for i, p in enumerate(pages)]
    scored = ask_json(f"""Score each FlytBase case study for relevance to this lead, 0-10 per criterion:
industry_match (mining / extractive), use_case_match (autonomous inspection / monitoring with docks),
operating_context_match (remote, large-area, 24/7, harsh desert or hazardous sites, replacing manual crews),
named_org_match (case study features an organisation named in the email; see mentions),
region_match (Chile / LATAM / South America).
Base every score on the excerpt text only. Also extract, for each, customer, industry, location,
and up to 3 concrete results stated in the text (verbatim numbers only, else "not stated").
Return JSON: {{"scores":[{{"id":0,"customer":"","industry":"","location":"","results":[""],
"industry_match":0,"use_case_match":0,"operating_context_match":0,"named_org_match":0,"region_match":0,
"reason":""}}]}}
LEAD: {lead}
EMAIL ORG MENTIONS ON FLYTBASE SITE: {mentions}
CASE STUDIES: {catalog}""", STAGE, f"score {len(catalog)} case studies")

    rows = []
    for s in scored.get("scores", []):
        if not isinstance(s.get("id"), int) or s["id"] >= len(catalog):
            continue
        total = round(sum(float(s.get(k, 0)) * w for k, w in WEIGHTS.items()), 2)
        rows.append({**s, "url": catalog[s["id"]]["url"], "title": catalog[s["id"]]["title"],
                     "weighted_score": total})
    rows.sort(key=lambda r: r["weighted_score"], reverse=True)
    best = rows[0] if rows else None
    runner_up = rows[1] if len(rows) > 1 else None

    why = {}
    if best:
        page = next(p for p in pages if p["url"] == best["url"])
        why = ask_json(f"""Explain why this case study is the strongest fit for the lead, using only the
case study text and the lead. Also say how the second-best one should be used (e.g. as a referral proof point).
Mention which named organisations from the email appear, if any.
Return JSON: {{"why_best":["3-4 bullets"],"how_to_use_in_outreach":"","use_of_runner_up":"",
"proof_points":["verbatim facts from the case study text"]}}
LEAD: {lead}
MENTIONS: {mentions}
BEST: {best['title']} {best['url']}
TEXT: {page['text'][:6000]}
RUNNER UP: {runner_up['title'] if runner_up else 'none'} {runner_up['url'] if runner_up else ''}""",
                       STAGE, "explain best match")
        log(f"Best match: {best['title']} ({best['weighted_score']}/10)", stage=STAGE)

    return {"discovered_count": len(urls), "fetched_count": len(pages), "weights": WEIGHTS,
            "email_orgs": orgs, "org_mentions_on_site": mentions,
            "ranking": rows, "best": best, "runner_up": runner_up, "rationale": why}
