from __future__ import annotations

import json


def _create_session(client, goal="learn algebra"):
    response = client.post("/api/sessions", json={"goal": goal, "file_ids": []})
    assert response.status_code == 200, response.text
    return response.json()


def _good_plan():
    return {
        "nodes": [
            {
                "node_key": "k1",
                "title": "Basics",
                "description": "d1",
                "depends_on": [],
            },
            {
                "node_key": "k2",
                "title": "Advanced",
                "description": "d2",
                "depends_on": ["k1"],
            },
            {
                "node_key": "k3",
                "title": "Expert",
                "description": "d3",
                "depends_on": ["k2"],
            },
        ]
    }


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


def _check_question():
    return [
        {
            "question": "Check Q?",
            "options": ["x", "y"],
            "correct_index": 0,
            "explanation": "e",
            "topic": None,
            "difficulty": 3,
        }
    ]


def _plan(client, session_id):
    response = client.post(f"/api/sessions/{session_id}/plan", json={})
    assert response.status_code == 200, response.text
    return response.json()


def test_plan_endpoint_happy_path(client, override_llm):
    session = _create_session(client)
    override_llm.complete_json_responses = [json.dumps(_good_plan())]
    payload = _plan(client, session["id"])
    assert payload["session"]["phase"] == "plan"
    assert len(payload["plan"]) == 3
    for node in payload["plan"]:
        assert node["status"] == "pending"
        assert "depends_on" in node
        assert "children" in node


def test_load_full_parses_plan_and_quiz(client, override_llm):
    session = _create_session(client)
    override_llm.complete_json_responses = [
        json.dumps(_good_questions()),
        json.dumps(_good_plan()),
    ]
    client.post(f"/api/sessions/{session['id']}/probe", json={})
    client.post(f"/api/sessions/{session['id']}/plan", json={})
    full = client.get(f"/api/sessions/{session['id']}").json()
    node = full["plan"][1]
    assert node["depends_on"] == ["k1"]
    assert "depends_on_json" not in node
    assert "children_json" not in node
    question = full["quiz"][0]
    assert question["options"][-1] == "I don't know"
    assert "options_json" not in question


def test_plan_missing_session_404(client, override_llm):
    response = client.post("/api/sessions/nope/plan", json={})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_plan_empty_model_output_422(client, override_llm):
    session = _create_session(client)
    override_llm.complete_json_responses = ["garbage", "garbage"]
    response = client.post(f"/api/sessions/{session['id']}/plan", json={})
    assert response.status_code == 422
    body = response.json()["error"]
    assert body["code"] == "model_output"
    assert body["retryable"] is True


def test_plan_duplicate_node_key_422(client, override_llm):
    session = _create_session(client)
    dup = json.dumps(
        {
            "nodes": [
                {"node_key": "k1", "title": "A", "depends_on": []},
                {"node_key": "k1", "title": "B", "depends_on": []},
            ]
        }
    )
    override_llm.complete_json_responses = [dup, dup]
    response = client.post(f"/api/sessions/{session['id']}/plan", json={})
    assert response.status_code == 422
    body = response.json()["error"]
    assert body["code"] == "model_output"
    assert body["retryable"] is True


def test_plan_config_missing_400(tmp_path):
    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.main import create_app

    settings = Settings(
        data_dir=tmp_path / "data",
        brot_api_key="",
        brot_base_url="http://127.0.0.1:9/v1",
    )
    app = create_app(settings)
    with TestClient(app) as client:
        session = _create_session(client)
        response = client.post(f"/api/sessions/{session['id']}/plan", json={})
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "config_error"


def test_approve_endpoint(client, override_llm):
    session = _create_session(client)
    override_llm.complete_json_responses = [json.dumps(_good_plan())]
    plan = _plan(client, session["id"])
    response = client.post(f"/api/sessions/{session['id']}/plan/approve", json={})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["session"]["phase"] == "teach"
    assert payload["session"]["current_node_id"] == plan["plan"][0]["id"]
    assert payload["plan"][0]["status"] == "current"


def test_reorder_endpoint(client, override_llm):
    session = _create_session(client)
    override_llm.complete_json_responses = [json.dumps(_good_plan())]
    _plan(client, session["id"])
    response = client.post(
        f"/api/sessions/{session['id']}/plan/reorder",
        json={"node_keys": ["k3", "k1", "k2"]},
    )
    assert response.status_code == 200
    assert [n["node_key"] for n in response.json()["plan"]] == ["k3", "k1", "k2"]
    bad = client.post(
        f"/api/sessions/{session['id']}/plan/reorder",
        json={"node_keys": ["k1", "k2"]},
    )
    assert bad.status_code == 400


def test_skip_plan_node_endpoint(client, override_llm):
    session = _create_session(client)
    override_llm.complete_json_responses = [json.dumps(_good_plan())]
    _plan(client, session["id"])
    response = client.post(
        f"/api/sessions/{session['id']}/plan/skip", json={"node_key": "k1"}
    )
    assert response.status_code == 200
    k1 = next(n for n in response.json()["plan"] if n["node_key"] == "k1")
    assert k1["status"] == "skipped"
    missing = client.post(
        f"/api/sessions/{session['id']}/plan/skip", json={"node_key": "ghost"}
    )
    assert missing.status_code == 404


