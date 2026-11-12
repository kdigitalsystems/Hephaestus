import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import parser


def test_extract_dependencies_validates_model_payload(monkeypatch):
    monkeypatch.setattr(parser, "ollama_chat",lambda **_kwargs: {
        "message": {"content": """{
            "dependencies": [{
                "source_company": "Supplier Inc.",
                "target_company": "Customer Inc.",
                "source_ticker": "SUP",
                "target_ticker": "CUS",
                "dependency_type": "Components",
                "product": "Control modules",
                "evidence_excerpt": "Supplier Inc. provides control modules to Customer Inc.",
                "evidence_source_url": "https://example.com/filing",
                "confidence_score": 0.9
            }]
        }"""}
    })

    result = parser.extract_dependencies("source text")

    assert result["dependencies"][0]["confidence_score"] == 0.9
    assert result["dependencies"][0]["evidence_source_url"] == "https://example.com/filing"


def test_extraction_model_comes_from_the_environment_and_failures_are_reported(monkeypatch):
    calls = []

    def failing_chat(**kwargs):
        calls.append(kwargs["model"])
        raise RuntimeError("model 'x' not found (status code: 404)")

    monkeypatch.setattr(parser, "ollama_chat",failing_chat)
    monkeypatch.setattr(parser, "DEFAULT_EXTRACTION_MODEL", "llama3.1:8b")

    result = parser.extract_dependencies("source text")

    assert calls == ["llama3.1:8b"]
    assert result["dependencies"] == []
    assert "not found" in result["error"]


def test_extract_dependencies_rejects_unbounded_or_missing_evidence(monkeypatch):
    monkeypatch.setattr(parser, "ollama_chat",lambda **_kwargs: {
        "message": {"content": """{
            "dependencies": [{
                "source_company": "Supplier Inc.",
                "target_company": "Customer Inc.",
                "dependency_type": "Components",
                "product": "Control modules",
                "evidence_excerpt": "",
                "confidence_score": 9
            }]
        }"""}
    })

    assert parser.extract_dependencies("source text") == {"dependencies": []}


def test_extract_dependencies_keeps_valid_items_when_one_is_malformed(monkeypatch):
    monkeypatch.setattr(parser, "ollama_chat",lambda **_kwargs: {
        "message": {"content": """{
            "dependencies": [
                {
                    "source_company": "Supplier Inc.",
                    "target_company": "Customer Inc.",
                    "dependency_type": "Components",
                    "product": "Control modules",
                    "evidence_excerpt": "Supplier Inc. provides control modules to Customer Inc.",
                    "confidence_score": 0.9
                },
                {
                    "source_company": "Other Supplier",
                    "target_company": "Customer Inc.",
                    "dependency_type": "Logistics",
                    "product": "Freight",
                    "evidence_excerpt": "Other Supplier ships freight for Customer Inc. every week.",
                    "confidence_score": 9
                }
            ]
        }"""}
    })

    result = parser.extract_dependencies("source text")

    assert [item["source_company"] for item in result["dependencies"]] == ["Supplier Inc."]


def test_extract_dependencies_unescapes_double_encoded_model_text(monkeypatch):
    monkeypatch.setattr(parser, "ollama_chat",lambda **_kwargs: {
        "message": {"content": r"""{
            "dependencies": [{
                "source_company": "LG Display",
                "target_company": "Apple Inc.",
                "dependency_type": "Display Panels",
                "product": "27-inch panels",
                "evidence_excerpt": "LG panels are used in Apple\\u2019s 2009 27-inch iMac and GM\\'s\\nnewer displays.",
                "confidence_score": 0.8
            }]
        }"""}
    })

    result = parser.extract_dependencies("source text")

    assert result["dependencies"][0]["evidence_excerpt"] == "LG panels are used in Apple’s 2009 27-inch iMac and GM's newer displays."


def test_extract_dependencies_rejects_url_with_trailing_payload(monkeypatch):
    monkeypatch.setattr(parser, "ollama_chat",lambda **_kwargs: {
        "message": {"content": """{
            "dependencies": [{
                "source_company": "Supplier Inc.",
                "target_company": "Customer Inc.",
                "dependency_type": "Components",
                "product": "Control modules",
                "evidence_excerpt": "Supplier Inc. provides control modules to Customer Inc.",
                "evidence_source_url": "https://x.com\\" onload=alert(1)",
                "confidence_score": 0.9
            }]
        }"""}
    })

    assert parser.extract_dependencies("source text") == {"dependencies": []}


