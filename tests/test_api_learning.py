from __future__ import annotations

import json


def _create_session(client, goal="learn algebra"):
    response = client.post("/api/sessions", json={"goal": goal, "file_ids": []})
    assert response.status_code == 200, response.text
    return response.json()


def _good_questions():
    return [
        {
            "question": f"What is Q{i}?",
            "options": ["a", "b", "c"],
            "correct_index": 1,
            "explanation": f"explanation {i}",
            "topic": f"topic-{i}",
            "difficulty": 2,
        }
        for i in range(3)
    ]


def _probe(client, session_id):
    response = client.post(f"/api/sessions/{session_id}/probe", json={})
    assert response.status_code == 200, response.text
    return response.json()


def test_probe_endpoint_happy_path(client, override_llm):
    session = _create_session(client)
    override_llm.complete_json_responses = [json.dumps(_good_questions())]
    payload = _probe(client, session["id"])
    assert payload["session"]["phase"] == "probe"
    assert len(payload["questions"]) == 3
    for question in payload["questions"]:
        assert question["kind"] == "probe"
        assert question["status"] == "pending"
        assert question["options"][-1] == "I don't know"


def test_probe_missing_session_404(client, override_llm):
    response = client.post("/api/sessions/nope/probe", json={})
    assert response.status_code == 404
    body = response.json()["error"]
    assert body["code"] == "not_found"


def test_probe_empty_model_output_422(client, override_llm):
    session = _create_session(client)
    override_llm.complete_json_responses = ["garbage", "garbage"]
    response = client.post(f"/api/sessions/{session['id']}/probe", json={})
    assert response.status_code == 422
    body = response.json()["error"]
    assert body["code"] == "model_output"
    assert body["retryable"] is True


def test_probe_provider_failure_502(client, override_llm):
    async def failing_complete_json(self, messages, *, max_tokens=1200, temperature=0.1):
        return (None, "connection: Could not connect to the model endpoint.")

    override_llm.complete_json = failing_complete_json.__get__(override_llm)
    session = _create_session(client)
    response = client.post(f"/api/sessions/{session['id']}/probe", json={})
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "connection"


def test_answer_endpoint_grading_and_replay(client, override_llm):
    session = _create_session(client)
    override_llm.complete_json_responses = [json.dumps(_good_questions())]
    questions = _probe(client, session["id"])["questions"]
    question_id = questions[0]["id"]

    first = client.post(
        f"/api/sessions/{session['id']}/quiz/{question_id}/answer",
        json={"choice_index": 1},
    )
    assert first.status_code == 200
    first_payload = first.json()
    assert first_payload["result"]["outcome"] == "correct"
    assert first_payload["result"]["correct_index"] == 1

    second = client.post(
        f"/api/sessions/{session['id']}/quiz/{question_id}/answer",
        json={"choice_index": 1},
    )
    assert second.status_code == 200
    assert second.json()["result"]["outcome"] == "correct"

    full = client.get(f"/api/sessions/{session['id']}").json()
    topic = next(m for m in full["mastery"] if m["topic"] == "topic-0")
    assert topic["observed_count"] == 1
    assert topic["correct_count"] == 1


def test_answer_idk_endpoint(client, override_llm):
    session = _create_session(client)
    override_llm.complete_json_responses = [json.dumps(_good_questions())]
    questions = _probe(client, session["id"])["questions"]
    response = client.post(
        f"/api/sessions/{session['id']}/quiz/{questions[0]['id']}/answer",
        json={"idk": True},
    )
    assert response.status_code == 200
    assert response.json()["result"]["outcome"] == "idk"


def test_answer_bad_choice_400(client, override_llm):
    session = _create_session(client)
    override_llm.complete_json_responses = [json.dumps(_good_questions())]
    questions = _probe(client, session["id"])["questions"]
    response = client.post(
        f"/api/sessions/{session['id']}/quiz/{questions[0]['id']}/answer",
        json={"choice_index": 99},
    )
    assert response.status_code == 400


def test_answer_missing_question_404(client, override_llm):
    session = _create_session(client)
    response = client.post(
        f"/api/sessions/{session['id']}/quiz/ghost/answer",
        json={"choice_index": 0},
    )
    assert response.status_code == 404


def test_check_endpoint(client, override_llm):
    session = _create_session(client, goal="learn group theory")
    override_llm.complete_json_responses = [
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
    response = client.post(f"/api/sessions/{session['id']}/check", json={})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["session"]["phase"] == "check"
    assert len(payload["questions"]) == 1
    question = payload["questions"][0]
    assert question["kind"] == "check"
    assert question["topic"] == "learn group theory"


def test_check_endpoint_idempotent(client, override_llm):
    session = _create_session(client)
    override_llm.complete_json_responses = [
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
    first = client.post(f"/api/sessions/{session['id']}/check", json={}).json()
    second = client.post(f"/api/sessions/{session['id']}/check", json={}).json()
    assert [q["id"] for q in second["questions"]] == [q["id"] for q in first["questions"]]
    assert len(override_llm.calls) == 1


def test_check_endpoint_empty_model_output_422(client, override_llm):
    session = _create_session(client)
    override_llm.complete_json_responses = ["garbage", "garbage"]
    response = client.post(f"/api/sessions/{session['id']}/check", json={})
    assert response.status_code == 422


def test_wrong_check_answer_transitions_to_remediate(client, override_llm):
    session = _create_session(client)
    override_llm.complete_json_responses = [
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
    questions = client.post(f"/api/sessions/{session['id']}/check", json={}).json()["questions"]
    response = client.post(
        f"/api/sessions/{session['id']}/quiz/{questions[0]['id']}/answer",
        json={"choice_index": 1},
    )
    assert response.status_code == 200
    assert response.json()["result"]["outcome"] == "incorrect"
    assert response.json()["session"]["phase"] == "remediate"


def test_load_full_includes_preferences(client):
    session = _create_session(client)
    full = client.get(f"/api/sessions/{session['id']}").json()
    assert full["preferences"]["depth"] == "standard"
    assert full["preferences"]["pacing"] == "normal"
    assert "updated_at" in full["preferences"]


def test_probe_config_missing_400(tmp_path):
    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.main import create_app

    settings = Settings(
        data_dir=tmp_path / "data",
        BROT_api_key="",
        BROT_base_url="http://127.0.0.1:9/v1",
    )
    app = create_app(settings)
    with TestClient(app) as client:
        session = client.post(
            "/api/sessions", json={"goal": "learn algebra", "file_ids": []}
        ).json()
        response = client.post(f"/api/sessions/{session['id']}/probe", json={})
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "config_error"
        response = client.post(f"/api/sessions/{session['id']}/check", json={})
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "config_error"


def test_answer_corrupt_options_400(client, conn, override_llm):
    session = _create_session(client)
    conn.execute(
        "INSERT INTO quiz_questions (id, session_id, kind, question, options_json, correct_index, status, created_at) "
        "VALUES ('q-broken', ?, 'probe', 'Q?', 'not-json', 0, 'pending', '2026-01-01T00:00:00Z')",
        (session["id"],),
    )
    conn.commit()
    response = client.post(
        f"/api/sessions/{session['id']}/quiz/q-broken/answer",
        json={"choice_index": 0},
    )
    assert response.status_code == 400
