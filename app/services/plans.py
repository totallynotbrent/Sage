from __future__ import annotations

import json
import sqlite3

from app.config import Settings
from app.errors import ModelOutputError, NotFoundError
from app.llm.structured import request_plan
from app.models import Plan
from app.services.sessions import SessionService, plan_node_dict
from app.services.teach import TeachService
from app.util import new_id, utc_now


class PlansService:
    def __init__(self, conn: sqlite3.Connection, settings: Settings) -> None:
        self.conn = conn
        self.settings = settings
        self.sessions = SessionService(conn, settings)

    async def generate_plan(self, session_id: str, llm) -> dict:
        session = self.sessions.get(session_id)
        existing = self._nodes(session_id)
        if existing:
            return {"session": session.model_dump(), "plan": existing}
        chunks = self.sessions._select_chunks(session, "learning plan")
        plan = await request_plan(
            llm,
            session=session.model_dump(),
            chunks=chunks,
            mastery_summary=self.sessions._mastery_summary(session_id),
            mode=session.grounding_mode,
            focus=session.goal,
            outline=self._session_outline(session) if session.grounding_mode == "strict" else None,
            lightweight=self.settings.lightweight,
        )
        if plan is None:
            error = ModelOutputError("The model returned no usable learning plan.")
            error.retryable = True
            raise error
        self._replace_nodes(session_id, plan)
        self.sessions.set_phase(session_id, "plan")
        from app.services.plan_graph import plan_to_mermaid

        nodes = self._nodes(session_id)
        return {
            "session": self.sessions.get(session_id).model_dump(),
            "plan": nodes,
            # Deterministic mermaid graph of the plan: gives the learner a
            # map and proves the planner reasoned the full dependency chain.
            "mermaid": plan_to_mermaid(nodes),
        }

    def approve(self, session_id: str) -> dict:
        session = self.sessions.get(session_id)
        nodes = self._nodes(session_id)
        if not nodes:
            raise ValueError("no plan has been generated yet")
        next_node = next((n for n in nodes if n["status"] == "pending"), None)
        if next_node is None:
            raise ValueError("no teachable node in the plan")
        now = utc_now()
        self.conn.execute(
            "UPDATE plan_nodes SET status = 'current' WHERE id = ? AND session_id = ?",
            (next_node["id"], session_id),
        )
        self.conn.execute(
            "UPDATE sessions SET current_node_id = ?, phase = 'teach', "
            "nodes_since_check = 0, updated_at = ? WHERE id = ?",
            (next_node["id"], now, session_id),
        )
        self.conn.commit()
        return {
            "session": self.sessions.get(session_id).model_dump(),
            "plan": self._nodes(session_id),
        }

    def reorder(self, session_id: str, node_keys: list[str]) -> dict:
        nodes = self._nodes(session_id)
        keys = {n["node_key"] for n in nodes}
        if set(node_keys) != keys or len(node_keys) != len(keys):
            raise ValueError("node_keys must contain every plan node exactly once")
        for index, node_key in enumerate(node_keys):
            self.conn.execute(
                "UPDATE plan_nodes SET position = ? WHERE session_id = ? AND node_key = ?",
                (index, session_id, node_key),
            )
        self.conn.commit()
        return {
            "session": self.sessions.get(session_id).model_dump(),
            "plan": self._nodes(session_id),
        }

    def skip_node(self, session_id: str, node_key: str) -> dict:
        session = self.sessions.get(session_id)
        row = self.conn.execute(
            "SELECT * FROM plan_nodes WHERE session_id = ? AND node_key = ?",
            (session_id, node_key),
        ).fetchone()
        if row is None:
            raise NotFoundError("plan node", node_key)
        node = dict(row)
        self.conn.execute(
            "UPDATE plan_nodes SET status = 'skipped' WHERE id = ? AND session_id = ?",
            (node["id"], session_id),
        )
        self.conn.commit()
        if node["status"] == "current":
            if session.phase == "teach":
                TeachService(self.conn, self.settings).advance(session_id)
            else:
                now = utc_now()
                self.conn.execute(
                    "UPDATE sessions SET current_node_id = NULL, updated_at = ? WHERE id = ?",
                    (now, session_id),
                )
                self.conn.commit()
        return {
            "session": self.sessions.get(session_id).model_dump(),
            "plan": self._nodes(session_id),
        }

    async def expand(
        self, session_id: str, node_key: str, detail: str | None, llm
    ) -> dict:
        session = self.sessions.get(session_id)
        row = self.conn.execute(
            "SELECT * FROM plan_nodes WHERE session_id = ? AND node_key = ?",
            (session_id, node_key),
        ).fetchone()
        if row is None:
            raise NotFoundError("plan node", node_key)
        parent = dict(row)
        chunks = self.sessions._select_chunks(session, "learning plan expansion")
        focus = f"{parent['title']}: {detail}" if detail else parent["title"]
        plan = await request_plan(
            llm,
            session=session.model_dump(),
            chunks=chunks,
            mastery_summary=self.sessions._mastery_summary(session_id),
            mode=session.grounding_mode,
            focus=focus,
            outline=self._session_outline(session) if session.grounding_mode == "strict" else None,
            lightweight=self.settings.lightweight,
        )
        if plan is None:
            error = ModelOutputError("The model returned no usable plan expansion.")
            error.retryable = True
            raise error
        existing_keys = {n["node_key"] for n in self._nodes(session_id)}
        new_keys = [n.node_key for n in plan.nodes]
        collisions = [k for k in new_keys if k in existing_keys]
        if collisions:
            raise ValueError(f"plan node key already exists: {collisions[0]}")
        start = len(self._nodes(session_id))
        now = utc_now()
        for index, node in enumerate(plan.nodes):
            self.conn.execute(
                "INSERT INTO plan_nodes (id, session_id, node_key, title, description, "
                "depends_on_json, status, position, children_json) "
                "VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, '[]')",
                (
                    new_id(),
                    session_id,
                    node.node_key,
                    node.title,
                    node.description,
                    json.dumps([node_key]),
                    start + index,
                ),
            )
        parent_children = [
            n["node_key"]
            for n in self._nodes(session_id)
            if node_key in n["depends_on"]
        ]
        self.conn.execute(
            "UPDATE plan_nodes SET children_json = ? WHERE id = ? AND session_id = ?",
            (json.dumps(parent_children), parent["id"], session_id),
        )
        self.conn.commit()
        return {
            "session": self.sessions.get(session_id).model_dump(),
            "plan": self._nodes(session_id),
        }

    async def regenerate(self, session_id: str, llm) -> dict:
        self.sessions.get(session_id)
        now = utc_now()
        self.conn.execute("DELETE FROM plan_nodes WHERE session_id = ?", (session_id,))
        self.conn.execute(
            "UPDATE sessions SET current_node_id = NULL, nodes_since_check = 0, "
            "updated_at = ? WHERE id = ?",
            (now, session_id),
        )
        self.conn.commit()
        return await self.generate_plan(session_id, llm)

    def select_node(self, session_id: str, node_key: str) -> dict:
        session = self.sessions.get(session_id)
        if not self._nodes(session_id):
            raise ValueError("no plan has been generated yet")
        row = self.conn.execute(
            "SELECT * FROM plan_nodes WHERE session_id = ? AND node_key = ?",
            (session_id, node_key),
        ).fetchone()
        if row is None:
            raise NotFoundError("plan node", node_key)
        node = dict(row)
        deps = json.loads(node.get("depends_on_json") or "[]")
        unmet = [
            dep
            for dep in deps
            if self.conn.execute(
                "SELECT status FROM plan_nodes WHERE session_id = ? AND node_key = ?",
                (session_id, dep),
            ).fetchone()["status"]
            not in ("done", "skipped")
        ]
        if unmet and node.get("status") == "pending":
            # jumping ahead of the learning sequence is refused: the plan's
            # dependency graph gates teaching, same as advance()
            raise ValueError(
                "finish these topics first: " + ", ".join(unmet)
            )
        now = utc_now()
        self.conn.execute(
            "UPDATE plan_nodes SET status = 'pending' WHERE session_id = ? AND status = 'current'",
            (session_id,),
        )
        self.conn.execute(
            "UPDATE plan_nodes SET status = 'current' WHERE id = ? AND session_id = ?",
            (node["id"], session_id),
        )
        self.conn.execute(
            "UPDATE sessions SET current_node_id = ?, phase = 'teach', updated_at = ? "
            "WHERE id = ?",
            (node["id"], now, session_id),
        )
        self.conn.commit()
        return {
            "session": self.sessions.get(session_id).model_dump(),
            "plan": self._nodes(session_id),
        }

    def _nodes(self, session_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM plan_nodes WHERE session_id = ? ORDER BY position, rowid",
            (session_id,),
        ).fetchall()
        return [plan_node_dict(dict(r)) for r in rows]

    def _session_outline(self, session) -> list[dict]:
        # collect the uploaded pdf section outlines in file order
        combined: list[dict] = []
        for file_id in (session.file_ids or []):
            try:
                record = self.sessions.files.get(file_id)
            except NotFoundError:
                continue
            if record.outline:
                combined.extend(record.outline)
        return combined

    def _replace_nodes(self, session_id: str, plan: Plan) -> None:
        self.conn.execute("DELETE FROM plan_nodes WHERE session_id = ?", (session_id,))
        now = utc_now()
        for index, node in enumerate(plan.nodes):
            children = [n.node_key for n in plan.nodes if node.node_key in n.depends_on]
            self.conn.execute(
                "INSERT INTO plan_nodes (id, session_id, node_key, title, description, "
                "depends_on_json, status, position, children_json) "
                "VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
                (
                    new_id(),
                    session_id,
                    node.node_key,
                    node.title,
                    node.description,
                    json.dumps(node.depends_on),
                    index,
                    json.dumps(children),
                ),
            )
        self.conn.commit()