def test_extraction_requests_are_bounded(monkeypatch):
    """One unbounded generation stalled a run for an hour; every request must carry
    a token cap, and the client must enforce a wall-clock timeout."""
    seen = {}

    class FakeClient:
        def __init__(self, timeout=None, **_kwargs):
            seen["timeout"] = timeout

        def chat(self, **kwargs):
            seen["options"] = kwargs.get("options")
            return {"message": {"content": '{"dependencies": []}'}}

    monkeypatch.setattr(parser.ollama, "Client", FakeClient)

    assert parser.extract_dependencies("source text") == {"dependencies": []}
    assert seen["timeout"] == parser.EXTRACTION_TIMEOUT_SECONDS > 0
    assert 0 < seen["options"]["num_predict"] <= 4096


def test_extraction_schema_stays_grammar_safe():
    """Ollama compiles the schema to a llama.cpp grammar with a tiny regex subset.

    A `pattern` on the URL field made every extraction fail with "failed to parse
    grammar"; URL validation lives in a Python validator instead.
    """
    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                assert key != "pattern", "regex patterns must not reach the Ollama grammar"
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(parser.ExtractionResult.model_json_schema())


def test_extract_dependencies_rejects_malformed_source_url(monkeypatch):
    monkeypatch.setattr(parser, "ollama_chat",lambda **_kwargs: {
        "message": {"content": """{
            "dependencies": [{
                "source_company": "Supplier Inc.",
                "target_company": "Customer Inc.",
                "dependency_type": "Components",
                "product": "Control modules",
                "evidence_excerpt": "Supplier Inc. provides control modules to Customer Inc.",
                "evidence_source_url": "javascript:alert(1)",
                "confidence_score": 0.9
            }]
        }"""}
    })

    assert parser.extract_dependencies("source text") == {"dependencies": []}


def test_a_response_without_a_dependencies_list_counts_as_a_failure(monkeypatch):
    """Without an error the caller treated it as successful research: cooldown stamped,
    failure uncounted, and the "every extraction failed" alarm suppressed."""
    monkeypatch.setattr(parser, "ollama_chat", lambda **_kwargs: {"message": {"content": '{"results": []}'}})

    result = parser.extract_dependencies("source text")

    assert result["dependencies"] == []
    assert "dependencies list" in result["error"]


def dependency_json(supplier, customer="Customer Inc."):
    return (
        '{"source_company": "%s", "target_company": "%s", "dependency_type": "Components", '
        '"product": "Control modules", '
        '"evidence_excerpt": "%s provides control modules to %s.", "confidence_score": 0.9}'
        % (supplier, customer, supplier, customer)
    )


def chat_returning(*answers):
    """A fake model that gives the next canned answer on every call, and counts the calls."""
    calls = []

    def chat(**_kwargs):
        calls.append(1)
        return {"message": {"content": answers[min(len(calls), len(answers)) - 1]}}

    chat.calls = calls
    return chat


# Output the way a 2,048-token cap leaves it: two objects complete, the third cut off
# inside a string.
CUT_OFF_ANSWER = (
    '{"dependencies": [' + dependency_json("Alpha Corp") + ", " + dependency_json("Beta Corp")
    + ', {"source_company": "Gamma Corp", "target_company": "Customer Inc.", "dependency_type": "Comp'
)
# One string that never ends: nothing complete to keep.
RUNAWAY_ANSWER = '{"dependencies": [{"source_company": "Gamma Corp", "evidence_excerpt": "' + "the " * 400


def test_salvage_keeps_the_objects_written_before_the_cut():
    kept = parser.salvage_dependencies(CUT_OFF_ANSWER)

    assert [item["source_company"] for item in kept] == ["Alpha Corp", "Beta Corp"]


def test_salvage_keeps_intact_objects_even_when_a_comma_between_them_is_missing():
    broken = '{"dependencies": [' + dependency_json("Alpha Corp") + " " + dependency_json("Beta Corp") + "]}"

    assert [item["source_company"] for item in parser.salvage_dependencies(broken)] == ["Alpha Corp", "Beta Corp"]


def test_salvage_stops_at_the_first_damaged_object():
    # "Expecting ',' delimiter" inside the second object: it and what follows are dropped.
    damaged = dependency_json("Beta Corp").replace('"Components", ', '"Components" ', 1)
    broken = '{"dependencies": [' + dependency_json("Alpha Corp") + ", " + damaged + ", " + dependency_json("Gamma Corp") + "]}"

    assert [item["source_company"] for item in parser.salvage_dependencies(broken)] == ["Alpha Corp"]


def test_salvage_is_not_fooled_by_brackets_and_braces_inside_strings():
    tricky = '{"dependencies": [{"source_company": "A] {B", "evidence_excerpt": "x }, { y ] z"}, {"source_company": "cut'

    assert parser.salvage_dependencies(tricky) == [{"source_company": "A] {B", "evidence_excerpt": "x }, { y ] z"}]


