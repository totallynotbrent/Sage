from __future__ import annotations

import asyncio
import json

import pytest
from pydantic import ValidationError

from app.errors import ModelOutputError
from app.llm.structured_outputs import parse_exact_json, request_output, validate_output
from app.models import ChatOutputDraft, MermaidOutputDraft, StructuredOutputRequest
from tests.fakes.fake_llm import FakeLLM


def test_parse_exact_json_accepts_one_fence():
    assert parse_exact_json('```json\n{"content":"ok"}\n```') == {"content": "ok"}


def test_parse_exact_json_rejects_trailing_data():
    with pytest.raises(ModelOutputError):
        parse_exact_json('{"content":"ok"} trailing')


def test_validate_output_rejects_coercion_and_extra_fields():
    with pytest.raises(ValidationError):
        validate_output({"content": 1}, "chat")
    with pytest.raises(ValidationError):
        validate_output({"content": "ok", "extra": True}, "chat")


def test_validate_quiz_rejects_duplicate_options():
    with pytest.raises(ValueError, match="duplicate_options"):
        validate_output(
            {
                "questions": [
                    {
                        "question": "Q",
                        "options": ["same", "same"],
                        "correct_index": 0,
                    }
                ]
            },
            "quiz",
        )


def test_request_output_repairs_once():
    llm = FakeLLM()
    llm.complete_json_responses = [
        "not json",
        json.dumps({"content": "repaired"}),
    ]
    request = StructuredOutputRequest(output_kind="chat", prompt="Explain this")
    result, attempts, diagram_type = asyncio.run(request_output(llm, [], request))
    assert isinstance(result, ChatOutputDraft)
    assert result.content == "repaired"
    assert attempts == 2
    assert diagram_type is None
    assert len(llm.calls) == 2


def test_request_output_exhaustion_is_retryable():
    llm = FakeLLM()
    llm.complete_json_responses = ["bad", "still bad"]
    request = StructuredOutputRequest(output_kind="chat", prompt="Explain this")
    with pytest.raises(ModelOutputError) as caught:
        asyncio.run(request_output(llm, [], request))
    assert caught.value.retryable is True


def test_request_output_exhaustion_preserves_invalid_json_code():
    llm = FakeLLM()
    llm.complete_json_responses = ["not json", "still not json"]
    request = StructuredOutputRequest(output_kind="chat", prompt="Explain this")
    with pytest.raises(ModelOutputError) as caught:
        asyncio.run(request_output(llm, [], request))
    assert caught.value.detail["issue_codes"] == ["invalid_json"]
    assert caught.value.retryable is True


def test_request_output_exhaustion_preserves_trailing_data_code():
    llm = FakeLLM()
    bad = json.dumps({"content": "ok"}) + " trailing"
    llm.complete_json_responses = [bad, bad]
    request = StructuredOutputRequest(output_kind="chat", prompt="Explain this")
    with pytest.raises(ModelOutputError) as caught:
        asyncio.run(request_output(llm, [], request))
    assert caught.value.detail["issue_codes"] == ["trailing_data"]


def test_request_output_exhaustion_empty_quiz_option_code():
    llm = FakeLLM()
    bad = json.dumps(
        {"questions": [{"question": "Q", "options": ["a", ""], "correct_index": 0}]}
    )
    llm.complete_json_responses = [bad, bad]
    request = StructuredOutputRequest(output_kind="quiz", prompt="Explain this")
    with pytest.raises(ModelOutputError) as caught:
        asyncio.run(request_output(llm, [], request))
    assert caught.value.detail["issue_codes"] == ["empty_text"]


def test_request_output_exhaustion_duplicate_quiz_option_code():
    llm = FakeLLM()
    bad = json.dumps(
        {"questions": [{"question": "Q", "options": ["a", "a"], "correct_index": 0}]}
    )
    llm.complete_json_responses = [bad, bad]
    request = StructuredOutputRequest(output_kind="quiz", prompt="Explain this")
    with pytest.raises(ModelOutputError) as caught:
        asyncio.run(request_output(llm, [], request))
    assert caught.value.detail["issue_codes"] == ["duplicate_options"]


def _mermaid_validator(responses):
    async def validator(draft):
        result = responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    return validator


def _mermaid_request():
    return StructuredOutputRequest(output_kind="mermaid", prompt="Draw it")


def test_request_output_repairs_mermaid_semantics():
    llm = FakeLLM()
    llm.complete_json_responses = [
        json.dumps({"title": "t", "source": "graph TD\nA"}),
        json.dumps({"title": "t", "source": "graph TD\nA-->B"}),
    ]
    validator = _mermaid_validator(
        [
            ModelOutputError("invalid", detail={"issue_codes": ["mermaid_invalid"]}),
            "flowchart",
        ]
    )
    result, attempts, diagram_type = asyncio.run(
        request_output(llm, [], _mermaid_request(), semantic_validator=validator)
    )
    assert isinstance(result, MermaidOutputDraft)
    assert attempts == 2
    assert diagram_type == "flowchart"
    assert len(llm.calls) == 2


def test_request_output_mermaid_exhaustion_is_retryable():
    llm = FakeLLM()
    source = json.dumps({"title": "t", "source": "graph TD\nA"})
    llm.complete_json_responses = [source, source]
    validator = _mermaid_validator(
        [
            ModelOutputError("invalid", detail={"issue_codes": ["mermaid_invalid"]}),
            ModelOutputError("invalid", detail={"issue_codes": ["mermaid_invalid"]}),
        ]
    )
    with pytest.raises(ModelOutputError) as caught:
        asyncio.run(
            request_output(llm, [], _mermaid_request(), semantic_validator=validator)
        )
    assert caught.value.retryable is True
    assert caught.value.detail["issue_codes"] == ["mermaid_invalid"]


def test_request_output_propagates_mermaid_validator_unavailable():
    llm = FakeLLM()
    llm.complete_json_responses = [
        json.dumps({"title": "t", "source": "graph TD\nA-->B"})
    ]
    validator = _mermaid_validator(
        [
            ModelOutputError(
                "unavailable", detail={"issue_codes": ["mermaid_validator_unavailable"]}
            )
        ]
    )
    with pytest.raises(ModelOutputError) as caught:
        asyncio.run(
            request_output(llm, [], _mermaid_request(), semantic_validator=validator)
        )
    assert caught.value.detail["issue_codes"] == ["mermaid_validator_unavailable"]
    assert len(llm.calls) == 1
