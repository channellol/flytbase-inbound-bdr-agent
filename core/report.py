"""Combine all stage outputs into one readable Markdown report."""
from __future__ import annotations

import datetime as dt

from core.log import OUTPUT


def _li(items):
    return "\n".join(f"- {x}" for x in items) if items else "- none"


def write_report(ctx: dict):
    s1, s2, s3, s4, s5, s6 = (ctx[k] for k in ("s1", "s2", "s3", "s4", "s5", "s6"))
    facts = {f["id"]: f for f in s2["facts"]}
    L = []
    a = L.append
    a("# Inbound Lead Report: " + f"{s1['lead'].get('name')} / {s1['lead'].get('company')}")
    a(f"_Generated automatically by the FlytBase inbound BDR agent on {dt.datetime.now():%Y-%m-%d %H:%M}. "
      f"Stage timings (s): {ctx.get('timings')}_\n")
    a("## Input email\n```\n" + ctx["email"] + "\n```\n")

    # Stage 6 first: it is what the AE reads
    a("## Stage 6: AE handoff\n")
    a(f"**{s6.get('headline','')}**\n")
    bc = s6.get("buyer_context", {})
    a(f"**Buyer:** {bc.get('who','')}\n\n**Cares about:**\n{_li(bc.get('cares_about', []))}\n")
    a("**Stakeholders:**\n" + _li([f"{(x.get('name') + ', ') if x.get('name') else ''}{x.get('role')}: {x.get('why')} ({x.get('known_or_hypothesis')})"
                                    for x in bc.get("likely_stakeholders", [])]) + "\n")
    q = s6.get("qualification_status", {})
    a(f"**Qualification:** {q.get('scores','')}\n\nKnown:\n{_li(q.get('knowns', []))}\n\nUnknown:\n{_li(q.get('unknowns', []))}\n")
    a("**Top 3 research findings:**\n")
    for i, f in enumerate(s6.get("top_3_research_findings", []), 1):
        a(f"{i}. {f.get('finding')} _So what:_ {f.get('so_what')} Sources: " +
          ", ".join(f"[{fid}]({facts[fid]['source_url']})" for fid in f.get("fact_ids", [])))
    cs = s6.get("recommended_case_study", {})
    a(f"\n**Case study:** [{cs.get('title')}]({cs.get('url')}): {cs.get('why')}\n")
    a(f"**Sales motion:** {s6.get('sales_motion','')}\n")
    ns = s6.get("next_step", {})
    a(f"**Next step:** {ns.get('action')} (owner: {ns.get('owner')}, when: {ns.get('when')}). {ns.get('rationale')}\n")
    a("**Watch-outs:**\n" + _li(s6.get("watch_outs", [])) + "\n")

    a("## Stage 1: Qualification (MEDDPICC)\n")
    a(f"_Why MEDDPICC:_ {s1['framework_reasoning']}\n")
    sc = s1["scores"]
    a(f"| Completeness | Fit | Priority | Tier |\n|---|---|---|---|\n| {sc['meddpicc_completeness_0_10']}/10 | "
      f"{sc['fit_score_0_10']}/10 | {sc['priority_score_0_10']}/10 | {sc['priority_tier']} |\n")
    a(f"_Scoring rule:_ {sc['scoring_rule']}\n")
    a("| Element | Status | Evidence | Missing | Discovery question |\n|---|---|---|---|---|")
    for e in s1["meddpicc"]:
        a(f"| {e.get('element')} | {e.get('status')} | {e.get('evidence','')} | {e.get('missing','')} | {e.get('discovery_question','')} |")
    a("\n**Fit signals:**\n" + _li([f"{x['signal']} ({x.get('evidence','')})" for x in s1["fit_signals"]]))
    a("\n**Risk signals:**\n" + _li([f"{x['signal']}: {x.get('why','')}" for x in s1["risk_signals"]]) + "\n")

    a("## Stage 2: Account research\n")
    st = s2["stats"]
    a(f"{st['results']} search results, {st['pages_ok']}/{st['pages_fetched']} pages fetched, "
      f"{st['verified_facts']} facts kept after verbatim-evidence check. "
      f"Categories not found: {st['categories_not_found'] or 'none'}\n")
    b = s2["brief"]
    for k, label in [("org_structure_summary", "Org structure"), ("reporting_line_hypothesis", "Reporting line (hypothesis)"),
                     ("budget_summary", "Budget signals"), ("news_summary", "Recent news"),
                     ("operational_priorities", "Operational priorities (investor / stakeholder letters)")]:
        a(f"**{label}:** {b.get(k, 'not found')}\n")
    a("**Named people:**\n" + _li([f"{p['name']}, {p['role']} [{p['fact_id']}]" for p in b.get("named_people", [])]) + "\n")
    a("**Positioning recommendation:**\n" + _li(b.get("positioning_recommendation", [])) + "\n")
    a("### Fact table (every fact verified against its source page)\n")
    a("| ID | Category | Fact | As of | Source |\n|---|---|---|---|---|")
    for f in s2["facts"]:
        a(f"| {f['id']} | {f.get('category')} | {f.get('fact')} | {f.get('as_of','')} | [{f['source_title'][:50] or 'link'}]({f['source_url']}) |")
    a("")

    a("## Stage 3: Email sequence\n")
    a(f"_Strategy:_ {s3.get('sequence_strategy','')}\n")
    for e in s3.get("emails", []):
        a(f"### Email {e.get('step')} (day {e.get('day')}): {e.get('subject')}\n")
        a(f"_Send if:_ {e.get('send_if')}  \n_Targets:_ {', '.join(e.get('targets_unknowns', []))}  \n"
          f"_Facts used:_ {', '.join(e.get('fact_ids_used', [])) or 'none'}\n")
        a("```\n" + e.get("body", "") + "\n```")
        a(f"_Progression logic:_ {e.get('progression_logic')}  \n_If he already replied:_ {e.get('alternate_if_replied')}\n")
    a(f"_Checks:_ {s3.get('checks')}\n")

    a("## Stage 4: Case study match\n")
    a(f"Discovered {s4['discovered_count']} case studies on flytbase.com, scored {len(s4['ranking'])}.\n")
    a("**Organisations detected in the email:** " + ", ".join(f"{o['name']} ({o['role']})" for o in s4["email_orgs"]) + "\n")
    a("**Where they appear on flytbase.com:**\n" + _li([f"{m['org']}: [{m['title']}]({m['url']})" for m in s4["org_mentions_on_site"]]) + "\n")
    a("| Rank | Case study | Industry | Use case | Context | Named org | Region | Weighted |\n|---|---|---|---|---|---|---|---|")
    for i, r in enumerate(s4["ranking"][:8], 1):
        a(f"| {i} | [{r.get('customer') or r['title']}]({r['url']}) | {r.get('industry_match')} | {r.get('use_case_match')} | "
          f"{r.get('operating_context_match')} | {r.get('named_org_match')} | {r.get('region_match')} | **{r['weighted_score']}** |")
    rat = s4.get("rationale", {})
    a("\n**Why the top match:**\n" + _li(rat.get("why_best", [])))
    a(f"\n**How to use it:** {rat.get('how_to_use_in_outreach','')}\n\n**Runner-up use:** {rat.get('use_of_runner_up','')}\n")

    a("## Stage 5: Sales motion\n")
    r = s5["recommendation"]
    a(f"**Recommendation: {r.get('motion')}**. {r.get('one_line')}\n")
    for k, v in r.get("justification", {}).items():
        a(f"- **{k.replace('_', ' ').title()}:** {v}")
    a("\n**Partners:**\n" + _li([f"{p.get('name')} ({p.get('type')}, {p.get('country')}): {p.get('role_in_deal')}"
                                for p in r.get("recommended_partners", [])]))
    a(f"\n_Partner directory: {s5['directory_url']} ({s5['partners_parsed']} parsed, "
      f"{len(s5['latam_partners'])} in LATAM: {', '.join(p['name'] + ' (' + p['country'] + ')' for p in s5['latam_partners'])})_\n")
    a("**Risks:**\n" + _li(r.get("risks", [])) + "\n")

    path = OUTPUT / "report.md"
    path.write_text("\n".join(L).replace("—", ", ").replace("–", "-"))
    return path