def test_salvage_collapses_repeats_from_a_looping_model():
    looping = '{"dependencies": [' + ", ".join([dependency_json("Alpha Corp")] * 6) + ", " + dependency_json("Beta Corp") + ", {"

    assert [item["source_company"] for item in parser.salvage_dependencies(looping)] == ["Alpha Corp", "Beta Corp"]


@pytest.mark.parametrize("raw", [RUNAWAY_ANSWER, "", "not json at all", '{"results": [{"a": 1}]}', None, 42, '{"dependencies": ['])
def test_salvage_finds_nothing_in_hopeless_output(raw):
    assert parser.salvage_dependencies(raw) == []


def test_a_cut_off_answer_keeps_its_complete_dependencies_and_is_not_a_failure(monkeypatch):
    """The company was researched; before, one bad tail failed it and it came back every night."""
    chat = chat_returning(CUT_OFF_ANSWER)
    monkeypatch.setattr(parser, "ollama_chat", chat)

    result = parser.extract_dependencies("source text")

    assert [dep["source_company"] for dep in result["dependencies"]] == ["Alpha Corp", "Beta Corp"]
    assert "error" not in result
    assert len(chat.calls) == 1


def test_salvaged_dependencies_are_still_validated(monkeypatch):
    bad = '{"source_company": "Bad Corp", "target_company": "Customer Inc.", "dependency_type": "Components", "product": "Parts", "evidence_excerpt": "", "confidence_score": 0.9}'
    answer = '{"dependencies": [' + bad + ", " + dependency_json("Alpha Corp") + ", {"
    monkeypatch.setattr(parser, "ollama_chat", chat_returning(answer))

    result = parser.extract_dependencies("source text")

    assert [dep["source_company"] for dep in result["dependencies"]] == ["Alpha Corp"]


def test_unreadable_output_is_asked_for_again_and_the_second_answer_is_used(monkeypatch):
    chat = chat_returning(RUNAWAY_ANSWER, '{"dependencies": [' + dependency_json("Alpha Corp") + "]}")
    monkeypatch.setattr(parser, "ollama_chat", chat)
    monkeypatch.setattr(parser, "EXTRACTION_ATTEMPTS", 2)

    result = parser.extract_dependencies("source text")

    assert [dep["source_company"] for dep in result["dependencies"]] == ["Alpha Corp"]
    assert "error" not in result
    assert len(chat.calls) == 2


def test_output_that_stays_unreadable_is_a_failure_after_the_attempts_run_out(monkeypatch):
    chat = chat_returning(RUNAWAY_ANSWER)
    monkeypatch.setattr(parser, "ollama_chat", chat)
    monkeypatch.setattr(parser, "EXTRACTION_ATTEMPTS", 2)

    result = parser.extract_dependencies("source text")

    assert result["dependencies"] == []
    assert "Unterminated string" in result["error"]
    assert len(chat.calls) == 2


def test_one_attempt_means_no_second_request(monkeypatch):
    chat = chat_returning(RUNAWAY_ANSWER)
    monkeypatch.setattr(parser, "ollama_chat", chat)
    monkeypatch.setattr(parser, "EXTRACTION_ATTEMPTS", 1)

    result = parser.extract_dependencies("source text")

    assert "error" in result
    assert len(chat.calls) == 1


def test_a_failing_server_is_not_asked_again(monkeypatch):
    """A missing model or a timeout would only fail again, and a timeout costs minutes."""
    calls = []

    def failing_chat(**_kwargs):
        calls.append(1)
        raise TimeoutError("timed out")

    monkeypatch.setattr(parser, "ollama_chat", failing_chat)
    monkeypatch.setattr(parser, "EXTRACTION_ATTEMPTS", 3)

    result = parser.extract_dependencies("source text")

    assert result == {"dependencies": [], "error": "timed out"}
    assert len(calls) == 1


@pytest.mark.parametrize("configured, expected", [("3", 3), ("1", 1), ("0", 1), ("-4", 1)])
def test_the_attempt_count_comes_from_the_environment_and_is_never_below_one(monkeypatch, configured, expected):
    """With zero attempts the loop would never run and an empty error would read as success."""
    import importlib

    monkeypatch.setenv("HEPHAESTUS_EXTRACTION_ATTEMPTS", configured)
    try:
        assert importlib.reload(parser).EXTRACTION_ATTEMPTS == expected
    finally:
        monkeypatch.delenv("HEPHAESTUS_EXTRACTION_ATTEMPTS")
        importlib.reload(parser)
