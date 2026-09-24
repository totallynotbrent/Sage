from __future__ import annotations

import asyncio
import json

import pytest

from app.errors import ProviderError
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




def test_generate_plan_provider_failure_raises_502(conn, settings):
    session = _create_session(conn, settings)

    class FailingFakeLLM(FakeLLM):
        async def complete_json(self, messages, *, max_tokens=1200, temperature=0.1):
            return (None, "upstream: The model endpoint returned status 500.")

    with pytest.raises(ProviderError) as excinfo:
        asyncio.run(PlansService(conn, settings).generate_plan(session.id, FailingFakeLLM()))
    assert excinfo.value.code == "upstream"
    assert excinfo.value.status_code == 502




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
    service = PlansService(conn, settings)
    # k2 depends on k1: jumping to k2 before k1 is done is refused
    with pytest.raises(ValueError, match="finish these topics first: k1"):
        service.select_node(session.id, "k2")
    # completing the prerequisite unlocks the jump
    conn.execute("UPDATE plan_nodes SET status = 'done' WHERE node_key = 'k1'")
    conn.commit()
    result = service.select_node(session.id, "k2")
    assert result["session"]["phase"] == "teach"
    k2 = next(n for n in result["plan"] if n["node_key"] == "k2")
    assert result["session"]["current_node_id"] == k2["id"]
    assert k2["status"] == "current"


def test_select_node_allows_review_of_done_nodes(conn, settings, fake_llm):
    session, plan = _planned_session(conn, settings, fake_llm)
    service = PlansService(conn, settings)
    conn.execute(
        "UPDATE plan_nodes SET status = 'done' WHERE node_key IN ('k1', 'k2')"
    )
    conn.commit()
    # re-selecting a finished node (review) is always allowed
    result = service.select_node(session.id, "k1")
    assert result["session"]["current_node_id"] == next(
        n["id"] for n in result["plan"] if n["node_key"] == "k1"
    )


