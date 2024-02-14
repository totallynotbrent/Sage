from __future__ import annotations

import json


def _create_session(client):
    response = client.post(
        "/api/sessions",
        json={"goal": "learn graphs", "file_ids": [], "grounding_mode": "grounded"},
    )
    assert response.status_code == 200
    return response.json()


def _create_strict_session(client):
    response = client.post(
        "/api/sessions",
        json={"goal": "learn graphs", "file_ids": [], "grounding_mode": "strict"},
    )
    assert response.status_code == 200
    return response.json()


def test_structured_chat_output(client, override_llm):
    session = _create_session(client)
    override_llm.complete_json_responses = [
        json.dumps({"content": "A graph has nodes."})
    ]
    response = client.post(
        f"/api/sessions/{session['id']}/outputs",
        json={"output_kind": "chat", "prompt": "Explain graphs"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["kind"] == "chat"
    assert body["content"]["content"] == "A graph has nodes."
    assert body["validation"]["status"] == "validated"
    assert body["session_id"] == session["id"]


def test_structured_todo_assigns_server_metadata(client, override_llm):
    session = _create_session(client)
    override_llm.complete_json_responses = [
        json.dumps({"title": "Study", "items": [{"text": "Read chapter"}]})
    ]
    response = client.post(
        f"/api/sessions/{session['id']}/outputs",
        json={"output_kind": "todo", "prompt": "Make a study todo"},
    )
    assert response.status_code == 200, response.text
    item = response.json()["content"]["items"][0]
    assert item["id"]
    assert item["position"] == 0


def test_structured_output_rejects_invalid_request(client, override_llm):
    session = _create_session(client)
    response = client.post(
        f"/api/sessions/{session['id']}/outputs",
        json={"output_kind": "unknown", "prompt": "x"},
    )
    assert response.status_code == 422
    assert override_llm.calls == []


def test_strict_output_without_chunks_skips_llm(client, override_llm):
    session = _create_strict_session(client)
    override_llm.complete_json_responses = [
        json.dumps({"content": "should not be used"})
    ]
    response = client.post(
        f"/api/sessions/{session['id']}/outputs",
        json={"output_kind": "chat", "prompt": "Explain graphs"},
    )
    assert response.status_code == 422
    body = response.json()["error"]
    assert body["code"] == "model_output"
    assert body["retryable"] is True
    assert "insufficient_context" in body["detail"]
    assert override_llm.calls == []


def test_structured_mermaid_uses_validated_diagram_type(
    client, override_llm, monkeypatch
):
    from app.services import structured_outputs as so_module

    calls = []

    async def fake_validate(source):
        calls.append(source)
        return "flowchart"

    monkeypatch.setattr(so_module, "validate_mermaid", fake_validate)
    session = _create_session(client)
    override_llm.complete_json_responses = [
        json.dumps({"title": "t", "source": "graph TD\nA-->B"})
    ]
    response = client.post(
        f"/api/sessions/{session['id']}/outputs",
        json={"output_kind": "mermaid", "prompt": "Draw a graph"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["kind"] == "mermaid"
    assert body["content"]["diagram_type"] == "flowchart"
    assert len(calls) == 1
