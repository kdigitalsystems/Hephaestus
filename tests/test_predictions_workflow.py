import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "predictions.yml"


def generate_step():
    text = WORKFLOW.read_text(encoding="utf-8")
    block = text.split("      - name: Generate bounded research signals\n", 1)[1].split("\n      - name:", 1)[0]
    return block, block.split("        run: |\n", 1)[1]


def test_a_manual_run_can_override_the_stale_dashboard_guard_and_a_scheduled_one_cannot():
    text = WORKFLOW.read_text(encoding="utf-8")
    dispatch = text.split("  workflow_dispatch:\n", 1)[1].split("  schedule:", 1)[0]
    assert "allow_stale_dashboard:" in dispatch and "type: boolean" in dispatch and "default: false" in dispatch
    block, _ = generate_step()
    # The input is empty on a scheduled run, so only a person ticking the box can set the flag.
    assert "ALLOW_STALE_DASHBOARD: ${{ inputs.allow_stale_dashboard }}" in block
    assert "--allow-stale-dashboard" not in text.split("      - name: Generate bounded research signals\n", 1)[0]


@pytest.mark.skipif(not shutil.which("bash"), reason="needs bash")
@pytest.mark.parametrize(
    ("value", "expect_flag"),
    [("true", True), ("false", False), ("", False), ("TRUE", False), ("1", False)],
)
def test_the_flag_reaches_the_generator_only_for_true(tmp_path, value, expect_flag):
    """Run the workflow's own shell step with a stub generator that records its arguments."""
    _, script = generate_step()
    script = "\n".join(line[10:] if line.startswith(" " * 10) else line for line in script.splitlines())
    (tmp_path / "venv" / "bin").mkdir(parents=True)
    stub = tmp_path / "venv" / "bin" / "python"
    stub.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$@" > "$(dirname "$0")/args.txt"\n')
    stub.chmod(0o755)
    env = {**os.environ, "ALLOW_STALE_DASHBOARD": value, "HEPHAESTUS_PREDICTION_MODEL": "llama3.1:8b"}
    result = subprocess.run(["bash", "-e", "-c", script], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    args = (tmp_path / "venv" / "bin" / "args.txt").read_text().splitlines()
    assert ("--allow-stale-dashboard" in args) is expect_flag
    # Everything the scheduled run always passed is still there, in order.
    assert args[:6] == ["backend/generate_predictions.py", "--limit", "50", "--use-ollama", "--require-ollama", "--ollama-model"]
    assert args[6] == "llama3.1:8b"
    assert len(args) == (8 if expect_flag else 7)


def test_the_cli_flag_the_workflow_passes_exists():
    cli = (ROOT / "backend" / "generate_predictions.py").read_text(encoding="utf-8")
    assert re.search(r'add_argument\("--allow-stale-dashboard"', cli)
