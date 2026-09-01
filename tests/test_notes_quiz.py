from __future__ import annotations

import asyncio
import json

import pytest

from app.errors import ModelOutputError, NotFoundError
from app.services.learning import LearningService
from app.services.sessions import SessionService
from tests.fakes.fake_llm import FakeLLM


def _good_question(question="Rolle Q?"):
    return {
        "question": question,
        "options": ["option a", "option b", "option c"],
        "correct_index": 1,
        "explanation": "stated by the theorem",
        "topic": "calculus",
        "difficulty": 2,
    }


def _ready_file(conn, file_id="f1", name="calc.tex", subject="calculus", chunks=None):
    if chunks is None:
        chunks = [
            {
                "chunk_id": f"{file_id}-c0",
                "environment": "theorem",
                "label": "thm:rolle",
                "section": "Rolle's Theorem",
                "text": "If f is continuous on [a, b], then the integral ∫ f exists.",
            },
            {
                "chunk_id": f"{file_id}-c1",
                "environment": "definition",
                "label": None,
                "section": "Derivative",
                "text": "The derivative f′ is the limit of a difference quotient.",
            },
        ]
    now = "2026-01-01T00:00:00Z"
    conn.execute(
        "INSERT INTO files (id, display_name, storage_name, mime_type, size_bytes, sha256, status, warnings, error, num_chunks, paired_file_id, subject, source_path, created_at, updated_at) "
        "VALUES (?, ?, ?, 'text/x-tex', 10, ?, 'ready', '[]', NULL, ?, NULL, ?, NULL, ?, ?)",
        (
            file_id,
            name,
            f"{file_id}.tex",
            f"sha-{file_id}",
            len(chunks),
            subject,
            now,
            now,
        ),
    )
    for index, chunk in enumerate(chunks):
        conn.execute(
            "INSERT INTO chunks (id, file_id, chunk_index, text, unicode_text, environment, label, location_kind, page, slide, section, start_line, end_line, char_start, char_end) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'lines', NULL, NULL, ?, 1, 1, 0, 10)",
            (
                chunk["chunk_id"],
                file_id,
                index,
                chunk["text"],
                chunk["text"],
                chunk["environment"],
                chunk["label"],
                chunk["section"],
            ),
        )
    conn.commit()
    return file_id


def _session_with_notes(conn, settings, file_ids=("f1",)):
    _ready_file(conn)
    return SessionService(conn, settings).create("learn calculus", list(file_ids))


def _user_texts(fake_llm):
    return [
        m["content"]
        for call in fake_llm.calls
        for m in call["messages"]
        if m.get("role") == "user"
    ]


def test_generate_notes_quiz_happy_path(conn, settings, fake_llm):
    session = _session_with_notes(conn, settings)
    fake_llm.complete_json_responses = [
        json.dumps(_good_question("Q1?")),
        json.dumps({"supported": True}),
        json.dumps(_good_question("Q2?")),
        json.dumps({"supported": True}),
    ]
    result = asyncio.run(
        LearningService(conn, settings).generate_notes_quiz(
            session.id, fake_llm, count=2
        )
    )
    assert len(result["questions"]) == 2
    assert result["session"]["phase"] == "setup"
    for question, chunk_id in zip(result["questions"], ("f1-c0", "f1-c1")):
        assert question["kind"] == "notes"
        assert question["status"] == "pending"
        assert question["options"][-1] == "I don't know"
        source_ref = json.loads(question["source_ref"])
        assert source_ref["chunk_id"] == chunk_id
        assert source_ref["file_id"] == "f1"
        assert source_ref["file_name"] == "calc.tex"
        assert source_ref["environment"] in ("theorem", "definition")
        assert source_ref["section"] in ("Rolle's Theorem", "Derivative")


def test_generate_notes_quiz_unicode_math_rule_in_prompt(conn, settings, fake_llm):
    session = _session_with_notes(conn, settings)
    fake_llm.complete_json_responses = [
        json.dumps(_good_question()),
        json.dumps({"supported": True}),
    ]
    asyncio.run(
        LearningService(conn, settings).generate_notes_quiz(
            session.id, fake_llm, count=1
        )
    )
    joined = "\n".join(_user_texts(fake_llm))
    assert "α" in joined
    assert "∫" in joined
    assert "\\alpha" in joined
    assert "\\int" in joined
    assert "near-misses" in joined


