from __future__ import annotations

import json


def plan_node_dict(row: dict) -> dict:
    out = dict(row)
    try:
        out["depends_on"] = json.loads(out.pop("depends_on_json") or "[]")
    except (json.JSONDecodeError, TypeError):
        out["depends_on"] = []
    try:
        out["children"] = json.loads(out.pop("children_json") or "[]")
    except (json.JSONDecodeError, TypeError):
        out["children"] = []
    return out


def question_dict(row: dict) -> dict:
    out = dict(row)
    try:
        out["options"] = json.loads(out.pop("options_json") or "[]")
    except (json.JSONDecodeError, TypeError):
        out["options"] = []
    return out
