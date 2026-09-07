from __future__ import annotations

import asyncio
import json

import pytest

from app.errors import ModelOutputError, NotFoundError, ProviderError
from app.services.learning import LearningService
from app.services.files import FileService
from app.services.plans import PlansService
from app.services.sessions import SessionService
from app.services.teach import TeachService
from tests.fakes.fake_llm import FakeLLM


def _create_session(conn, settings, goal="learn algebra"):
    return SessionService(conn, settings).create(goal, [])


def _good_plan():
    return {
        "nodes": [
            {"node_key": "k1", "title": "Basics", "description": "d1", "depends_on": []},
            {"node_key": "k2", "title": "Advanced", "description": "d2", "depends_on": ["k1"]},
            {"node_key": "k3", "title": "Expert", "description": "d3", "depends_on": ["k2"]},
        ]
    }


def _planned_session(conn, settings, fake_llm):
    session = _create_session(conn, settings)
    fake_llm.complete_json_responses = [json.dumps(_good_plan())]
    result = asyncio.run(PlansService(conn, settings).generate_plan(session.id, fake_llm))
    return session, result["plan"]


def test_generate_plan_persists_and_sets_phase(conn, settings, fake_llm):
    session, plan = _planned_session(conn, settings, fake_llm)
    assert len(plan) == 3
    for index, node in enumerate(plan):
        assert node["status"] == "pending"
        assert node["position"] == index
    assert plan[0]["node_key"] == "k1"
    assert plan[0]["depends_on"] == []
    assert plan[1]["depends_on"] == ["k1"]
    assert plan[0]["children"] == ["k2"]
    assert plan[1]["children"] == ["k3"]
    stored = SessionService(conn, settings).get(session.id)
    assert stored.phase == "plan"


def test_generate_plan_idempotent(conn, settings, fake_llm):
    session, first_plan = _planned_session(conn, settings, fake_llm)
    service = PlansService(conn, settings)
    second = asyncio.run(service.generate_plan(session.id, fake_llm))
    assert [n["id"] for n in second["plan"]] == [n["id"] for n in first_plan]
    assert len(fake_llm.calls) == 1


def test_generate_plan_strict_passes_pdf_outline(conn, settings, fake_llm):
    pymupdf = pytest.importorskip("pymupdf")
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "Intro body")
    page.insert_text((72, 120), "Math body")
    document.set_toc([[1, "Introduction", 1], [1, "Integrals", 1], [1, "Derivatives", 1]])
    pdf = FileService(conn, settings).save_upload(
        filename="textbook.pdf",
        content=document.tobytes(),
        content_type="application/pdf",
    )
    assert pdf.outline

    session = SessionService(conn, settings).create(
        "learn calculus", [pdf.id], grounding_mode="strict"
    )
    fake_llm.complete_json_responses = [json.dumps(_good_plan())]
    asyncio.run(PlansService(conn, settings).generate_plan(session.id, fake_llm))

    prompt = " ".join(
        str(m.get("content", ""))
        for call in fake_llm.calls
        for m in call.get("messages", [])
    )
    assert "Document sections" in prompt
    assert "Introduction" in prompt
    assert "Integrals" in prompt
    assert "Derivatives" in prompt


def test_generate_plan_grounded_ignores_outline(conn, settings, fake_llm):
    pymupdf = pytest.importorskip("pymupdf")
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "Intro body")
    document.set_toc([[1, "Introduction", 1]])
    pdf = FileService(conn, settings).save_upload(
        filename="textbook.pdf",
        content=document.tobytes(),
        content_type="application/pdf",
    )

    session = SessionService(conn, settings).create(
        "learn calculus", [pdf.id], grounding_mode="grounded"
    )
    fake_llm.complete_json_responses = [json.dumps(_good_plan())]
    asyncio.run(PlansService(conn, settings).generate_plan(session.id, fake_llm))

    prompt = " ".join(
        str(m.get("content", ""))
        for call in fake_llm.calls
        for m in call.get("messages", [])
    )
    assert "Document sections" not in prompt


