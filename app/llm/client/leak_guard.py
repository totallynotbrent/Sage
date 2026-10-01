"""Strip leaked tool-call text gemma writes alongside structured tool_calls."""
import re

# Leaked tool-call text some models emit as visible content even though they
# also emitted structured tool_calls (e.g. "Please pick ... call:run_probe/").
# Bread's model doesn't leak, but some smaller served models do. Strip it only
# from chunks that actually carry tool_calls, so normal prose mentioning
# "call:" is preserved.
_LEAKED_CALL_RE = re.compile(r"<call:\w+\b[^>]*>?")
_LEAKED_BARE_RE = re.compile(
    r"(?:(?<=\s)|(?<=^)|(?<=[\n\r\t.:;,!?)(\\\"'-]))"
    r"\[?call:\w+\b/?\]?"
    r"(?:\s*status\s*=\s*[\"'][^\"']*[\"'])?"
    r"(?:\s*\((?:[^()\"']|\"[^\"]*\"|'[^']*')*\))?"
)
_LEAKED_OPEN_RE = re.compile(r"<call:\w+\b[^<]*$")
# full call-signature form some models emit as prose: tool_name(args=..., ...)
# or tool_name: {actions: [...]}; only strip when the name is a real sage tool
_SAGE_TOOL_NAMES = (
    "run_probe", "grade_answer", "build_plan", "run_final_quiz",
    "record_step_actions", "start_review", "web_search", "generate_quiz",
    "generate_todo", "generate_latex", "ask_question",
)
_LEAKED_SIGNATURE_RE = re.compile(
    r"(?:(?<=\s)|(?<=^)|(?<=[\n\r\t]))"
    r"(?:(" + "|".join(_SAGE_TOOL_NAMES) + r"))"
    r"(?:\s*\((?:[^()\"']|\"[^\"]*\"|'[^']*'|\([^()\"']*\))*\)"
    r"|\s*:\s*\{(?:[^{}\"']|\"[^\"]*\"|'[^']*'|\{(?:[^{}\"']|\"[^\"]*\"|'[^']*')*\})*\})"
    r"(?:\s*status\s*=\s*[\"'][^\"']*[\"'])?",
    re.MULTILINE,
)


def _strip_leaked_calls(text: str) -> str:
    if not text:
        return text
    lowered = text.lower()
    if "call:" not in lowered and not any(
        (name + "(" in lowered or name + ": {" in lowered or name + ":{" in lowered)
        for name in _SAGE_TOOL_NAMES
    ):
        return text
    cleaned = _LEAKED_CALL_RE.sub("", text)
    cleaned = _LEAKED_BARE_RE.sub("", cleaned)
    cleaned = _LEAKED_OPEN_RE.sub("", cleaned)
    cleaned = _LEAKED_SIGNATURE_RE.sub("", cleaned)
    return cleaned
