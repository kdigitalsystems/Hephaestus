import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from validate_predictions import validate_predictions
from generate_predictions import load_prediction_history


def test_published_prediction_export_is_bounded_and_research_only():
    payload = json.loads((ROOT / "docs" / "predictions.json").read_text(encoding="utf-8"))

    validate_predictions(payload)


def test_prediction_workflow_is_separate_from_daily_graph_updates():
    workflow = (ROOT / ".github" / "workflows" / "predictions.yml").read_text(encoding="utf-8")
    daily_workflow = (ROOT / ".github" / "workflows" / "gpu_pipeline.yml").read_text(encoding="utf-8")

    assert "runs-on: [self-hosted, nvidia-gpu]" in workflow
    assert "venv/bin/pip install pytest" in workflow
    assert "--use-ollama" in workflow
    assert "--require-ollama" in workflow
    assert "group: hephaestus-main-publisher" in workflow
    assert "group: hephaestus-main-publisher" in daily_workflow
    assert "git reset --hard origin/main" in workflow
    assert "git reset --hard origin/main" in daily_workflow
    assert "git pull --rebase origin main" in workflow
    assert "git pull --rebase origin main" in daily_workflow
    assert "docs/predictions.json docs/prediction_history.json" in workflow
    assert "generate_predictions.py" not in daily_workflow


def test_invalid_prediction_history_is_not_silently_replaced(tmp_path):
    history_path = tmp_path / "history.json"
    history_path.write_text("{broken", encoding="utf-8")

    try:
        load_prediction_history(history_path)
    except ValueError as exc:
        assert "unreadable or invalid" in str(exc)
    else:
        raise AssertionError("invalid history must stop prediction generation")
