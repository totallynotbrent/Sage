from __future__ import annotations

import asyncio
import subprocess

import pytest

from app.errors import ModelOutputError
from app.services import mermaid
from app.services.mermaid import validate_mermaid


def test_mermaid_rejects_unsafe_source():
    with pytest.raises(ModelOutputError):
        asyncio.run(validate_mermaid("<script>alert(1)</script>"))


def test_mermaid_rejects_oversized_source():
    with pytest.raises(ModelOutputError):
        asyncio.run(validate_mermaid("graph TD\n" + "A-->B\n" * 3000))


def test_mermaid_timeout_is_unavailable_error(monkeypatch):
    def raise_timeout(source):
        raise subprocess.TimeoutExpired(["node"], 30)

    monkeypatch.setattr(mermaid, "_run_adapter", raise_timeout)

    with pytest.raises(ModelOutputError) as raised:
        asyncio.run(validate_mermaid("graph TD\nA-->B"))

    assert raised.value.detail == {"issue_codes": ["mermaid_validator_unavailable"]}


def test_mermaid_unwraps_dict_diagram_type(monkeypatch):
    def fake_adapter(source):
        return {
            "ok": True,
            "diagram_type": {"diagramType": "flowchart-v2", "config": {}},
        }

    monkeypatch.setattr(mermaid, "_run_adapter", fake_adapter)

    assert asyncio.run(validate_mermaid("graph TD\nA-->B")) == "flowchart-v2"
