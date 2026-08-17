"""Structured model outputs: plan and quiz-question parsing/validation.

Both ``request_plan`` and ``request_questions`` follow the same strategy:
parse + validate, one corrective retry when the output is malformed, then
graceful degradation (linear outline / keep the valid questions).
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.errors import ModelOutputError, ProviderError, PROVIDER_STATUS
from app.models import Plan, PlanNode, QuizQuestionInput

#: User-role message appended after a malformed first attempt.
PLAN_RETRY_NOTE = (
    "Your previous answer could not be parsed. Return ONLY a JSON object with a "
    '"nodes" array; each node must have "node_key", "title", "description", and '
    '"depends_on" (an array of node_key strings that exist in the same plan).'
)
QUESTIONS_RETRY_NOTE = (
    'Your previous answer could not be parsed. Return ONLY a JSON array of '
    'question objects; each must have "question" (string), "options" (array of '
    '2-6 strings), "correct_index" (integer index of the correct option), '
    '"explanation" (string), "topic" (string), and "difficulty" (integer 1-5).'
)


def provider_error_from_text(error_text: str | None) -> ProviderError:
    """Rebuild a ``ProviderError`` from the client's normalized error string."""
    if not error_text:
        return ProviderError("upstream", detail="The model endpoint returned no response.")
    code, sep, rest = error_text.partition(": ")
    if not sep or code not in PROVIDER_STATUS:
        return ProviderError("upstream", detail=error_text)
    return ProviderError(code, detail=rest or None)


