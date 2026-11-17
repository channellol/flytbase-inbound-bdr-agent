"""Stage 1: Qualification with MEDDPICC.

Step A (LLM): parse the raw email into structured lead fields.
Step B (LLM): assess each MEDDPICC element as known / partial / unknown, citing email text.
Step C (code): compute fit and priority scores from the element statuses and explicit
                fit signals, so the scoring rule is inspectable and not a model whim.
"""
from __future__ import annotations

from core.llm import ask_json
from core.log import log

STAGE = "1-qualification"
ELEMENTS = ["Metrics", "Economic Buyer", "Decision Criteria", "Decision Process",
            "Paper Process", "Identify Pain", "Champion", "Competition"]

FRAMEWORK_WHY = (
    "MEDDPICC was chosen over BANT because this is an enterprise, multi-site mining deal: "
    "a Head of Operations is rarely the economic buyer, procurement and safety approval "
    "(Paper Process) at a listed miner is long, and the email already hints at a champion "
    "and a budget event (Q3). MEDDPICC separates those threads; BANT would collapse them into "
    "'budget yes/no' and hide what the AE still has to discover."
)


def run(ctx: dict) -> dict:
    email = ctx["email"]
    log("Parsing inbound email into structured fields", stage=STAGE)
    lead = ask_json(f"""Extract structured fields from this inbound lead email. Use only what is written.
Return JSON: {{"name":"","email":"","title":"","company":"","company_short":"","country":"",
"email_domain":"","subject":"","sites_count":null,"site_description":"","operating_hours":"",
"use_case":"","stated_pain":[""],"referral_source":"","budget_timing":"","industry":"","commodity":"",
"region":""}}
For referral_source give the organisation or person named as the referrer, or "" if none.

EMAIL:
{email}""", STAGE, "parse email")

    log("Assessing MEDDPICC elements against email evidence", stage=STAGE)
    meddpicc = ask_json(f"""Apply MEDDPICC to this inbound lead. For each element give status
"known", "partial" or "unknown", the evidence quoted verbatim from the email (or "" if none),
what is still missing, and one discovery question an SDR would ask to close the gap.
Do not assume anything not in the email. Elements: {ELEMENTS}.

Also list fit_signals: concrete reasons this matches FlytBase's ideal customer profile
(large industrial sites, 24/7 operations, autonomous dock-based drones, hazardous manual
inspection, enterprise budget), each with evidence from the email, and risk_signals.

Return JSON: {{"elements":[{{"element":"","status":"","evidence":"","missing":"","discovery_question":""}}],
"fit_signals":[{{"signal":"","evidence":""}}],"risk_signals":[{{"signal":"","why":""}}],
"summary":""}}

LEAD (parsed): {lead}
EMAIL:
{email}""", STAGE, "MEDDPICC assessment")

    # ---- deterministic scoring (inspectable) ----
    weights = {"known": 1.0, "partial": 0.5, "unknown": 0.0}
    els = meddpicc.get("elements", [])
    completeness = round(10 * sum(weights.get(e.get("status", "unknown"), 0) for e in els) / max(len(ELEMENTS), 1), 1)
    n_fit = len(meddpicc.get("fit_signals", []))
    n_risk = len(meddpicc.get("risk_signals", []))
    fit_score = max(0, min(10, 4 + n_fit - 0.5 * n_risk))
    timing_bonus = 2 if lead.get("budget_timing") else 0
    referral_bonus = 2 if lead.get("referral_source") else 0
    priority_score = round(min(10, 0.5 * fit_score + 0.2 * completeness + timing_bonus + referral_bonus), 1)
    tier = "P1 (respond same day)" if priority_score >= 7 else "P2 (respond within 48h)" if priority_score >= 5 else "P3 (nurture)"

    known = [e for e in els if e.get("status") == "known"]
    partial = [e for e in els if e.get("status") == "partial"]
    unknown = [e for e in els if e.get("status") == "unknown"]
    out = {
        "framework": "MEDDPICC",
        "framework_reasoning": FRAMEWORK_WHY,
        "lead": lead,
        "meddpicc": els,
        "known": [f"{e['element']}: {e.get('evidence','')}" for e in known],
        "partial": [f"{e['element']}: {e.get('evidence','')} (missing: {e.get('missing','')})" for e in partial],
        "unknown": [f"{e['element']}: {e.get('missing','')}" for e in unknown],
        "fit_signals": meddpicc.get("fit_signals", []),
        "risk_signals": meddpicc.get("risk_signals", []),
        "scores": {
            "meddpicc_completeness_0_10": completeness,
            "fit_score_0_10": fit_score,
            "priority_score_0_10": priority_score,
            "priority_tier": tier,
            "scoring_rule": ("completeness = known 1 / partial 0.5 / unknown 0 averaged over 8 elements x10; "
                             "fit = 4 + fit signals - 0.5 x risk signals (0-10); priority = 0.5 fit + 0.2 "
                             "completeness + 2 if a budget date is named + 2 if referred by a known account"),
        },
        "summary": meddpicc.get("summary", ""),
    }
    log(f"Fit {fit_score}/10, priority {priority_score}/10, {len(unknown)} unknown elements", stage=STAGE)
    return out
