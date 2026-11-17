"""Orchestrator: runs the six stages in dependency order, passing a shared JSON context."""
from __future__ import annotations

import datetime as dt
import json
import time
import traceback

from core.llm import MODEL, check_key
from core.log import OUTPUT, emit, log, reset_log
from core.report import write_report
from stages import s1_qualify, s2_research, s3_emails, s4_case_study, s5_motion, s6_handoff

# Execution order differs from numbering on purpose: the email sequence (3) uses the
# matched case study (4) and the sales motion (5), so it runs after them.
STAGES = [
    ("s1", "Stage 1: Qualification (MEDDPICC)", s1_qualify, "stage1_qualification.json"),
    ("s2", "Stage 2: Deep account research", s2_research, "stage2_research.json"),
    ("s4", "Stage 4: Case study matching", s4_case_study, "stage4_case_study.json"),
    ("s5", "Stage 5: Partner / sales motion", s5_motion, "stage5_sales_motion.json"),
    ("s3", "Stage 3: Adaptive email sequence", s3_emails, "stage3_email_sequence.json"),
    ("s6", "Stage 6: AE handoff", s6_handoff, "stage6_ae_handoff.json"),
]


def run(email: str) -> dict:
    check_key()
    reset_log()
    ctx = {"email": email.strip(), "today": dt.date.today().isoformat()}
    log(f"Run started. Model: {MODEL}. Input: inbound email ({len(email)} chars)")
    timings = {}
    for key, name, mod, fname in STAGES:
        emit({"type": "stage_start", "key": key, "name": name})
        log(f"=== {name} ===")
        t0 = time.time()
        try:
            ctx[key] = mod.run(ctx)
        except Exception as e:
            log(f"{name} failed: {e}", level="error")
            log(traceback.format_exc(), level="error")
            emit({"type": "stage_error", "key": key, "name": name, "error": str(e)})
            raise
        timings[key] = round(time.time() - t0, 1)
        (OUTPUT / fname).write_text(json.dumps(ctx[key], indent=2, ensure_ascii=False))
        log(f"{name} done in {timings[key]}s -> output/{fname}")
        emit({"type": "stage_done", "key": key, "name": name, "data": ctx[key], "secs": timings[key]})
    ctx["timings"] = timings
    path = write_report(ctx)
    log(f"Report written -> {path.relative_to(OUTPUT.parent)}")
    log(f"Run finished in {sum(timings.values()):.0f}s")
    emit({"type": "run_done", "report": str(path)})
    return ctx
