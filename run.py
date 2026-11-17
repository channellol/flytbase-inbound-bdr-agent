"""Entry point: python run.py [path/to/email.txt]

Takes the raw inbound email as its only input and runs all six stages.
"""
import sys
from pathlib import Path

from core.llm import MissingKey
from core.pipeline import run


def main() -> None:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).parent / "input" / "inbound_email.txt")
    try:
        ctx = run(path.read_text())
    except MissingKey as e:
        print(f"\n{e}\n  export GEMINI_API_KEY=...   (get one at https://aistudio.google.com/apikey)")
        sys.exit(1)
    print("\nDone. Outputs in ./output (report.md, stageN_*.json, run_log.txt)")
    print("Headline:", ctx["s6"].get("headline"))


if __name__ == "__main__":
    main()
