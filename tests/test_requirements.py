import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def requirement_lines():
    text = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    return [line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]


def test_alpaca_py_is_pinned_to_one_release():
    """Its models validate every asset Alpaca returns, so a new release can change what the nightly
    seeder accepts (the TXSE exchange stopped the 2026-10-31 run; 0.45.0 later accepted it)."""
    pins = [line for line in requirement_lines() if re.match(r"alpaca[-_]py\b", line, re.IGNORECASE)]
    assert len(pins) == 1
    assert re.fullmatch(r"alpaca-py==\d+\.\d+\.\d+", pins[0]), pins[0]
