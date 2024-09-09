from __future__ import annotations

import json


def _create_session(client, goal="learn algebra"):
    response = client.post("/api/sessions", json={"goal": goal, "file_ids": []})
    assert response.status_code == 200, response.text
    return response.json()


def _one_question(text="What is the derivative?", topic="derivatives"):
    return [
        {
            "question": text,
            "options": ["x", "2x", "x^2"],
            "correct_index": 1,
            "explanation": f"explanation for {text}",
            "topic": topic,
            "difficulty": 2,
        }
    ]


def _answer_wrong(client, sid, qid):
    answer = client.post(
        f"/api/sessions/{sid}/quiz/{qid}/answer", json={"choice_index": 2}
    )
    assert answer.status_code == 200
    assert answer.json()["result"]["outcome"] == "incorrect"


def test_reveal_ladder_escalates_and_caches(client, conn, override_llm):
    sid = _create_session(client)["id"]
    override_llm.complete_json_responses = [
        json.dumps(_one_question()),          # 0: probe questions
        "Step 1: bring the exponent down.",   # 1: worked_example
        "Hint: apply the power rule.",        # 2: hint
    ]

    questions = client.post(f"/api/sessions/{sid}/probe", json={}).json()["questions"]
    qid = questions[0]["id"]
    _answer_wrong(client, sid, qid)

    # Ladder before any LLM rung generated: only reveal is available (static).
    pre = client.post(f"/api/sessions/{sid}/quiz/{qid}/ladder", json={})
    assert pre.status_code == 200
    levels_pre = [r["level"] for r in pre.json()["ladder"]]
    assert levels_pre == ["hint", "worked_example", "walkthrough", "reveal"]
    assert all(r["generated"] is False for r in pre.json()["ladder"][:3])

    # Escalate: worked_example consumes response[1].
    we = client.post(f"/api/sessions/{sid}/quiz/{qid}/worked-example", json={})
    assert we.status_code == 200
    assert we.json()["worked_example"] == "Step 1: bring the exponent down."

    # Caching: repeat returns the same content without another LLM call.
    calls_after_first = len(override_llm.calls)
    we2 = client.post(f"/api/sessions/{sid}/quiz/{qid}/worked-example", json={})
    assert we2.json()["worked_example"] == "Step 1: bring the exponent down."
    assert len(override_llm.calls) == calls_after_first

    # Hint consumes the remaining response[2] via the shared ladder step.
    hint = client.post(f"/api/sessions/{sid}/quiz/{qid}/hint", json={})
    assert hint.status_code == 200
    assert hint.json()["hint"] == "Hint: apply the power rule."

    post = client.post(f"/api/sessions/{sid}/quiz/{qid}/ladder", json={})
    generated = {r["level"]: r for r in post.json()["ladder"] if r["generated"]}
    assert generated["worked_example"]["content"] == "Step 1: bring the exponent down."
    assert generated["hint"]["content"] == "Hint: apply the power rule."
    assert generated["reveal"]["content"] == "2x"

    rows = conn.execute(
        "SELECT action, content FROM feedback_actions WHERE session_id = ? ORDER BY created_at",
        (sid,),
    ).fetchall()
    actions = [(r["action"], r["content"]) for r in rows]
    # Executed in call order: probe answer then worked_example then hint.
    assert actions[-2:] == [
        ("worked_example", "Step 1: bring the exponent down."),
        ("hint", "Hint: apply the power rule."),
    ]


def test_ladder_rejects_unanswered_and_wrong_kind(client, override_llm):
    sid = _create_session(client)["id"]
    override_llm.complete_json_responses = [json.dumps(_one_question())]
    questions = client.post(f"/api/sessions/{sid}/probe", json={}).json()["questions"]
    qid = questions[0]["id"]
    # Not yet answered -> ladder rung generation refused (reveal also absent).
    pre = client.post(f"/api/sessions/{sid}/quiz/{qid}/ladder", json={})
    assert pre.status_code == 200
    assert pre.json()["answered"] is False
    assert pre.json()["ladder"][-1]["level"] != "reveal"
    # Direct worked-example before answering -> 400.
    r = client.post(f"/api/sessions/{sid}/quiz/{qid}/worked-example", json={})
    assert r.status_code == 400