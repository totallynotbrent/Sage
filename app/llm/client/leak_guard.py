"""Strip leaked tool-call text gemma writes alongside structured tool_calls."""
import re

# Leaked tool-call text some models emit as visible content even though they
# also emitted structured tool_calls (e.g. "Please pick ... call:run_probe/").
# Bread's model doesn't leak, but Sage's gemma4:31b-cloud does. Strip it only
# from chunks that actually carry tool_calls, so normal prose mentioning
# "call:" is preserved.
_LEAKED_CALL_RE = re.compile(r"<call:\w+\b[^>]*>?")
_LEAKED_BARE_RE = re.compile(
    r"(?:(?<=\s)|(?<=^)|(?<=[\n\r\t.:;,!?)(\\\"'-]))"
    r"\[?call:\w+\b/?\]?"
    r"(?:\s*status\s*=\s*[\"'][^\"']*[\"'])?"
    r"(?:\s*\([^)\"']*\))?"
)
_LEAKED_OPEN_RE = re.compile(r"<call:\w+\b[^<]*$")


def _strip_leaked_calls(text: str) -> str:
    if not text or "call:" not in text.lower():
        return text
    cleaned = _LEAKED_CALL_RE.sub("", text)
    cleaned = _LEAKED_BARE_RE.sub("", cleaned)
    return _LEAKED_OPEN_RE.sub("", cleaned)
