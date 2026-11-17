"""Stage 6: AE handoff summary, built only from the structured outputs of stages 1-5."""
from __future__ import annotations

from core.llm import ask_writer
from core.log import log

STAGE = "6-ae-handoff"


def run(ctx: dict) -> dict:
    s1, s2, s3, s4, s5 = (ctx[k] for k in ("s1", "s2", "s3", "s4", "s5"))
    facts = {f["id"]: f for f in s2["facts"]}
    out = ask_writer(f"""Write the AE handoff for this inbound lead. An AE must be able to act on it in 2 minutes.
Use only the inputs. Cite fact ids for research claims. No em dashes. Plain, specific sentences;
no filler. Name real people from the research (named_people) as stakeholders where relevant, marked
"known" only if a fact names them, otherwise "hypothesis". The next step must be concrete: what, who, by when,
and what it unlocks.
Return JSON: {{
 "headline":"one line: who, what, how hot, what to do",
 "buyer_context":{{"who":"","cares_about":[""],"likely_stakeholders":[{{"name":"","role":"","why":"","known_or_hypothesis":""}}]}},
 "qualification_status":{{"framework":"MEDDPICC","scores":"","knowns":[""],"unknowns":[""]}},
 "top_3_research_findings":[{{"finding":"","so_what":"","fact_ids":[""]}}],
 "recommended_case_study":{{"title":"","url":"","why":""}},
 "sales_motion":"",
 "next_step":{{"action":"","owner":"","when":"","rationale":""}},
 "watch_outs":[""]
}}
STAGE1: {s1}
STAGE2 BRIEF: {s2['brief']}
STAGE2 FACTS: {list(facts.values())}
STAGE3 EMAIL 1 SUBJECT: {(s3.get('emails') or [{}])[0].get('subject')}
STAGE4 BEST: {s4.get('best')} RATIONALE: {s4.get('rationale')}
STAGE5: {s5['recommendation']}""", STAGE, "write AE handoff", temperature=0.3)
    for f in out.get("top_3_research_findings", []):
        f["fact_ids"] = [i for i in f.get("fact_ids", []) if i in facts]
        f["sources"] = sorted({facts[i]["source_url"] for i in f["fact_ids"]})
    log("Handoff ready", stage=STAGE)
    return out
