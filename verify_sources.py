"""Re-fetch every research source live (no cache) and confirm each fact's quote is still on the page.
Usage: python verify_sources.py  -> writes output/source_check.json"""
import json
from pathlib import Path
from core.web import fetch, quote_in_text

facts = json.loads(Path("output/stage2_research.json").read_text())["facts"]
pages, rows = {}, []
for f in facts:
    u = f["source_url"]
    if u not in pages:
        pages[u] = fetch(u, use_cache=False)
    p = pages[u]
    ok = p["ok"] and quote_in_text(f["evidence"], p["text"])
    rows.append({"id": f["id"], "url": u, "live": p["ok"], "quote_found": ok})
    print(f"{f['id']:4} {'OK ' if ok else 'BAD'} {u}")
Path("output/source_check.json").write_text(json.dumps(rows, indent=2))
print(f"{sum(r['quote_found'] for r in rows)}/{len(rows)} facts re-verified live")
