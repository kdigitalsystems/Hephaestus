import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "gpu_pipeline.yml"
SCRIPT = ROOT / "scripts" / "published_today.sh"


def test_the_retry_trigger_is_gated_on_todays_publish():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "- cron: '23 2 * * *'" in workflow and "- cron: '23 14 * * *'" in workflow
    assert "workflow_dispatch" in workflow
    freshness = workflow.split("\n  freshness:", 1)[1].split("\n  run-ai-discovery:", 1)[0]
    # The decision runs on a GitHub-hosted machine, so it never waits on the GPU runner.
    assert "runs-on: ubuntu-latest" in freshness
    assert "bash scripts/published_today.sh" in freshness
    gpu = workflow.split("\n  run-ai-discovery:", 1)[1]
    assert "needs: freshness" in gpu
    assert "if: needs.freshness.outputs.publish == 'true'" in gpu


@pytest.mark.skipif(not (shutil.which("bash") and shutil.which("jq")), reason="needs bash and jq")
@pytest.mark.parametrize(
    ("event", "status", "expected"),
    [
        ("schedule", "today", "false"),
        ("schedule", "yesterday", "true"),
        ("schedule", "unreadable", "true"),  # cannot tell: run rather than risk a missed day
        ("workflow_dispatch", "today", "true"),  # manual runs always publish
    ],
)
def test_published_today_decides_whether_a_run_is_needed(tmp_path, event, status, expected):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    shim = tmp_path / "bin"
    shim.mkdir()
    fake_gh = shim / "gh"
    if status == "unreadable":
        fake_gh.write_text("#!/usr/bin/env bash\necho 'HTTP 404' >&2\nexit 1\n")
    else:
        stamp = f"{today}T09:14:51+00:00" if status == "today" else "2000-01-01T09:14:51+00:00"
        fake_gh.write_text("#!/usr/bin/env bash\ncat <<'JSON'\n" + json.dumps({"data_as_of": stamp}) + "\nJSON\n")
    fake_gh.chmod(0o755)
    output = tmp_path / "github_output"
    env = {
        **os.environ,
        "PATH": f"{shim}{os.pathsep}{os.environ.get('PATH', '')}",
        "EVENT_NAME": event,
        "REPO": "owner/repo",
        "GITHUB_OUTPUT": str(output),
    }

    result = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=30)

    assert result.returncode == 0, result.stderr
    assert output.read_text().strip() == f"publish={expected}"
