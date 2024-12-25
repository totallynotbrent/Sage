"""Tool handler implementations for the learning/teach/web tools."""
from __future__ import annotations

from pydantic import ValidationError

from app.errors import ModelOutputError
from app.llm.messages import make_system_prompt
from app.llm.structured_outputs import parse_exact_json, validate_output
from app.llm.tools.schemas import _SCHEMA_HINTS, _WEB_BLOCKED_TOKENS

_KNOWN_ERROR_CODES = frozenset(
    """empty_text empty_output duplicate_options correct_index_out_of_range quiz_count
    quiz_shape unsupported_output_kind duplicate_action_ids invalid_action
    script_content invalid_output invalid_json trailing_data auth rate_limit
    timeout connection bad_request upstream not_found""".split()
)


def _issue_code(exc: Exception) -> str:
    detail = getattr(exc, "detail", None)
    codes = detail.get("issue_codes") if isinstance(detail, dict) else None
    if isinstance(codes, list) and codes:
        return str(codes[0])
    errors = exc.errors() if isinstance(exc, ValidationError) else []
    if errors:
        return str(errors[0].get("type", "invalid_output"))
    message = str(exc)
    if message.startswith("Empty model output"):
        return "empty_output"
    exc_code = getattr(exc, "code", None)
    if isinstance(exc_code, str) and exc_code:
        return exc_code
    code = message.split(":", 1)[0].strip()
    return code if code in _KNOWN_ERROR_CODES else "invalid_output"


def _web_entry(result: dict) -> dict:
    url = str(result.get("url") or "")
    snippet = str(result.get("snippet") or result.get("content") or "")
    return {"title": str(result.get("title") or ""), "url": url, "snippet": snippet}


async def _run_web_search(arguments: dict, ctx) -> dict:
    from app.services.web_search import search_web

    query = str(arguments.get("query") or "").strip()
    budget = min(5, ctx.settings.context_chunk_budget)
    results = await search_web(ctx.settings.searxng_url, query, max_results=budget)
    allowed = [
        result
        for result in results
        if not any(t in str(result.get("url") or "") for t in _WEB_BLOCKED_TOKENS)
    ]
    return {"results": [_web_entry(result) for result in allowed]}


async def _run_generate(kind: str, arguments: dict, ctx) -> dict:
    goal_topic = str(ctx.session_dict.get("goal") or "the session goal")
    topic = str(arguments.get("topic") or "").strip()
    if not topic:
        topic = str(getattr(ctx, "recent_user_text", "") or "").strip()
    if not topic:
        topic = goal_topic
    system = make_system_prompt(ctx.session_dict, ctx.mode, ctx.mastery_summary)
    shape = _SCHEMA_HINTS.get(kind, "{}")
    user = (
        f"Generate ONLY the JSON for output_kind={kind} about: {topic}. "
        f"Return ONLY this exact JSON shape: {shape}. Fill every field. "
        f"The topic MUST stay within the current lesson thread ({topic}). "
        "Do not choose an unrelated subject."
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    validate_fn = ctx.validate_fn or validate_output
    output = None
    for attempt in range(2):
        text, error_text = await ctx.llm.complete_json(messages)
        try:
            if text is None:
                raise ValueError((error_text or "upstream").split(":", 1)[0])
            output = validate_fn(parse_exact_json(text), kind)
            break
        except (ModelOutputError, ValidationError, ValueError) as exc:
            stable_code = _issue_code(exc)
            if attempt:
                return {"error": stable_code}
            messages.append(
                {
                    "role": "assistant",
                    "content": text or f"(no usable answer: {stable_code})",
                }
            )
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"Your previous answer could not be used ({stable_code}). "
                        f"Return ONLY corrected JSON for output_kind={kind} about "
                        f"{topic}, EXACTLY this shape: {shape}"
                    ),
                }
            )
    if output is None:
        return {"error": "invalid_output"}
    payload = output.model_dump()
    count = arguments.get("count")
    if kind == "quiz" and isinstance(count, int) and 1 <= count <= 10:
        if len(payload["questions"]) != count:
            return {"error": "quiz_count"}
    if kind == "mermaid":
        validator = ctx.mermaid_validate
        if validator is None:
            from app.services.mermaid import validate_mermaid

            validator = validate_mermaid
        try:
            payload["diagram_type"] = await validator(payload["source"])
        except Exception as exc:
            logger = logging.getLogger("app")
            logger.warning(
                "mermaid validation failed; raw source: %r",
                payload["source"][:500],
            )
            return {
                "error": _issue_code(exc),
                "source_preview": repr(payload["source"][:400]),
            }
    payload["kind"] = kind
    return payload

def _learning_guard(ctx) -> dict | None:
    if getattr(ctx, "conn", None) is None:
        return {"error": "unavailable_in_context"}
    return None


def _strip_question(question: dict, fields: tuple[str, ...]) -> dict:
    return {field: question.get(field) for field in fields}


def _current_plan_node(conn, session_id: str) -> dict | None:
    from app.services.sessions import plan_node_dict

    row = conn.execute(
        "SELECT * FROM plan_nodes WHERE session_id = ? AND status = 'current' "
        "ORDER BY position, rowid LIMIT 1",
        (session_id,),
    ).fetchone()
    if row is None:
        return None
    node = plan_node_dict(dict(row))
    return {"node_key": node.get("node_key"), "title": node.get("title")}


