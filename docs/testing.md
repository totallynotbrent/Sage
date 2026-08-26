# Testing

## Test suite

```bash
python -m pytest tests/ -q
```

All tests run offline (no live model required); the LLM is faked. The
extraction tests generate tiny PDF/DOCX/PPTX fixtures on the fly and skip
gracefully if a library is unavailable; the tex extractor tests skip when
`pylatexenc` is not installed (`pytest.importorskip`). The suite also covers
the math-aware tokenizer, the notes folder watcher (`tests/test_watcher.py`),
and the notes quiz (`tests/test_notes_quiz.py`, including the answerability
drop gate with a scripted fake LLM).

## Preflight wheel check

```bash
python tools/check_wheels.py
```

Downloads every requirement without dependencies and — on Linux — verifies an
ARM64/aarch64 or `any` wheel exists for each package. A package without an
ARM64 Python 3.11 wheel fails fast here with an actionable message rather than
as a mid-session runtime traceback.

## Live endpoint smoke test

```bash
python tools/smoke_chat.py
```

Sends a minimal request to the configured endpoint (requires a running model
and a valid key). Not part of the test suite.