def test_generate_plan_garbage_raises(conn, settings, fake_llm):
    session = _create_session(conn, settings)
    fake_llm.complete_json_responses = ["garbage", "garbage"]
    with pytest.raises(ModelOutputError) as excinfo:
        asyncio.run(PlansService(conn, settings).generate_plan(session.id, fake_llm))
    assert excinfo.value.status_code == 422
    assert excinfo.value.retryable is True


def test_generate_plan_duplicate_node_key_raises_422(conn, settings, fake_llm):
    session = _create_session(conn, settings)
    dup = json.dumps(
        {
            "nodes": [
                {"node_key": "k1", "title": "A", "depends_on": []},
                {"node_key": "k1", "title": "B", "depends_on": []},
            ]
        }
    )
    fake_llm.complete_json_responses = [dup, dup]
    with pytest.raises(ModelOutputError) as excinfo:
        asyncio.run(PlansService(conn, settings).generate_plan(session.id, fake_llm))
    assert excinfo.value.status_code == 422
    assert excinfo.value.retryable is True
    stored = conn.execute(
        "SELECT COUNT(*) AS n FROM plan_nodes WHERE session_id = ?", (session.id,)
    ).fetchone()["n"]
    assert stored == 0


def test_generate_plan_missing_session(conn, settings, fake_llm):
    with pytest.raises(NotFoundError):
        asyncio.run(PlansService(conn, settings).generate_plan("nope", fake_llm))


def test_generate_plan_provider_failure_raises_502(conn, settings):
    session = _create_session(conn, settings)

    class FailingFakeLLM(FakeLLM):
        async def complete_json(self, messages, *, max_tokens=1200, temperature=0.1):
            return (None, "upstream: The model endpoint returned status 500.")

    with pytest.raises(ProviderError) as excinfo:
        asyncio.run(PlansService(conn, settings).generate_plan(session.id, FailingFakeLLM()))
    assert excinfo.value.code == "upstream"
    assert excinfo.value.status_code == 502


def test_approve_starts_teaching(conn, settings, fake_llm):
    session, plan = _planned_session(conn, settings, fake_llm)
    result = PlansService(conn, settings).approve(session.id)
    assert result["session"]["phase"] == "teach"
    assert result["session"]["current_node_id"] == plan[0]["id"]
    assert result["session"]["nodes_since_check"] == 0
    assert result["plan"][0]["status"] == "current"
    assert result["plan"][1]["status"] == "pending"


def test_approve_without_plan_raises(conn, settings):
    session = _create_session(conn, settings)
    with pytest.raises(ValueError):
        PlansService(conn, settings).approve(session.id)


def test_reorder_positions(conn, settings, fake_llm):
    session, plan = _planned_session(conn, settings, fake_llm)
    result = PlansService(conn, settings).reorder(session.id, ["k3", "k1", "k2"])
    keys = [n["node_key"] for n in result["plan"]]
    assert keys == ["k3", "k1", "k2"]
    positions = [n["position"] for n in result["plan"]]
    assert positions == [0, 1, 2]


def test_reorder_bad_keys_raise(conn, settings, fake_llm):
    session, plan = _planned_session(conn, settings, fake_llm)
    service = PlansService(conn, settings)
    with pytest.raises(ValueError):
        service.reorder(session.id, ["k1", "k2"])
    with pytest.raises(ValueError):
        service.reorder(session.id, ["k1", "k2", "ghost"])


def test_skip_non_current_node_keeps_phase(conn, settings, fake_llm):
    session, plan = _planned_session(conn, settings, fake_llm)
    result = PlansService(conn, settings).skip_node(session.id, "k1")
    skipped = next(n for n in result["plan"] if n["node_key"] == "k1")
    assert skipped["status"] == "skipped"
    assert result["session"]["phase"] == "plan"


def test_skip_current_node_advances(conn, settings, fake_llm):
    session, plan = _planned_session(conn, settings, fake_llm)
    PlansService(conn, settings).approve(session.id)
    result = PlansService(conn, settings).skip_node(session.id, "k1")
    k1 = next(n for n in result["plan"] if n["node_key"] == "k1")
    k2 = next(n for n in result["plan"] if n["node_key"] == "k2")
    assert k1["status"] == "skipped"
    assert k2["status"] == "current"
    assert result["session"]["current_node_id"] == k2["id"]
    assert result["session"]["phase"] == "teach"