async def _run_probe(arguments: dict, ctx) -> dict:
    guard = _learning_guard(ctx)
    if guard:
        return guard
    from app.services.learning import LearningService

    service = LearningService(ctx.conn, ctx.settings)
    result = await service.generate_probe(ctx.session_id, ctx.llm)
    questions = [
        _strip_question(question, ("id", "question", "options", "difficulty"))
        for question in result.get("questions") or []
    ]
    return {
        "phase": "probe",
        "count": len(questions),
        "questions": questions,
        "grading_hint": (
            "Grade replies with grade_answer using these exact question_id values."
        ),
    }


async def _run_grade_answer(arguments: dict, ctx) -> dict:
    guard = _learning_guard(ctx)
    if guard:
        return guard
    from app.services.learning import LearningService

    service = LearningService(ctx.conn, ctx.settings)
    result = service.answer_quiz(
        ctx.session_id,
        str(arguments.get("question_id") or ""),
        arguments.get("choice_index"),
        bool(arguments.get("idk") or False),
    )
    return result["result"]


def _plan_label(title) -> str:
    cleaned = str(title or "").replace('"', "").replace("[", "").replace("]", "")
    return " ".join(cleaned.split())


def _build_plan_source(nodes: list[dict]) -> str | None:
    usable = [node for node in nodes if node.get("node_key")]
    if not usable:
        return None
    lines = ["flowchart TD"]
    for node in usable:
        lines.append(f"    {node['node_key']}[{_plan_label(node.get('title'))}]")
    for node in usable:
        for dep in node.get("depends_on") or []:
            lines.append(f"    {dep} --> {node['node_key']}")
    return "\n".join(lines)


async def _validate_plan_diagram(source: str, ctx) -> dict | None:
    validator = getattr(ctx, "mermaid_validate", None)
    if validator is None:
        from app.services.mermaid import validate_mermaid

        validator = validate_mermaid
    try:
        diagram_type = await validator(source)
    except ModelOutputError:
        return None
    return {"source": source, "diagram_type": diagram_type}


async def _run_build_plan(arguments: dict, ctx) -> dict:
    guard = _learning_guard(ctx)
    if guard:
        return guard
    from app.services.plans import PlansService

    service = PlansService(ctx.conn, ctx.settings)
    result = await service.generate_plan(ctx.session_id, ctx.llm)
    nodes = [
        {
            "node_key": node.get("node_key"),
            "title": node.get("title"),
            "depends_on": list(node.get("depends_on") or []),
            "status": node.get("status"),
            "position": node.get("position"),
        }
        for node in result.get("plan") or []
    ]
    payload = {"phase": "plan", "nodes": nodes}
    source = _build_plan_source(nodes)
    if source:
        plan_diagram = await _validate_plan_diagram(source, ctx)
        if plan_diagram is not None:
            payload["plan_diagram"] = plan_diagram
    return payload


async def _run_advance_lesson(arguments: dict, ctx) -> dict:
    guard = _learning_guard(ctx)
    if guard:
        return guard
    from app.services.plans import PlansService
    from app.services.teach import TeachService

    passed_check = bool(arguments.get("passed_check"))
    service = TeachService(ctx.conn, ctx.settings)
    if not passed_check:
        service.sessions.set_phase(ctx.session_id, "remediate")
        updated = service.sessions.get(ctx.session_id)
        payload = {
            "advanced": False,
            "node": _current_plan_node(service.conn, ctx.session_id),
            "check_due": False,
            "session_phase": updated.phase,
        }
        if updated.phase == "remediate":
            from app.services.learning import LearningService

            check = await LearningService(ctx.conn, ctx.settings).generate_check(
                ctx.session_id, ctx.llm
            )
            pending = check.get("questions") or []
            if pending:
                payload["check_question"] = _strip_question(
                    pending[0], ("id", "question", "options")
                )
        return payload
    current_phase = service.sessions.get(ctx.session_id).phase
    if current_phase == "remediate":
        # Check was passed during remediation — clear the flag so advance() can proceed.
        service.sessions.set_phase(ctx.session_id, "teach")
    if service.sessions.get(ctx.session_id).phase == "plan":
        approved = PlansService(ctx.conn, ctx.settings).approve(ctx.session_id)
        current = next(
            (node for node in approved["plan"] if node["status"] == "current"),
            None,
        )
        return {
            "advanced": True,
            "node": {
                "node_key": (current or {}).get("node_key"),
                "title": (current or {}).get("title"),
            },
            "check_due": False,
            "session_phase": approved["session"]["phase"],
        }
    result = service.advance(ctx.session_id)
    session = result["session"]
    if result.get("node") is None or session["phase"] == "complete":
        return {"lesson_complete": True}
    payload = {
        "advanced": True,
        "node": {
            "node_key": result["node"].get("node_key"),
            "title": result["node"].get("title"),
        },
        "check_due": bool(result.get("check_due")),
        "session_phase": session["phase"],
    }
    if payload["check_due"]:
        from app.services.learning import LearningService

        check = await LearningService(ctx.conn, ctx.settings).generate_check(
            ctx.session_id, ctx.llm
        )
        pending = check.get("questions") or []
        if pending:
            payload["check_question"] = _strip_question(
                pending[0], ("id", "question", "options")
            )
    return payload