def test_generate_notes_quiz_drops_unsupported(conn, settings, fake_llm):
    session = _session_with_notes(conn, settings)
    fake_llm.complete_json_responses = [
        json.dumps(_good_question("Q1?")),
        json.dumps({"supported": True}),
        json.dumps(_good_question("Q2?")),
        json.dumps({"supported": False}),
    ]
    result = asyncio.run(
        LearningService(conn, settings).generate_notes_quiz(
            session.id, fake_llm, count=1
        )
    )
    assert len(result["questions"]) == 1
    source_ref = json.loads(result["questions"][0]["source_ref"])
    assert source_ref["chunk_id"] == "f1-c0"


def test_generate_notes_quiz_uses_later_seeds_after_early_failures(
    conn, settings, fake_llm
):
    _ready_file(
        conn,
        file_id="f1",
        name="calc.tex",
        subject="calculus",
        chunks=[
            {
                "chunk_id": f"f1-c{index}",
                "environment": "theorem",
                "label": f"thm:{index}",
                "section": "S",
                "text": f"seed {index}",
            }
            for index in range(5)
        ],
    )
    session = SessionService(conn, settings).create("learn calculus", ["f1"])
    responses = []
    for index in range(5):
        responses.append(json.dumps(_good_question(f"Q{index}?")))
        responses.append(json.dumps({"supported": index >= 2}))
    fake_llm.complete_json_responses = responses
    result = asyncio.run(
        LearningService(conn, settings).generate_notes_quiz(
            session.id, fake_llm, count=3
        )
    )
    assert len(result["questions"]) == 3
    refs = [json.loads(q["source_ref"])["chunk_id"] for q in result["questions"]]
    assert refs == ["f1-c2", "f1-c3", "f1-c4"]


def test_generate_notes_quiz_all_dropped_raises(conn, settings, fake_llm):
    session = _session_with_notes(conn, settings)
    fake_llm.complete_json_responses = [json.dumps(_good_question())]
    fake_llm.script("supported", json.dumps({"supported": False}))
    with pytest.raises(ModelOutputError) as excinfo:
        asyncio.run(
            LearningService(conn, settings).generate_notes_quiz(
                session.id, fake_llm, count=1
            )
        )
    assert excinfo.value.status_code == 422
    assert excinfo.value.retryable is True


def test_generate_notes_quiz_no_seed_chunks_raises(conn, settings, fake_llm):
    session = SessionService(conn, settings).create("learn calculus", [])
    with pytest.raises(ModelOutputError) as excinfo:
        asyncio.run(
            LearningService(conn, settings).generate_notes_quiz(session.id, fake_llm)
        )
    assert "environments" in str(excinfo.value)
    assert excinfo.value.retryable is True


def test_generate_notes_quiz_subject_filter(conn, settings, fake_llm):
    _ready_file(conn, file_id="f1", name="calc.tex", subject="calculus")
    _ready_file(conn, file_id="f2", name="algebra.tex", subject="algebra")
    session = SessionService(conn, settings).create("learn both", ["f1", "f2"])
    fake_llm.complete_json_responses = [
        json.dumps(_good_question()),
        json.dumps({"supported": True}),
    ]
    result = asyncio.run(
        LearningService(conn, settings).generate_notes_quiz(
            session.id, fake_llm, count=1, subject="algebra"
        )
    )
    assert len(result["questions"]) == 1
    source_ref = json.loads(result["questions"][0]["source_ref"])
    assert source_ref["file_id"] == "f2"


def test_generate_notes_quiz_bad_count_raises(conn, settings, fake_llm):
    session = _session_with_notes(conn, settings)
    with pytest.raises(ValueError):
        asyncio.run(
            LearningService(conn, settings).generate_notes_quiz(
                session.id, fake_llm, count=0
            )
        )


def test_generate_notes_quiz_missing_session(conn, settings, fake_llm):
    with pytest.raises(NotFoundError):
        asyncio.run(
            LearningService(conn, settings).generate_notes_quiz("nope", fake_llm)
        )


