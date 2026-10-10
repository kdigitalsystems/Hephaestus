import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = {
    "gpu_pipeline.yml": "Commit and Push Dashboard Data",
    "predictions.yml": "Commit and push research signals",
}
ATTEMPTS = 5


def load(name):
    return (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")


def steps(text):
    """Split a workflow into its '- name:' step blocks, keyed by step name."""
    blocks = re.split(r"\n(?=      - name: )", text)
    return {re.match(r"      - name: (.+)", block).group(1).strip(): block for block in blocks if block.startswith("      - name: ")}


def mint_blocks(text):
    return [block for name, block in steps(text).items() if name.startswith("Mint fresh publish token")]


def step_id(attempt):
    return "push-token" if attempt == 1 else f"push-token-{attempt}"


@pytest.mark.parametrize("workflow", WORKFLOWS)
def test_the_publish_token_is_minted_up_to_five_times(workflow):
    # api.github.com was unreachable for ~40 s on 2026-10-09; the single mint step failed the
    # job after a full day's work and nothing was published.
    blocks = mint_blocks(load(workflow))
    assert len(blocks) == ATTEMPTS
    for attempt, block in enumerate(blocks, start=1):
        assert f"id: {step_id(attempt)}\n" in block
        assert "continue-on-error: true" in block
        assert "uses: actions/create-github-app-token@v2" in block
        if attempt == 1:
            assert "\n        if:" not in block
        else:
            # Attempt N runs only when attempt N-1 ran and failed: a skipped attempt has
            # outcome 'skipped', which is what stops the chain once a mint has worked.
            assert f"if: steps.{step_id(attempt - 1)}.outcome == 'failure'" in block


@pytest.mark.parametrize("workflow", WORKFLOWS)
def test_each_retry_waits_longer_than_the_one_before(workflow):
    text = load(workflow)
    waits = []
    for name, block in steps(text).items():
        if name.startswith("Wait before minting the publish token again"):
            attempt = int(re.search(r"attempt (\d+) of", name).group(1))
            assert f"if: steps.{step_id(attempt - 1)}.outcome == 'failure'" in block
            waits.append((attempt, int(re.search(r"run: sleep (\d+)", block).group(1))))
    assert [attempt for attempt, _ in waits] == [2, 3, 4, 5]
    seconds = [wait for _, wait in waits]
    assert seconds == sorted(seconds) and len(set(seconds)) == len(seconds)
    # The jobs have timeout headroom for this (gpu: 240 min against ~210 of budgets).
    assert 600 <= sum(seconds) <= 1200


@pytest.mark.parametrize(("workflow", "push_step"), WORKFLOWS.items())
def test_the_push_uses_whichever_token_worked_and_refuses_an_empty_one(workflow, push_step):
    block = steps(load(workflow))[push_step]
    expected = " || ".join(f"steps.{step_id(attempt)}.outputs.token" for attempt in range(1, ATTEMPTS + 1))
    assert f"PUSH_TOKEN: ${{{{ {expected} }}}}" in block
    guard = block.index('if [ -z "$PUSH_TOKEN" ]')
    # The guard comes before any git command touches the credential.
    assert guard < block.index("git config")
    assert "exit 1" in block[guard:block.index("fi", guard) + 2]
    # A longer outage than a minute must not cost the day's push either.
    assert 'PUSH_ATTEMPTS: "5"' in block and 'PUSH_BACKOFF_SECONDS: "45"' in block


def test_both_workflows_use_the_same_retry_chain():
    def chain(workflow):
        text = load(workflow)
        start = text.index("      - name: Mint fresh publish token\n")
        end = text.index(f"      - name: {WORKFLOWS[workflow]}\n")
        return text[start:end]

    assert chain("gpu_pipeline.yml") == chain("predictions.yml")