def parse_json(text: str) -> Any:
    """Parse ``text`` as JSON, tolerating markdown fences and surrounding prose.

    Finds the first balanced ``{...}`` or ``[...]`` in the text. Raises
    ``ModelOutputError`` when nothing parseable is found.
    """
    if not text:
        raise ModelOutputError("Empty model output; expected JSON.")
    cleaned = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)

    start = -1
    for i, ch in enumerate(cleaned):
        if ch in "{[":
            start = i
            break
    if start < 0:
        raise ModelOutputError("Model output contained no JSON object or array.")

    opening = cleaned[start]
    closing = "}" if opening == "{" else "]"
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(cleaned)):
        ch = cleaned[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == opening:
            depth += 1
        elif ch == closing:
            depth -= 1
            if depth == 0:
                candidate = cleaned[start : i + 1]
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError as exc:
                    raise ModelOutputError(
                        f"Model output was not valid JSON: {exc.msg}"
                    ) from exc
    raise ModelOutputError("Unbalanced JSON in model output.")


def _try_parse(text: str) -> Any | None:
    try:
        return parse_json(text)
    except ModelOutputError:
        return None


def validate_plan(raw: Any) -> Plan | None:
    """Validate raw parsed JSON into a ``Plan``, or return None.

    A plan is valid when it has a non-empty ``nodes`` list, every node has a
    ``node_key`` and ``title``, and no ``depends_on`` reference dangles.
    """
    if not isinstance(raw, dict):
        return None
    raw_nodes = raw.get("nodes")
    if not isinstance(raw_nodes, list) or not raw_nodes:
        return None

    nodes: list[PlanNode] = []
    keys: set[str] = set()
    for index, item in enumerate(raw_nodes):
        if not isinstance(item, dict):
            return None
        node_key = item.get("node_key")
        title = item.get("title")
        if not isinstance(node_key, str) or not node_key.strip():
            return None
        if not isinstance(title, str) or not title.strip():
            return None
        depends_on = item.get("depends_on") or []
        if not isinstance(depends_on, list) or not all(
            isinstance(d, str) for d in depends_on
        ):
            return None
        if node_key in keys:
            return None
        keys.add(node_key)
        nodes.append(
            PlanNode(
                node_key=node_key,
                title=title,
                description=item.get("description"),
                depends_on=depends_on,
                position=index,
            )
        )
    plan = Plan(nodes=nodes)
    if not plan.validate_dependencies():
        return None
    return plan


def _valid_question(item: Any) -> QuizQuestionInput | None:
    """Validate a single raw question; return None when malformed."""
    if not isinstance(item, dict):
        return None
    options = item.get("options")
    if not isinstance(options, list) or not (2 <= len(options) <= 6):
        return None
    if not all(isinstance(o, str) and o.strip() for o in options):
        return None
    correct_index = item.get("correct_index")
    if not isinstance(correct_index, int):
        return None
    question = item.get("question")
    if not isinstance(question, str) or not question.strip():
        return None
    candidate = QuizQuestionInput(
        topic=item.get("topic"),
        difficulty=item.get("difficulty", 3),
        question=question.strip(),
        options=options,
        correct_index=correct_index,
        explanation=item.get("explanation"),
    )
    if not candidate.validate_index():
        return None
    return candidate


def validate_questions(raw: Any) -> list[QuizQuestionInput] | None:
    """Validate raw parsed JSON into a list of questions, or None.

    Strict: every item must be a valid question (2-6 options, correct index in
    range, unique ids). The "I don't know" option is appended by the server
    later, not expected from the model.
    """
    if isinstance(raw, dict):
        raw = raw.get("questions")
    if not isinstance(raw, list) or not raw:
        return None
    result = [_valid_question(item) for item in raw]
    if any(q is None for q in result):
        return None
    return result  # type: ignore[return-value]


def _questions_from_fragments(raw: Any) -> list[QuizQuestionInput]:
    """Graceful degradation: keep every individually valid question."""
    if isinstance(raw, dict):
        raw = raw.get("questions")
    if not isinstance(raw, list):
        return []
    out: list[QuizQuestionInput] = []
    for item in raw:
        q = _valid_question(item)
        if q is not None:
            out.append(q)
    return out


def _plan_from_fragments(raw: Any) -> Plan | None:
    """Graceful degradation: a linear outline from any parseable nodes."""
    if isinstance(raw, dict):
        raw = raw.get("nodes")
    if not isinstance(raw, list) or not raw:
        return None
    nodes: list[PlanNode] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        title = item.get("title")
        node_key = item.get("node_key")
        if isinstance(title, str) and title.strip():
            node_key = (
                node_key
                if isinstance(node_key, str) and node_key
                else f"step-{index + 1}"
            )
            if node_key in seen:
                return None
            seen.add(node_key)
            nodes.append(
                PlanNode(
                    node_key=node_key,
                    title=title.strip(),
                    description=item.get("description") if isinstance(item.get("description"), str) else None,
                    depends_on=[],
                    position=index,
                )
            )
    return Plan(nodes=nodes) if nodes else None


def _context_block(chunks: list[dict[str, Any]]) -> str:
    """Compact excerpt block for plan/quiz generation prompts."""
    from app.llm.messages import chunk_block

    if not chunks:
        return "No source excerpts are available for this request."
    return "\n\n".join(chunk_block(c) for c in chunks)


async def request_plan(
    llm,
    *,
    session: dict[str, Any],
    chunks: list[dict[str, Any]],
    mastery_summary: str,
    mode: str,
    focus: str | None = None,
) -> Plan | None:
    """Ask the model for a dependency-aware plan with one corrective retry.

    Returns a validated ``Plan`` or, as graceful degradation, a linear outline
    from any parseable fragments; None when nothing usable came back.
    """
    from app.llm.messages import make_system_prompt

    system = make_system_prompt(session, mode, mastery_summary)
    user = (
        "Based on the learner goal and the source excerpts, propose a "
        "dependency-aware learning plan. Return ONLY a JSON object:\n"
        '{"nodes": [{"node_key": "k1", "title": "...", "description": "...", '
        '"depends_on": ["k0"]}]}\n'
        "depends_on must reference node_keys that exist in the same plan.\n\n"
        f"{_context_block(chunks)}"
    )
    if focus:
        user += f"\n\nFocus on: {focus}"
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

    full_text, error_text = await llm.complete_json(messages)
    if full_text is None:
        raise provider_error_from_text(error_text)

    raw = _try_parse(full_text)
    plan = validate_plan(raw)
    if plan is None:
        messages.extend(
            [
                {"role": "assistant", "content": full_text},
                {"role": "user", "content": PLAN_RETRY_NOTE},
            ]
        )
        retry_text, retry_error = await llm.complete_json(messages)
        if retry_text is None:
            raise provider_error_from_text(retry_error)
        if retry_text:
            retry_raw = _try_parse(retry_text)
            plan = validate_plan(retry_raw) or _plan_from_fragments(retry_raw)
    if plan is None:
        plan = _plan_from_fragments(raw)
    return plan


async def request_questions(
    llm,
    *,
    session: dict[str, Any],
    chunks: list[dict[str, Any]],
    mastery_summary: str,
    mode: str,
    count: int = 3,
    focus: str | None = None,
) -> list[QuizQuestionInput]:
    """Ask the model for ``count`` multiple-choice questions with one retry.

    Returns the validated questions, degrading gracefully to the individually
    valid subset; an empty list when nothing usable came back.
    """
    from app.llm.messages import make_system_prompt

    system = make_system_prompt(session, mode, mastery_summary)
    user = (
        f"Write {count} multiple-choice questions relevant to the learner goal "
        "and the source excerpts. Vary difficulty. Return ONLY a JSON array of "
        "question objects, each with: question (string), options (array of 2-6 "
        "strings), correct_index (integer index into options), explanation "
        '(string), topic (string), difficulty (integer 1-5). Do not include an '
        '"I don\'t know" option; the server adds it.\n\n'
        f"{_context_block(chunks)}"
    )
    if focus:
        user += f"\n\nFocus on: {focus}"
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

    full_text, error_text = await llm.complete_json(messages)
    if full_text is None:
        raise provider_error_from_text(error_text)

    raw = _try_parse(full_text)
    questions = validate_questions(raw)
    if questions is None:
        messages.extend(
            [
                {"role": "assistant", "content": full_text},
                {"role": "user", "content": QUESTIONS_RETRY_NOTE},
            ]
        )
        retry_text, retry_error = await llm.complete_json(messages)
        if retry_text is None:
            raise provider_error_from_text(retry_error)
        if retry_text:
            questions = validate_questions(_try_parse(retry_text))
    if questions is None:
        questions = _questions_from_fragments(raw)
    return questions
