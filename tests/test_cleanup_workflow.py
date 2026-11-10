from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_the_artifact_cleanup_runs_on_a_hosted_machine():
    """It needs only gh and jq, which the hosted image has; the self-hosted runners do not all have gh."""
    workflow = (ROOT / ".github" / "workflows" / "cleanup-artifacts.yml").read_text(encoding="utf-8")
    runners = [line.split(":", 1)[1].strip() for line in workflow.splitlines() if line.strip().startswith("runs-on:")]
    assert runners == ["ubuntu-latest"]
    assert "actions: write" in workflow
    assert "workflow_dispatch:" in workflow and "schedule:" in workflow
