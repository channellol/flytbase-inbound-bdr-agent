"""Timestamped run log + event bus (the Streamlit UI subscribes to events)."""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "output"
OUTPUT.mkdir(exist_ok=True)
LOG_PATH = OUTPUT / "run_log.txt"

_listeners: list[Callable[[dict], None]] = []


def subscribe(fn: Callable[[dict], None]) -> None:
    _listeners.append(fn)


def clear_listeners() -> None:
    _listeners.clear()


def reset_log() -> None:
    LOG_PATH.write_text("")


def emit(event: dict) -> None:
    for fn in list(_listeners):
        try:
            fn(event)
        except Exception:
            pass


def log(msg: str, level: str = "info", stage: str | None = None) -> None:
    ts = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {level.upper():5} {('[' + stage + '] ') if stage else ''}{msg}"
    print(line, flush=True)
    with LOG_PATH.open("a") as f:
        f.write(line + "\n")
    emit({"type": "log", "ts": ts, "level": level, "stage": stage, "msg": msg})
