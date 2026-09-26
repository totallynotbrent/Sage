from __future__ import annotations

import json
import re
from typing import Any

from app.errors import ModelOutputError, ProviderError, PROVIDER_STATUS
from app.llm.messages import chunk_block, make_system_prompt
from app.models import Plan, PlanNode, QuizQuestionInput

PLAN_RETRY_NOTE = (
    "Your previous answer could not be parsed. Return ONLY a JSON object with a "
    '"nodes" array; each node must have "node_key", "title", "description", and '
    '"depends_on" (an array of node_key strings that exist in the same plan).'
)
QUESTIONS_RETRY_NOTE = (
    "Your previous answer could not be parsed. Return ONLY a JSON array of "
    'question objects; each must have "question" (string), "options" (array of '
    '2-6 strings), "correct_index" (integer index of the correct option), '
    '"explanation" (string), "topic" (string), and "difficulty" (integer 1-5).'
)


def provider_error_from_text(error_text: str | None) -> ProviderError:
    if not error_text:
        return ProviderError(
            "upstream", detail="The model endpoint returned no response."
        )
    code, sep, rest = error_text.partition(": ")
    if not sep or code not in PROVIDER_STATUS:
        return ProviderError("upstream", detail=error_text)
    return ProviderError(code, detail=rest or None)


def parse_json(text: str) -> Any:
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
    if isinstance(raw, dict):
        raw = raw.get("questions")
    if not isinstance(raw, list) or not raw:
        return None
    result = [_valid_question(item) for item in raw]
    if any(q is None for q in result):
        return None
    return result  # type: ignore[return-value]


def _questions_from_fragments(raw: Any) -> list[QuizQuestionInput]:
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
    # last-resort repair for a verbose model: keep any well-formed nodes but
    # preserve their depends_on and still require a resolvable graph, else a
    # cyclic or dangling plan sneaks in ungated
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
            depends_on = item.get("depends_on") or []
            if not isinstance(depends_on, list) or not all(
                isinstance(d, str) and d for d in depends_on
            ):
                depends_on = []
            nodes.append(
                PlanNode(
                    node_key=node_key,
                    title=title.strip(),
                    description=item.get("description")
                    if isinstance(item.get("description"), str)
                    else None,
                    depends_on=depends_on,
                    position=index,
                )
            )
            seen.add(node_key)
    if not nodes:
        return None
    plan = Plan(nodes=nodes)
    return plan if plan.validate_dependencies() else None


def _context_block(chunks: list[dict[str, Any]]) -> str:
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
    outline: list[dict] | None = None,
    lightweight: bool = False,
) -> Plan | None:
    system = make_system_prompt(session, mode, mastery_summary, lightweight=lightweight)
    user = (
        "Based on the learner goal and the source excerpts, propose a "
        "dependency-aware learning plan. Return ONLY a JSON object:\n"
        '{"nodes": [{"node_key": "k1", "title": "...", "description": "...", '
        '"depends_on": ["k0"]}]}\n'
        "depends_on must reference node_keys that exist in the same plan.\n\n"
        f"{_context_block(chunks)}"
    )
    if outline:
        titles = [
            entry["title"]
            for entry in outline
            if isinstance(entry.get("title"), str) and entry["title"].strip()
        ]
        if titles:
            user += (
                "\n\nStrict mode: build the plan strictly from the document's "
                "own sections below. Use those section titles as the node topics, "
                "in the order they appear, and do not add external topics.\n"
                "Document sections:\n- " + "\n- ".join(titles)
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
    avoid: list[str] | None = None,
    lightweight: bool = False,
) -> list[QuizQuestionInput]:
    system = make_system_prompt(session, mode, mastery_summary, lightweight=lightweight)
    user = (
        f"Write {count} multiple-choice questions relevant to the learner goal "
        "and the source excerpts. Vary difficulty. Return ONLY a JSON array of "
        "question objects, each with: question (string), options (array of 2-6 "
        "strings), correct_index (integer index into options), explanation "
        "(string), topic (string), difficulty (integer 1-5). Do not include an "
        '"I don\'t know" option; the server adds it.\n\n'
        f"{_context_block(chunks)}"
    )
    if focus:
        user += f"\n\nFocus on: {focus}"
    if avoid:
        user += (
            "\n\nDo NOT repeat or closely rephrase any of these questions that "
            "were already asked in this session. Write fresh questions with "
            "different phrasing and different distractors:\n"
            + "\n".join(f"- {stem[:180]}" for stem in avoid)
        )
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


LEARNER_REVIEW_RETRY_NOTE = (
    "Your previous answer could not be parsed. Return ONLY a JSON array, one "
    "object per learner question, each with: question (string, the learner's "
    "question verbatim), answer (string), coverage (one of hit/partial/miss), "
    "feedback (string)."
)


def _validate_learner_review(raw: Any) -> list[dict] | None:
    if not isinstance(raw, list) or not raw:
        return None
    items = []
    for entry in raw:
        if not isinstance(entry, dict):
            return None
        coverage = str(entry.get("coverage") or "partial")
        if coverage not in ("hit", "partial", "miss"):
            coverage = "partial"
        items.append(
            {
                "question": str(entry.get("question") or "").strip(),
                "answer": str(entry.get("answer") or "").strip(),
                "coverage": coverage,
                "feedback": str(entry.get("feedback") or "").strip(),
            }
        )
    if not items or any(not item["question"] for item in items):
        return None
    return items


async def request_learner_review(
    llm,
    *,
    session: dict[str, Any],
    chunks: list[dict[str, Any]],
    mastery_summary: str,
    mode: str,
    questions: list[str],
    topic: str | None = None,
    lightweight: bool = False,
) -> list[dict]:
    """Sage answers the learner's own questions and grades fact coverage."""
    system = make_system_prompt(session, mode, mastery_summary, lightweight=lightweight)
    bullet = "\n".join(f"{i + 1}. {q}" for i, q in enumerate(questions))
    user = (
        "The learner just studied the material and wrote TWO questions of their "
        "own over it. For each, (1) answer it accurately and (2) grade whether it "
        "hits the key facts of the material. Return ONLY a JSON array, one object "
        "per learner question, each with: question (the learner's question "
        "verbatim), answer (string), coverage (exactly one of: hit / partial / "
        "miss: hit if it targets a key fact, partial if it touches it loosely, "
        "miss if it's off-topic or trivial), feedback (string, 1-2 sentences "
        "encouraging what was good and what a stronger question would probe).\n\n"
        f"Learner questions:\n{bullet}\n\n"
        f"{_context_block(chunks)}"
    )
    if topic:
        user += f"\n\nTopic just covered: {topic}"
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    full_text, error_text = await llm.complete_json(messages)
    if full_text is None:
        raise provider_error_from_text(error_text)
    raw = _try_parse(full_text)
    review = _validate_learner_review(raw)
    if review is None:
        messages.extend(
            [
                {"role": "assistant", "content": full_text},
                {"role": "user", "content": LEARNER_REVIEW_RETRY_NOTE},
            ]
        )
        retry_text, retry_error = await llm.complete_json(messages)
        if retry_text is None:
            raise provider_error_from_text(retry_error)
        review = _validate_learner_review(_try_parse(retry_text))
    if review is None:
        error = ModelOutputError("The model returned no usable learner-question review.")
        error.retryable = True
        raise error
    return review
