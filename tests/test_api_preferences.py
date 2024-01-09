from __future__ import annotations


def _create_session(client, goal="learn algebra"):
    response = client.post("/api/sessions", json={"goal": goal, "file_ids": []})
    assert response.status_code == 200, response.text
    return response.json()


def test_preferences_defaults(client):
    response = client.get("/api/preferences")
    assert response.status_code == 200
    payload = response.json()
    assert payload["depth"] == "standard"
    assert payload["pacing"] == "normal"
    assert payload["style"] == "analogy-first"
    assert "updated_at" in payload


def test_preferences_patch_partial(client):
    response = client.patch("/api/preferences", json={"depth": "deep"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["depth"] == "deep"
    assert payload["pacing"] == "normal"
    assert payload["style"] == "analogy-first"
    second = client.patch("/api/preferences", json={"style": "formal-first", "notes": "fast learner"})
    assert second.status_code == 200
    body = second.json()
    assert body["depth"] == "deep"
    assert body["style"] == "formal-first"
    assert body["notes"] == "fast learner"


def test_preferences_delete_resets(client):
    client.patch("/api/preferences", json={"depth": "deep", "notes": "x"})
    response = client.delete("/api/preferences")
    assert response.status_code == 204
    payload = client.get("/api/preferences").json()
    assert payload["depth"] == "standard"
    assert payload["pacing"] == "normal"
    assert payload["notes"] is None


def test_mastery_delete_resets(client, conn):
    session = _create_session(client)
    conn.execute(
        "INSERT INTO mastery_topics (topic, label, observed_count, correct_count, "
        "idk_count, last_assessed_at, evidence_json) "
        "VALUES ('algebra', 'Algebra', 1, 1, 0, '2026-01-01T00:00:00Z', '[]')"
    )
    conn.commit()
    response = client.delete("/api/mastery")
    assert response.status_code == 204
    assert conn.execute("SELECT COUNT(*) FROM mastery_topics").fetchone()[0] == 0


def test_delete_session_204_and_cascades(client, conn):
    session = _create_session(client)
    conn.execute(
        "INSERT INTO plan_nodes (id, session_id, node_key, title, position) "
        "VALUES ('n1', ?, 'k1', 'Node', 0)",
        (session["id"],),
    )
    conn.execute(
        "INSERT INTO quiz_questions (id, session_id, kind, question, options_json, "
        "correct_index, status, created_at) "
        "VALUES ('q1', ?, 'probe', 'Q?', '[]', 0, 'pending', '2026-01-01T00:00:00Z')",
        (session["id"],),
    )
    conn.commit()
    response = client.delete(f"/api/sessions/{session['id']}")
    assert response.status_code == 204
    assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM plan_nodes").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM quiz_questions").fetchone()[0] == 0
    assert client.get(f"/api/sessions/{session['id']}").status_code == 404


def test_delete_missing_session_404(client):
    response = client.delete("/api/sessions/nope")
    assert response.status_code == 404