def test_generate_notes_quiz_idempotent(conn, settings, fake_llm):
    session = _session_with_notes(conn, settings)
    fake_llm.complete_json_responses = [
        json.dumps(_good_question("Q1?")),
        json.dumps({"supported": True}),
        json.dumps(_good_question("Q2?")),
        json.dumps({"supported": True}),
    ]
    service = LearningService(conn, settings)
    first = asyncio.run(service.generate_notes_quiz(session.id, fake_llm, count=2))
    second = asyncio.run(service.generate_notes_quiz(session.id, fake_llm, count=2))
    assert [q["id"] for q in second["questions"]] == [
        q["id"] for q in first["questions"]
    ]
    assert len(fake_llm.calls) == 4


def test_notes_quiz_endpoint_happy_path(client, conn, override_llm):
    _ready_file(conn)
    session = client.post(
        "/api/sessions", json={"goal": "learn calculus", "file_ids": ["f1"]}
    ).json()
    override_llm.complete_json_responses = [
        json.dumps(_good_question()),
        json.dumps({"supported": True}),
    ]
    response = client.post(
        f"/api/sessions/{session['id']}/notes-quiz", json={"count": 1}
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert len(payload["questions"]) == 1
    question = payload["questions"][0]
    assert question["kind"] == "notes"
    assert json.loads(question["source_ref"])["chunk_id"] == "f1-c0"
    assert payload["session"]["phase"] == "setup"


def test_notes_quiz_endpoint_missing_session_404(client, override_llm):
    response = client.post("/api/sessions/nope/notes-quiz", json={})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_notes_quiz_endpoint_empty_model_output_422(client, conn, override_llm):
    _ready_file(conn)
    session = client.post(
        "/api/sessions", json={"goal": "learn calculus", "file_ids": ["f1"]}
    ).json()
    override_llm.complete_json_responses = ["garbage", "garbage"]
    response = client.post(
        f"/api/sessions/{session['id']}/notes-quiz", json={"count": 1}
    )
    assert response.status_code == 422
    body = response.json()["error"]
    assert body["code"] == "model_output"
    assert body["retryable"] is True


def test_notes_quiz_endpoint_provider_failure_502(client, conn, override_llm):
    async def failing_complete_json(
        self, messages, *, max_tokens=1200, temperature=0.1
    ):
        return (None, "connection: Could not connect to the model endpoint.")

    override_llm.complete_json = failing_complete_json.__get__(override_llm)
    _ready_file(conn)
    session = client.post(
        "/api/sessions", json={"goal": "learn calculus", "file_ids": ["f1"]}
    ).json()
    response = client.post(
        f"/api/sessions/{session['id']}/notes-quiz", json={"count": 1}
    )
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "connection"


def test_notes_quiz_endpoint_bad_count_400(client, conn, override_llm):
    _ready_file(conn)
    session = client.post(
        "/api/sessions", json={"goal": "learn calculus", "file_ids": ["f1"]}
    ).json()
    response = client.post(
        f"/api/sessions/{session['id']}/notes-quiz", json={"count": 0}
    )
    assert response.status_code == 400


def test_notes_quiz_endpoint_config_missing_400(tmp_path):
    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.main import create_app

    settings = Settings(
        data_dir=tmp_path / "data",
        api_key="",
        api_url="http://127.0.0.1:9/v1",
    )
    app = create_app(settings)
    with TestClient(app) as client:
        session = client.post(
            "/api/sessions", json={"goal": "learn algebra", "file_ids": []}
        ).json()
        response = client.post(f"/api/sessions/{session['id']}/notes-quiz", json={})
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "config_error"


def test_notes_quiz_answer_endpoint_keeps_phase(client, conn, override_llm):
    _ready_file(conn)
    session = client.post(
        "/api/sessions", json={"goal": "learn calculus", "file_ids": ["f1"]}
    ).json()
    override_llm.complete_json_responses = [
        json.dumps(_good_question()),
        json.dumps({"supported": True}),
    ]
    question = client.post(
        f"/api/sessions/{session['id']}/notes-quiz", json={"count": 1}
    ).json()["questions"][0]
    response = client.post(
        f"/api/sessions/{session['id']}/quiz/{question['id']}/answer",
        json={"choice_index": 1},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["result"]["outcome"] == "correct"
    assert payload["result"]["next_node"] is None
    assert payload["session"]["phase"] == "setup"