def test_expand_endpoint(client, override_llm):
    session = _create_session(client)
    override_llm.complete_json_responses = [
        json.dumps(_good_plan()),
        json.dumps(
            {
                "nodes": [
                    {"node_key": "k1a", "title": "Sub-A", "depends_on": []},
                    {"node_key": "k1b", "title": "Sub-B", "depends_on": []},
                ]
            }
        ),
    ]
    _plan(client, session["id"])
    response = client.post(
        f"/api/sessions/{session['id']}/plan/expand",
        json={"node_key": "k1", "detail": "go deeper"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert len(payload["plan"]) == 5
    new_a = next(n for n in payload["plan"] if n["node_key"] == "k1a")
    assert new_a["depends_on"] == ["k1"]
    parent = next(n for n in payload["plan"] if n["node_key"] == "k1")
    assert "k1a" in parent["children"]


def test_regenerate_endpoint(client, override_llm):
    session = _create_session(client)
    override_llm.complete_json_responses = [
        json.dumps(_good_plan()),
        json.dumps(_good_plan()),
    ]
    _plan(client, session["id"])
    client.post(f"/api/sessions/{session['id']}/plan/approve", json={})
    response = client.post(f"/api/sessions/{session['id']}/plan/regenerate", json={})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["session"]["phase"] == "plan"
    assert payload["session"]["current_node_id"] is None
    assert payload["session"]["nodes_since_check"] == 0


def test_select_plan_node_endpoint(client, override_llm):
    session = _create_session(client)
    override_llm.complete_json_responses = [json.dumps(_good_plan())]
    plan = _plan(client, session["id"])
    response = client.post(
        f"/api/sessions/{session['id']}/plan/select", json={"node_key": "k2"}
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["session"]["phase"] == "teach"
    assert payload["session"]["current_node_id"] == plan["plan"][1]["id"]


def test_full_learning_flow(client, conn, override_llm):
    session = _create_session(client)
    sid = session["id"]
    override_llm.complete_json_responses = [
        json.dumps(_good_questions()),
        json.dumps(_good_plan()),
        json.dumps(_check_question()),
        "Think about the first step of the derivation.",
    ]

    questions = client.post(f"/api/sessions/{sid}/probe", json={}).json()["questions"]
    assert len(questions) == 3
    for question in questions:
        answer = client.post(
            f"/api/sessions/{sid}/quiz/{question['id']}/answer",
            json={"choice_index": 1},
        )
        assert answer.status_code == 200

    plan_payload = client.post(f"/api/sessions/{sid}/plan", json={}).json()
    assert plan_payload["session"]["phase"] == "plan"

    approved = client.post(f"/api/sessions/{sid}/plan/approve", json={}).json()
    assert approved["session"]["phase"] == "teach"
    assert approved["session"]["current_node_id"] == plan_payload["plan"][0]["id"]

    first = client.post(f"/api/sessions/{sid}/advance", json={}).json()
    assert first["node"]["id"] == plan_payload["plan"][1]["id"]
    assert first["check_due"] is False
    second = client.post(f"/api/sessions/{sid}/advance", json={}).json()
    assert second["node"]["id"] == plan_payload["plan"][2]["id"]
    assert second["check_due"] is True

    check_payload = client.post(f"/api/sessions/{sid}/check", json={}).json()
    check_q = check_payload["questions"][0]
    assert check_q["topic"] == "Expert"

    wrong = client.post(
        f"/api/sessions/{sid}/quiz/{check_q['id']}/answer",
        json={"choice_index": 1},
    )
    assert wrong.status_code == 200
    assert wrong.json()["result"]["outcome"] == "incorrect"
    assert wrong.json()["session"]["phase"] == "remediate"

    hint = client.post(f"/api/sessions/{sid}/quiz/{check_q['id']}/hint", json={})
    assert hint.status_code == 200
    assert hint.json()["hint"] == "Think about the first step of the derivation."

    reveal = client.post(f"/api/sessions/{sid}/quiz/{check_q['id']}/reveal", json={})
    assert reveal.status_code == 200
    reveal_payload = reveal.json()
    assert reveal_payload["correct_index"] == 0
    assert reveal_payload["correct_option"] == "x"
    assert reveal_payload["explanation"] == "e"

    rows = conn.execute(
        "SELECT action, content FROM feedback_actions WHERE session_id = ? ORDER BY created_at",
        (sid,),
    ).fetchall()
    assert [(r["action"], r["content"]) for r in rows] == [
        ("hint", "Think about the first step of the derivation."),
        ("reveal", "x"),
    ]

    retry = client.post(
        f"/api/sessions/{sid}/quiz/{check_q['id']}/answer",
        json={"choice_index": 0},
    )
    assert retry.status_code == 200
    retry_payload = retry.json()
    assert retry_payload["result"]["outcome"] == "correct"
    assert retry_payload["result"]["next_node"] is None
    assert retry_payload["result"]["check_due"] is False
    assert retry_payload["session"]["phase"] == "complete"
