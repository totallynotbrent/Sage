from __future__ import annotations

import asyncio
import json

import pytest

from app.errors import ModelOutputError
from app.llm.structured import (
    _plan_from_fragments,
    parse_json,
    request_plan,
    request_questions,
    validate_plan,
    validate_questions,
)
from tests.fakes.fake_llm import FakeLLM


def test_parse_plain_json():
    assert parse_json('{"a": 1}') == {"a": 1}
    assert parse_json("[1, 2, 3]") == [1, 2, 3]





def test_parse_broken_raises():
    with pytest.raises(ModelOutputError):
        parse_json("this is not json at all")
    with pytest.raises(ModelOutputError):
        parse_json("""{"a": "unclosed""")
    with pytest.raises(ModelOutputError):
        parse_json("")


def test_validate_plan_ok():
    raw = {
        "nodes": [
            {"node_key": "k1", "title": "Prereq", "description": "d", "depends_on": []},
            {
                "node_key": "k2",
                "title": "Main",
                "description": "d2",
                "depends_on": ["k1"],
            },
        ]
    }
    plan = validate_plan(raw)
    assert plan is not None
    assert [n.node_key for n in plan.nodes] == ["k1", "k2"]




def test_validate_plan_duplicate_node_key_is_none():
    raw = {
        "nodes": [
            {"node_key": "k1", "title": "A", "depends_on": []},
            {"node_key": "k1", "title": "B", "depends_on": []},
        ]
    }
    assert validate_plan(raw) is None


def test_plan_from_fragments_rejects_duplicate_node_keys():
    raw = {
        "nodes": [
            {"node_key": "k1", "title": "A"},
            {"node_key": "k1", "title": "B"},
        ]
    }
    assert _plan_from_fragments(raw) is None


def _good_questions():
    return [
        {
            "question": "What is 2+2?",
            "options": ["3", "4", "5"],
            "correct_index": 1,
            "explanation": "2+2=4",
            "topic": "arithmetic",
            "difficulty": 1,
        },
        {
            "question": "What is water?",
            "options": ["H2O", "CO2"],
            "correct_index": 0,
        },
    ]


def test_validate_questions_ok():
    questions = validate_questions(_good_questions())
    assert questions is not None
    assert len(questions) == 2
    assert questions[0].correct_index == 1







def _session():
    return {
        "id": "s1",
        "goal": "learn x",
        "phase": "setup",
        "grounding_mode": "grounded",
    }


def test_request_plan_ok():
    llm = FakeLLM()
    llm.complete_json_responses = [
        json.dumps({"nodes": [{"node_key": "a", "title": "A", "depends_on": []}]})
    ]
    plan = asyncio.run(
        request_plan(
            llm, session=_session(), chunks=[], mastery_summary="", mode="grounded"
        )
    )
    assert plan is not None
    assert plan.nodes[0].title == "A"


def test_request_plan_corrective_retry():
    llm = FakeLLM()
    llm.complete_json_responses = [
        "not json at all",
        json.dumps({"nodes": [{"node_key": "a", "title": "A", "depends_on": []}]}),
    ]
    plan = asyncio.run(
        request_plan(
            llm, session=_session(), chunks=[], mastery_summary="", mode="grounded"
        )
    )
    assert plan is not None
    assert len(llm.calls) == 2


def test_request_plan_degrades_to_outline():
    llm = FakeLLM()
    llm.complete_json_responses = [
        json.dumps(
            {"nodes": [{"node_key": "a", "title": "First", "depends_on": ["ghost"]}]}
        ),
        json.dumps({"nodes": [{"node_key": "a", "title": "First", "depends_on": []}]}),
    ]
    plan = asyncio.run(
        request_plan(
            llm, session=_session(), chunks=[], mastery_summary="", mode="grounded"
        )
    )
    # the dangling dep is rejected and the corrective retry's clean plan wins
    assert plan is not None
    assert [n.title for n in plan.nodes] == ["First"]
    assert plan.nodes[0].depends_on == []


def test_request_plan_nothing_usable_returns_none():
    llm = FakeLLM()
    llm.complete_json_responses = ["garbage", "more garbage"]
    plan = asyncio.run(
        request_plan(
            llm, session=_session(), chunks=[], mastery_summary="", mode="grounded"
        )
    )
    assert plan is None


def test_request_questions_ok():
    llm = FakeLLM()
    llm.complete_json_responses = [json.dumps(_good_questions())]
    questions = asyncio.run(
        request_questions(
            llm, session=_session(), chunks=[], mastery_summary="", mode="grounded"
        )
    )
    assert len(questions) == 2


def test_request_questions_corrective_retry():
    llm = FakeLLM()
    llm.complete_json_responses = [
        json.dumps([{"question": "broken"}]),
        json.dumps(_good_questions()),
    ]
    questions = asyncio.run(
        request_questions(
            llm, session=_session(), chunks=[], mastery_summary="", mode="grounded"
        )
    )
    assert questions is not None and len(questions) == 2
    assert len(llm.calls) == 2


def test_request_questions_keeps_valid_questions():
    llm = FakeLLM()
    bad = _good_questions()
    bad[0]["correct_index"] = 99
    llm.complete_json_responses = [json.dumps(bad), "no"]
    questions = asyncio.run(
        request_questions(
            llm, session=_session(), chunks=[], mastery_summary="", mode="grounded"
        )
    )
    assert len(questions) == 1
    assert questions[0].question == "What is water?"


def test_request_questions_nothing_returns_empty():
    llm = FakeLLM()
    llm.complete_json_responses = ["junk", "junk"]
    questions = asyncio.run(
        request_questions(
            llm, session=_session(), chunks=[], mastery_summary="", mode="grounded"
        )
    )
    assert questions == []
