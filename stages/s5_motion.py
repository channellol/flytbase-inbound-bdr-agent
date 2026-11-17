"""Stage 5: Partner identification and go-to-market motion.

1. Find FlytBase partner pages through the sitemap (any URL containing 'partner').
2. Parse the partner directory text into structured partner records (LLM, text only).
3. Code filters to partners in the lead's region (LATAM country list) and to partners named
   in FlytBase case studies that mention the lead's company (from Stage 4).
4. LLM recommends direct AE vs partner-led, with justification across region,
   deal complexity and partner availability.
"""
from __future__ import annotations

from core.llm import ask_json
from core.log import log
from core.web import fetch, fetch_xml_locs, relevant_excerpt

STAGE = "5-sales-motion"
SITE = "https://flytbase.com"
LATAM = ["Argentina", "Bolivia", "Brazil", "Chile", "Colombia", "Costa Rica", "Ecuador", "Guatemala",
         "Mexico", "Panama", "Paraguay", "Peru", "Uruguay", "Venezuela", "Dominican Republic", "Honduras",
         "El Salvador", "Nicaragua", "Puerto Rico"]


def run(ctx: dict) -> dict:
    lead, s4 = ctx["s1"]["lead"], ctx["s4"]
    pages_sm = []
    for sm in fetch_xml_locs(f"{SITE}/sitemap.xml"):
        if sm.endswith("/pages"):
            pages_sm = fetch_xml_locs(sm)
    partner_urls = sorted({u for u in pages_sm if "partner" in u and "/" + u.split("/")[3] + "/" not in
                           ["/es/", "/de/", "/fr/", "/ja/", "/ko/", "/zh/", "/pt/", "/ru/"]})
    log(f"Partner pages found in sitemap: {partner_urls}", stage=STAGE)

    texts = {}
    for u in partner_urls:
        p = fetch(u)
        if p["ok"]:
            texts[u] = p["text"]
    # region-filtered view of the directory, as a human would click "Americas"
    amer = fetch(f"{SITE}/partner?region=Americas")
    if amer["ok"]:
        texts[amer["url"]] = amer["text"]

    directory_url = next((u for u in texts if u.rstrip("/").endswith("/partner")), None)
    directory_text = texts.get(directory_url, "")
    log("Parsing partner directory", stage=STAGE)
    parsed = ask_json(f"""This is the text of FlytBase's public partner directory. Parse every partner listed
into records. Keep names exactly as written. Fields: name, tier (Gold/Silver/Bronze or ""), types (list,
e.g. Reseller, System Integrator, Solution Provider, Flinks, BVLOS Advisory, Dock Manufacturer, Master Distributor),
country. Also report the total partner count the page states.
Return JSON: {{"stated_total":"","partners":[{{"name":"","tier":"","types":[""],"country":""}}]}}
TEXT:
{directory_text[:30000]}""", STAGE, "parse partner directory")
    partners = parsed.get("partners", [])
    latam = [p for p in partners if p.get("country") in LATAM]
    in_country = [p for p in latam if p.get("country", "").lower() == (lead.get("country") or "").lower()]
    log(f"{len(partners)} partners parsed, {len(latam)} in LATAM, {len(in_country)} in {lead.get('country')}", stage=STAGE)

    # partners named inside case studies that mention the lead's own company
    own = [m for m in s4.get("org_mentions_on_site", []) if "sender" in m["role"].lower() or "lead" in m["role"].lower()]
    cs_context = []
    for m in own:
        p = fetch(m["url"])
        cs_context.append({"url": m["url"], "title": m["title"],
                           "excerpt": relevant_excerpt(p.get("text", ""), ["partner", "integrator", "deploy", "chile"], 3000)})
    if own:
        log(f"Lead's company already appears in {len(own)} FlytBase case stud(ies): checking which partner delivered it", stage=STAGE)

    rec = ask_json(f"""Recommend the go-to-market motion for this inbound lead: "direct_ae" or "partner_led"
(or "hybrid": AE owns the account, partner delivers). Justify on: region, deal complexity,
partner availability, and any existing relationship (e.g. a partner that already deployed at this company).
Name specific partners only from the lists below or from the case study excerpts. If a case study about
the lead's own company names an integrator that delivered it, that integrator comes first (existing
relationship beats directory listing); say whether it is listed in the directory. If partner-led, give the partner type and rationale.
Return JSON: {{"motion":"","one_line":"","justification":{{"region":"","deal_complexity":"","partner_availability":"",
"existing_relationship":""}},"recommended_partners":[{{"name":"","type":"","country":"","role_in_deal":"","source_url":""}}],
"risks":[""],"ae_actions":[""]}}
LEAD: {lead}
QUALIFICATION SCORES: {ctx['s1']['scores']}
LATAM PARTNERS (from {directory_url}): {latam}
CASE STUDIES ON FLYTBASE.COM THAT MENTION THE LEAD'S COMPANY: {cs_context}""", STAGE, "recommend sales motion")
    log(f"Recommended motion: {rec.get('motion')}", stage=STAGE)
    return {"partner_pages": list(texts), "directory_url": directory_url,
            "stated_total": parsed.get("stated_total"), "partners_parsed": len(partners),
            "latam_partners": latam, "in_country_partners": in_country,
            "lead_company_case_studies": [{"url": c["url"], "title": c["title"]} for c in cs_context],
            "recommendation": rec}
