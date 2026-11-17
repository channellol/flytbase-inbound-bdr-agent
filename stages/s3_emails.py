"""Stage 3: Adaptive email sequence.

Runs after stages 4 and 5 so the emails can use the matched case study and partner.
Each email targets specific Stage 1 unknowns and cites Stage 2 fact ids; code checks
that every cited fact id exists and that no email contains an em dash.
"""
from __future__ import annotations

import re

from pathlib import Path

from core.llm import ask_writer
from core.log import log

STAGE = "3-email-sequence"
GUIDE = (Path(__file__).resolve().parent.parent / "prompts" / "writing_guide.md").read_text()


def run(ctx: dict) -> dict:
    s1, s2, s4, s5 = ctx["s1"], ctx["s2"], ctx["s4"], ctx["s5"]
    facts = [{k: f[k] for k in ("id", "category", "fact", "as_of", "source_url")} for f in s2["facts"]]
    best = s4.get("best") or {}
    seq = ask_writer(f"""Follow this writing guide exactly:
{GUIDE}

Write an adaptive 4-email sequence from a FlytBase inbound BDR to {s1['lead'].get('name')}
({s1['lead'].get('title')}, {s1['lead'].get('company')}). He contacted us first, so email 1 is a fast reply, not cold outreach.

Hard rules:
- Each email targets 1-3 specific MEDDPICC unknowns below and asks at most 2 questions.
- Use research facts only from FACTS and cite their ids in "fact_ids_used". Do not invent numbers,
  customers or claims about FlytBase beyond the case study and partner info provided.
- Tone: he is a senior operations leader in his industry; adapt to his seniority, industry and region.
- Greeting "Hi Rodrigo,". No em dashes. Sign off as "[BDR name], FlytBase".
- Offer Spanish once if useful. Email 1 thanks him plainly for the referral if he named one.
- If the best case study features the lead's OWN company, treat this as an expansion, not a new logo:
  he may or may not know about that deployment. Email 1 should mention it neutrally and ask whether his
  division is connected to it (this uncovers Champion, Decision Process and Competition at once).
  Never say "our deployment" as if he was part of it.
- Mention the integrator partner named in the sales motion where it helps.
- Adaptive: for emails 2-4 give a "send_if" condition (e.g. no reply after N days / replied with X) and an
  "alternate_if_replied" short note describing how the email changes if he already replied.
- "progression_logic": why this email comes now and what it unlocks for the next.

Return JSON: {{"sequence_strategy":"","emails":[{{"step":1,"day":0,"send_if":"","subject":"","body":"",
"targets_unknowns":[""],"fact_ids_used":[""],"progression_logic":"","alternate_if_replied":""}}]}}

LEAD: {s1['lead']}
UNKNOWNS: {s1['unknown']}
PARTIAL: {s1['partial']}
FACTS: {facts}
RESEARCH BRIEF: {s2['brief'].get('positioning_recommendation')}
BEST CASE STUDY: {best.get('title')} {best.get('url')} results: {best.get('results')}
CASE STUDY RATIONALE: {s4.get('rationale')}
RUNNER UP: {(s4.get('runner_up') or {}).get('title')} {(s4.get('runner_up') or {}).get('url')}
SALES MOTION: {s5['recommendation'].get('one_line')}""", STAGE, "write email sequence")

    # code-level checks: expansion angle in email 1, no invented assets; one automatic rewrite if violated
    short = (s1["lead"].get("company_short") or "").lower()
    expansion = bool(best) and short and short in (best.get("title", "") + " " + str(best.get("customer", ""))).lower()
    banned = ["guide", "whitepaper", "white paper", "playbook", "deck", "roi model", "pricing", "free trial", "pilot program", "a pilot", "pilot at",
              "technical requirements", "documentation", "share the specific", "production targets", "we achieved",
              "i am curious", "i noticed", "clutter your inbox", "respect your time", "hop on", "dive into",
              "proven way", "similar site", "unnecessary noise", "it is clear that", "the pressure to", "massive", "robust", "streamline", "i hope"]

    def problems(sq):
        out = []
        emails = sq.get("emails", [])
        if expansion and emails and not (short in emails[0].get("body", "").lower()
                                                 and any(k in emails[0].get("body", "").lower() for k in ("already", "existing", "678"))):
            out.append("Email 1 must mention, neutrally, that FlytBase already has a deployment at the lead's own company "
                       "(see BEST CASE STUDY) and ask whether his division is connected to it.")
        for e in emails:
            hit = [b for b in banned if b in e.get("body", "").lower()]
            if re.search(r"\bpilot", e.get("body", ""), re.I):
                hit.append("pilot")
            if hit:
                out.append(f"Email {e.get('step')} offers {hit}, which are not in the inputs. Remove; offer only the case study link or a short call.")
        ref = (s1["lead"].get("referral_source") or "").strip()
        if ref and ref.lower() not in ("not found", "unknown", "none") and emails \
                and ref.lower() not in emails[0].get("body", "").lower():
            out.append(f"Email 1 must thank him plainly for the referral from {ref}.")
        for e in emails:
            n = len(re.findall(r"[A-Za-z0-9']+", e.get("body", "").replace("[BDR name], FlytBase", "")))
            if n < 70 or n > 125:
                out.append(f"Email {e.get('step')} body is {n} words; it must be 70 to 120 words.")
        valid_ids = {f["id"] for f in facts}
        for e in emails[1:3]:
            if not [i for i in e.get("fact_ids_used", []) if i in valid_ids]:
                out.append(f"Email {e.get('step')} uses no research fact. Use one specific number or event from FACTS "
                           "(for example a capex figure or a named project) and cite its id in fact_ids_used.")
        return out

    issues_first = problems(seq)
    original = [dict(e) for e in seq.get("emails", [])]
    issues, best_seq, best_n = issues_first, seq, len(issues_first)
    for attempt in range(3):  # rewrite until the checks pass, keep the best version
        if not issues:
            break
        for i in issues:
            log(f"check failed (round {attempt + 1}): {i}", level="warn", stage=STAGE)
        seq = ask_writer(f"""Rewrite this email sequence to fix these problems, keeping everything else:
{issues}
Rules that matter most: email 1 thanks him for the referral by name, mentions neutrally that FlytBase already
has a deployment at his company and asks if his division is connected. Emails are written by the FlytBase BDR
about FlytBase; never write as if you were the customer. Each body 70 to 120 words.
Writing guide:
{GUIDE}
LEAD: {s1['lead']}
BEST CASE STUDY: {best.get('title')} {best.get('url')}
CASE STUDY RATIONALE: {s4.get('rationale')}
Credit the case study results to the deployment (FlytBase with the partner), never "we achieved".
Do not claim anything about his company that is not in FACTS.
Return the same JSON shape.
FACTS: {facts}
SEQUENCE: {seq}""", STAGE, f"rewrite email sequence (round {attempt + 1})")
        issues = problems(seq)
        if len(issues) < best_n:
            best_seq, best_n = seq, len(issues)
    seq = best_seq
    for i in problems(seq):
        log(f"still failing after rewrites: {i}", level="warn", stage=STAGE)
    if True:
        # keep the sequencing metadata from the first draft if the rewrite dropped any of it
        for n, e in enumerate(seq.get("emails", [])):
            if n < len(original):
                for k in ("step", "day", "send_if", "targets_unknowns", "progression_logic", "alternate_if_replied"):
                    if not e.get(k) and original[n].get(k):
                        e[k] = original[n][k]

    valid = {f["id"] for f in s2["facts"]}
    issues = []
    for e in seq.get("emails", []):
        bad = [i for i in e.get("fact_ids_used", []) if i not in valid]
        if bad:
            issues.append(f"email {e.get('step')}: removed unknown fact ids {bad}")
            e["fact_ids_used"] = [i for i in e["fact_ids_used"] if i in valid]
        if re.search("[—–]", e.get("body", "") + e.get("subject", "")):
            issues.append(f"email {e.get('step')}: dash found")
        e["body"] = re.sub(r"\s*[\(\[]F\d+[\)\]]", "", e.get("body", ""))  # fact ids belong in metadata, not in the email
        if "FlytBase" not in e["body"].strip().splitlines()[-1]:
            e["body"] = e["body"].rstrip() + "\n\n[BDR name], FlytBase"
        e["word_count"] = len(e.get("body", "").split())
        e["sources"] = [next(f["source_url"] for f in s2["facts"] if f["id"] == i) for i in e["fact_ids_used"]]
    for i in issues:
        log(i, level="warn", stage=STAGE)
    log(f"{len(seq.get('emails', []))} emails written", stage=STAGE)
    return {**seq, "checks": issues or ["all fact ids valid, no dashes"]}