def test_skip_current_node_during_check_clears_current(conn, settings, fake_llm):
    session, plan = _planned_session(conn, settings, fake_llm)
    PlansService(conn, settings).approve(session.id)
    fake_llm.complete_json_responses = [
        json.dumps(
            [
                {
                    "question": "Check Q?",
                    "options": ["x", "y"],
                    "correct_index": 0,
                    "explanation": "e",
                    "topic": None,
                    "difficulty": 3,
                }
            ]
        )
    ]
    check = asyncio.run(LearningService(conn, settings).generate_check(session.id, fake_llm))
    assert check["session"]["phase"] == "check"
    result = PlansService(conn, settings).skip_node(session.id, "k1")
    k1 = next(n for n in result["plan"] if n["node_key"] == "k1")
    k2 = next(n for n in result["plan"] if n["node_key"] == "k2")
    assert k1["status"] == "skipped"
    assert k2["status"] == "pending"
    assert result["session"]["phase"] == "check"
    assert result["session"]["current_node_id"] is None


def test_expand_appends_child_nodes(conn, settings, fake_llm):
    session, plan = _planned_session(conn, settings, fake_llm)
    fake_llm.complete_json_responses = [
        json.dumps(
            {
                "nodes": [
                    {"node_key": "k1a", "title": "Sub-A", "description": "s", "depends_on": []},
                    {"node_key": "k1b", "title": "Sub-B", "description": "s", "depends_on": []},
                ]
            }
        )
    ]
    result = asyncio.run(
        PlansService(conn, settings).expand(session.id, "k1", "go deeper", fake_llm)
    )
    assert len(result["plan"]) == 5
    new_a = next(n for n in result["plan"] if n["node_key"] == "k1a")
    new_b = next(n for n in result["plan"] if n["node_key"] == "k1b")
    assert new_a["depends_on"] == ["k1"]
    assert new_b["depends_on"] == ["k1"]
    assert new_a["position"] == 3
    parent = next(n for n in result["plan"] if n["node_key"] == "k1")
    assert parent["children"] == ["k2", "k1a", "k1b"]


def test_expand_unknown_node_404(conn, settings, fake_llm):
    session, plan = _planned_session(conn, settings, fake_llm)
    with pytest.raises(NotFoundError):
        asyncio.run(PlansService(conn, settings).expand(session.id, "ghost", None, fake_llm))


def test_expand_colliding_key_raises(conn, settings, fake_llm):
    session, plan = _planned_session(conn, settings, fake_llm)
    fake_llm.complete_json_responses = [
        json.dumps({"nodes": [{"node_key": "k1", "title": "Dup", "depends_on": []}]})
    ]
    with pytest.raises(ValueError):
        asyncio.run(PlansService(conn, settings).expand(session.id, "k2", None, fake_llm))


def test_regenerate_resets_state(conn, settings, fake_llm):
    session, plan = _planned_session(conn, settings, fake_llm)
    PlansService(conn, settings).approve(session.id)
    TeachService(conn, settings).advance(session.id)
    assert SessionService(conn, settings).get(session.id).nodes_since_check == 1
    fake_llm.complete_json_responses = [json.dumps(_good_plan())]
    result = asyncio.run(PlansService(conn, settings).regenerate(session.id, fake_llm))
    assert result["session"]["phase"] == "plan"
    assert result["session"]["current_node_id"] is None
    assert result["session"]["nodes_since_check"] == 0
    assert all(n["status"] == "pending" for n in result["plan"])


def test_select_node_sets_current(conn, settings, fake_llm):
    session, plan = _planned_session(conn, settings, fake_llm)
    result = PlansService(conn, settings).select_node(session.id, "k2")
    assert result["session"]["phase"] == "teach"
    k2 = next(n for n in result["plan"] if n["node_key"] == "k2")
    assert result["session"]["current_node_id"] == k2["id"]
    assert k2["status"] == "current"


def test_select_node_without_plan_raises(conn, settings):
    session = _create_session(conn, settings)
    with pytest.raises(ValueError):
        PlansService(conn, settings).select_node(session.id, "k1")
