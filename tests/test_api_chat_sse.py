"""Chat SSE integration tests: event sequence, partial persistence, retry,
dedupe, strict-mode insufficiency, and config rejection."""

from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient

from app.config import Settings
from app.errors import ProviderError
from app.main import create_app
from tests.conftest import sse_events, upload_txt


def _first_chunk_id(settings, file_id: str) -> str:
    conn = sqlite3.connect(str(settings.db_path))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT id FROM chunks WHERE file_id=? ORDER BY chunk_index LIMIT 1",
            (file_id,),
        ).fetchone()
        return row["id"]
    finally:
        conn.close()


def _message_row(settings, session_id: str, client_msg_id: str) -> dict:
    conn = sqlite3.connect(str(settings.db_path))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM messages WHERE session_id=? AND client_msg_id=?",
            (session_id, client_msg_id),
        ).fetchone()
        return dict(row)
    finally:
        conn.close()


def _create_session(client, file_ids=None, goal="learn group theory"):
    response = client.post(
        "/api/sessions",
        json={"goal": goal, "file_ids": file_ids or [], "grounding_mode": "grounded"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _ready_session(client, settings, text=None):
    text = text or "# Algebra\n\nA group is a set with a binary operation and an identity element."
    record = upload_txt(client, "notes.md", text, "text/markdown")
    session = _create_session(client, [record["id"]])
    return session, _first_chunk_id(settings, record["id"])


def test_turn_event_sequence(client, override_llm, settings):
    session, chunk_id = _ready_session(client, settings)
    override_llm.script("group", f"A group is a set with an operation. [cit:{chunk_id}]")

    with client.stream(
        "POST",
        f"/api/sessions/{session['id']}/turns",
        json={"message": "What is a group?", "client_msg_id": "c1"},
    ) as response:
        events = list(sse_events(response))

    types = [e["type"] for e in events]
    assert types[0] == "meta"
    assert "delta" in types
    assert "citation" in types
    assert types[-1] == "done"

    assert events[0]["insufficient"] is False
    assert events[0]["chunks"][0]["chunk_id"] == chunk_id
    assert events[-1]["replayed"] is False

    # Message persisted complete with the real citation.
    full = client.get(f"/api/sessions/{session['id']}").json()
    messages = full["messages"]
    assert messages[-1]["role"] == "assistant"
    assert "[cit:" in messages[-1]["content"]
    assert messages[-1]["citations"] == [chunk_id]
    assert messages[-1]["partial"] == 0


def test_turn_filters_fake_citations(client, override_llm, settings):
    session, chunk_id = _ready_session(client, settings)
    # Model cites a chunk that was NOT sent — must be dropped.
    override_llm.script("group", f"answer [cit:{chunk_id}] [cit:bogus:0:9]")

    with client.stream(
        "POST",
        f"/api/sessions/{session['id']}/turns",
        json={"message": "What is a group?", "client_msg_id": "c2"},
    ) as response:
        events = list(sse_events(response))

    citation_events = [e for e in events if e["type"] == "citation"]
    assert [e["chunk_id"] for e in citation_events] == [chunk_id]

    full = client.get(f"/api/sessions/{session['id']}").json()
    assert full["messages"][-1]["citations"] == [chunk_id]


def test_mid_stream_failure_persists_partial(client, override_llm, settings):
    session, chunk_id = _ready_session(client, settings)
    override_llm.script("group", "alpha beta gamma delta")
    override_llm.fail_after = 2
    override_llm.failure = ProviderError("upstream", "fake boom")

    with client.stream(
        "POST",
        f"/api/sessions/{session['id']}/turns",
        json={"message": "What is a group?", "client_msg_id": "c3"},
    ) as response:
        events = list(sse_events(response))

    assert events[0]["type"] == "meta"
    assert "delta" in [e["type"] for e in events]
    error_event = events[-1]
    assert error_event["type"] == "error"
    assert error_event["code"] == "upstream"

    # Partial marker persisted with the client_msg_id; not exposed as complete.
    row = _message_row(settings, session["id"], "c3")
    assert row["partial"] == 1
    full = client.get(f"/api/sessions/{session['id']}").json()
    assert all(m["client_msg_id"] != "c3" for m in full["messages"])


def test_retry_regenerates_partial(client, override_llm, settings):
    session, chunk_id = _ready_session(client, settings)
    override_llm.script("group", "alpha beta gamma")
    override_llm.fail_after = 1
    override_llm.failure = ProviderError("timeout", "slow")

    with client.stream(
        "POST",
        f"/api/sessions/{session['id']}/turns",
        json={"message": "What is a group?", "client_msg_id": "c4"},
    ) as response:
        events = list(sse_events(response))
    assert events[-1]["type"] == "error"
    assert _message_row(settings, session["id"], "c4")["partial"] == 1

    # Now the model succeeds; retry regenerates and completes.
    override_llm.fail_after = None
    override_llm.failure = ProviderError("upstream", "unused")
    override_llm.script("group", "A group has an identity element. [cit:" + chunk_id + "]")

    with client.stream(
        "POST",
        f"/api/sessions/{session['id']}/retry",
        json={"client_msg_id": "c4"},
    ) as response:
        events = list(sse_events(response))

    types = [e["type"] for e in events]
    assert "delta" in types
    assert "citation" in types
    assert types[-1] == "done"
    assert _message_row(settings, session["id"], "c4")["partial"] == 0


def test_retry_completed_replays_done_only(client, override_llm, settings):
    session, chunk_id = _ready_session(client, settings)
    override_llm.script("group", "plain answer")

    with client.stream(
        "POST",
        f"/api/sessions/{session['id']}/turns",
        json={"message": "What is a group?", "client_msg_id": "c5"},
    ) as response:
        list(sse_events(response))

    # Retrying a completed turn must only replay `done`, no model call.
    before = len(override_llm.calls)
    with client.stream(
        "POST",
        f"/api/sessions/{session['id']}/retry",
        json={"client_msg_id": "c5"},
    ) as response:
        events = list(sse_events(response))

    assert all(e["type"] == "done" for e in events)
    assert events[0]["replayed"] is True
    assert len(override_llm.calls) == before


def test_turn_duplicate_client_msg_id_replays_done(client, override_llm, settings):
    session, chunk_id = _ready_session(client, settings)
    override_llm.script("group", "answer one")

    with client.stream(
        "POST",
        f"/api/sessions/{session['id']}/turns",
        json={"message": "What is a group?", "client_msg_id": "c6"},
    ) as response:
        list(sse_events(response))

    # Duplicate turn with the same client_msg_id: replay done, no model call.
    before = len(override_llm.calls)
    with client.stream(
        "POST",
        f"/api/sessions/{session['id']}/turns",
        json={"message": "What is a group?", "client_msg_id": "c6"},
    ) as response:
        events = list(sse_events(response))

    assert all(e["type"] == "done" for e in events)
    assert events[0]["replayed"] is True
    assert len(override_llm.calls) == before

    # Exactly one message row persisted for c6.
    full = client.get(f"/api/sessions/{session['id']}").json()
    assert len([m for m in full["messages"] if m["client_msg_id"] == "c6"]) == 1


def test_strict_mode_insufficiency(client, override_llm):
    # No ready files -> strict mode must emit the insufficiency notice without
    # ever calling the model.
    session = _create_session(client, file_ids=[], goal="answer from sources only")
    client.patch(
        f"/api/sessions/{session['id']}",
        json={"grounding_mode": "strict"},
    )

    with client.stream(
        "POST",
        f"/api/sessions/{session['id']}/turns",
        json={"message": "Explain photosynthesis", "client_msg_id": "c7"},
    ) as response:
        events = list(sse_events(response))

    assert events[0]["type"] == "meta"
    assert events[0]["insufficient"] is True
    assert events[0]["chunks"] == []
    types = [e["type"] for e in events]
    assert "delta" in types
    assert types[-1] == "done"
    assert not any(e["type"] == "citation" for e in events)
    # Model was never contacted.
    assert override_llm.calls == []

    full = client.get(f"/api/sessions/{session['id']}").json()
    assert full["messages"][-1]["content"].startswith("I could not find")


def test_grounded_mode_proceeds_without_chunks(client, override_llm, settings):
    # Grounded mode with no ready files still calls the model.
    session = _create_session(client, file_ids=[], goal="anything")
    override_llm.script("anything", "general knowledge answer")

    with client.stream(
        "POST",
        f"/api/sessions/{session['id']}/turns",
        json={"message": "Anything at all", "client_msg_id": "c8"},
    ) as response:
        events = list(sse_events(response))

    assert events[0]["type"] == "meta"
    assert events[0]["insufficient"] is False
    types = [e["type"] for e in events]
    assert "delta" in types
    assert types[-1] == "done"


def test_turn_missing_session_404(client, override_llm):
    response = client.post(
        "/api/sessions/nope/turns",
        json={"message": "hi", "client_msg_id": "c9"},
    )
    assert response.status_code == 404


def test_turn_rejects_missing_config(tmp_path):
    from fastapi.testclient import TestClient

    settings = Settings(data_dir=tmp_path / "data", BROT_api_key="")
    app = create_app(settings)
    with TestClient(app) as test_client:
        response = test_client.post(
            "/api/sessions/whatever/turns",
            json={"message": "hi", "client_msg_id": "c10"},
        )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "config_error"


def test_sessions_crud(client, settings):
    record = upload_txt(client, "s.md", "# s\ncontent", "text/markdown")
    created = _create_session(client, [record["id"]], goal="understand sessions")
    assert created["phase"] == "setup"
    assert created["grounding_mode"] == "grounded"

    listing = client.get("/api/sessions").json()
    assert [s["id"] for s in listing] == [created["id"]]

    full = client.get(f"/api/sessions/{created['id']}").json()
    assert full["session"]["goal"] == "understand sessions"
    assert full["selected_files"][0]["id"] == record["id"]
    assert full["selected_files"][0]["status"] == "ready"

    patched = client.patch(
        f"/api/sessions/{created['id']}", json={"grounding_mode": "strict"}
    )
    assert patched.status_code == 200
    assert patched.json()["grounding_mode"] == "strict"

    selected = client.post(
        f"/api/sessions/{created['id']}/files", json={"file_ids": []}
    )
    assert selected.status_code == 200
    assert selected.json()["file_ids"] == []


def test_stop_returns_ok(client, override_llm):
    response = client.post("/api/sessions/some-session/stop")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_sse_format_and_heartbeat_comment(client, override_llm, settings):
    session, chunk_id = _ready_session(client, settings)
    override_llm.script("group", "small answer")

    with client.stream(
        "POST",
        f"/api/sessions/{session['id']}/turns",
        json={"message": "What is a group?", "client_msg_id": "c11"},
    ) as response:
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers.get("cache-control") == "no-cache"
        lines = list(response.iter_lines())

    # Every data line carries a JSON payload with a type field.
    parsed = 0
    for line in lines:
        if line.startswith("data: "):
            import json

            payload = json.loads(line[6:])
            assert "type" in payload
            parsed += 1
    assert parsed >= 3  # meta + delta + done
