"""Streamlit demo: streamlit run app.py"""
import json
from pathlib import Path

import streamlit as st

from core import log as logmod
from core.pipeline import STAGES, run

ROOT = Path(__file__).parent
st.set_page_config(page_title="FlytBase Inbound BDR Agent", page_icon="🛰️", layout="wide")
st.title("FlytBase Inbound BDR Agent")
st.caption("Raw inbound email in. Qualification, research, emails, case study, sales motion and AE handoff out. "
           "Six stages, structured JSON between them, every research fact checked against its source page.")

FLOW = [("1", "Qualify", "MEDDPICC score: knowns, unknowns, fit, priority"),
        ("2", "Research", "Web search + SEC filings, every fact quote-checked"),
        ("4", "Case study", "Crawl flytbase.com, match orgs named in email"),
        ("5", "Sales motion", "Crawl partner directory, pick direct vs partner"),
        ("3", "Emails", "4-email sequence built on unknowns + facts"),
        ("6", "AE handoff", "One-page brief and next step")]
st.markdown("**How it works** (runs left to right; each step passes JSON to the next. "
            "Emails run after the case study and partner steps so they can use them.)")
cols = st.columns(len(FLOW))
for c, (n, t, d) in zip(cols, FLOW):
    c.markdown(f"<div style='border:1px solid #555;border-radius:10px;padding:10px;height:120px'>"
               f"<div style='font-size:12px;opacity:.7'>STAGE {n}</div><b>{t}</b>"
               f"<div style='font-size:12px;opacity:.8;margin-top:4px'>{d}</div></div>", unsafe_allow_html=True)
st.write("")
email = st.text_area("Inbound email (the only input)", (ROOT / "input" / "inbound_email.txt").read_text(), height=130)
go = st.button("Run pipeline live (about 4 min)", type="primary")
show_last = not go


def render(key, data):
    if key == "s1":
        sc = data["scores"]
        m = st.columns(4)
        m[0].metric("Fit", f"{sc['fit_score_0_10']}/10")
        m[1].metric("Priority", f"{sc['priority_score_0_10']}/10")
        m[2].metric("MEDDPICC completeness", f"{sc['meddpicc_completeness_0_10']}/10")
        m[3].metric("Tier", sc["priority_tier"].split(" ")[0])
        st.dataframe([{k: e.get(k) for k in ("element", "status", "evidence", "missing", "discovery_question")}
                      for e in data["meddpicc"]], width="stretch", hide_index=True)
    elif key == "s2":
        s = data["stats"]
        st.write(f"**{s['verified_facts']} verified facts** from {s['pages_ok']} pages. Not found: {s['categories_not_found'] or 'none'}")
        for t in data["brief"].get("top_findings", []):
            st.markdown(f"- {t['finding']} `{', '.join(t['fact_ids'])}`")
        st.dataframe([{"id": f["id"], "category": f["category"], "fact": f["fact"], "source": f["source_url"]}
                      for f in data["facts"]], width="stretch", hide_index=True,
                     column_config={"source": st.column_config.LinkColumn()})
    elif key == "s4":
        st.write("Orgs in email: " + ", ".join(f"**{o['name']}** ({o['role']})" for o in data["email_orgs"]))
        for m in data["org_mentions_on_site"]:
            st.write(f"Found on flytbase.com: {m['org']} -> [{m['title']}]({m['url']})")
        st.dataframe([{"case study": r.get("customer") or r["title"], "score": r["weighted_score"], "reason": r.get("reason"),
                       "url": r["url"]} for r in data["ranking"][:8]], width="stretch", hide_index=True,
                     column_config={"url": st.column_config.LinkColumn()})
    elif key == "s5":
        r = data["recommendation"]
        st.success(f"{r.get('motion')}: {r.get('one_line')}")
        st.json(r.get("justification", {}))
    elif key == "s3":
        tabs = st.tabs([f"Email {e['step']} (day {e.get('day')})" for e in data.get("emails", [])] or ["none"])
        for t, e in zip(tabs, data.get("emails", [])):
            with t:
                st.markdown(f"**{e.get('subject')}**")
                st.caption(f"Send if: {e.get('send_if')} | Targets: {', '.join(e.get('targets_unknowns', []))}")
                st.text(e.get("body", ""))
                st.caption(f"Why now: {e.get('progression_logic')}")
    elif key == "s6":
        st.info(data.get("headline", ""))
        ns = data.get("next_step", {})
        st.write(f"**Next step:** {ns.get('action')} ({ns.get('when')}). {ns.get('rationale')}")
    st.caption("Raw JSON passed to the next stage")
    st.json(data, expanded=False)


if go:
    boxes, logs = {}, {}
    for key, name, _, _ in STAGES:
        boxes[key] = st.status(name, state="running" if key == "s1" else "complete", expanded=False)
        boxes[key].update(label=f"⏳ {name}")
    logmod.clear_listeners()

    def on_event(ev):
        k = ev.get("key")
        if ev["type"] == "stage_start":
            boxes[k].update(label=f"▶️ {ev['name']}", state="running", expanded=True)
            logs[k] = boxes[k].empty()
            st.session_state["current"] = k
            st.session_state.setdefault("lines", {})[k] = []
        elif ev["type"] == "log":
            k = st.session_state.get("current")
            if k:
                st.session_state["lines"][k].append(f"{ev['ts'][11:]}  {ev['msg']}")
                logs[k].code("\n".join(st.session_state["lines"][k][-12:]))
        elif ev["type"] == "stage_done":
            boxes[k].update(label=f"✅ {ev['name']} ({ev['secs']}s)", state="complete", expanded=False)
            with boxes[k]:
                render(k, ev["data"])
        elif ev["type"] == "stage_error":
            boxes[k].update(label=f"❌ {ev['name']}: {ev['error']}", state="error")

    logmod.subscribe(on_event)
    try:
        run(email)
        st.balloons()
        st.markdown((ROOT / "output" / "report.md").read_text())
    except Exception as e:
        st.error(str(e))

if show_last:
    have = [(k, n, f) for k, n, _, f in STAGES if (ROOT / "output" / f).exists()]
    if have:
        st.subheader("Results of the last run")
        order = ["s1", "s2", "s3", "s4", "s5", "s6"]
        have.sort(key=lambda x: order.index(x[0]))
        tabs = st.tabs([n.split(": ", 1)[0] + ": " + n.split(": ", 1)[1].split(" (")[0] for _, n, _ in have] + ["Full report"])
        for t, (key, name, fname) in zip(tabs, have):
            with t:
                render(key, json.loads((ROOT / "output" / fname).read_text()))
        with tabs[-1]:
            rp = ROOT / "output" / "report.md"
            if rp.exists():
                st.download_button("Download report.md", rp.read_text(), "report.md")
                st.markdown(rp.read_text())
