from __future__ import annotations

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import init_db
from app.llm.client import get_llm_client
from app.main import create_app
from tests.fakes.fake_llm import FakeLLM


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        api_key="test-key",
        api_url="http://127.0.0.1:9/v1",
        model="test-model",
        streaming=True,
        max_upload_mb=30,
        max_total_mb=500,
        chunk_chars=100,
        chunk_overlap=20,
        context_chunk_budget=8,
    )


@pytest.fixture
def app(settings) -> "object":
    return create_app(settings)


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def conn(settings) -> sqlite3.Connection:
    init_db(settings.db_path)
    connection = sqlite3.connect(str(settings.db_path), check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    yield connection
    connection.close()


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def override_llm(app, fake_llm):
    app.dependency_overrides[get_llm_client] = lambda: fake_llm
    yield fake_llm
    app.dependency_overrides.clear()


def sse_events(response):
    assert response.status_code == 200, (
        f"expected 200, got {response.status_code}: {response.text[:300]}"
    )
    for line in response.iter_lines():
        if line.startswith("data: "):
            yield json.loads(line[6:])
        elif line.startswith(": "):
            continue


def upload_txt(client, name: str, text: str, content_type: str = "text/plain"):
    response = client.post(
        "/api/files",
        files={"files": (name, text.encode("utf-8"), content_type)},
    )
    assert response.status_code == 200, response.text
    records = response.json()
    assert len(records) == 1
    return records[0]
