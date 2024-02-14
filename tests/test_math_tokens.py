from __future__ import annotations

from app.services.math_tokens import (
    _GREEK_NAMES,
    _LATEX_MATH,
    _UNICODE_MATH,
    tokenize_math,
)
from app.services.retrieval import STOPWORDS


def test_greek_name_map():
    assert tokenize_math("α β γ δ ω") == ["alpha", "beta", "gamma", "delta", "omega"]
    assert tokenize_math("Σ") == ["sigma"]
    assert tokenize_math("no greek here") == []


def test_latex_symbol_map():
    assert tokenize_math(r"\alpha \int \sum \frac \sqrt \partial") == [
        "alpha",
        "integral",
        "sum",
        "frac",
        "sqrt",
        "partial",
    ]


def test_unicode_symbol_map():
    assert tokenize_math("∫ ∑ √ ∂ ∈ ≤ ≥ ≠ ∞ →") == [
        "integral",
        "sum",
        "sqrt",
        "partial",
        "member_of",
        "le",
        "ge",
        "ne",
        "infinity",
        "maps_to",
    ]


def test_in_and_to_never_stopwords():
    assert tokenize_math(r"\in") == ["member_of"]
    assert tokenize_math(r"\to") == ["maps_to"]
    assert tokenize_math("∈") == ["member_of"]
    assert tokenize_math("→") == ["maps_to"]


def test_unknown_commands_fall_through_to_name():
    assert tokenize_math(r"\mathbb") == ["mathbb"]
    assert tokenize_math(r"\operatorname") == ["operatorname"]


def test_empty_and_plain_text():
    assert tokenize_math("") == []
    assert tokenize_math("plain prose words only") == []


def test_all_canonicals_collision_free_with_stopwords():
    canonicals = set(_GREEK_NAMES.values())
    canonicals.update(_LATEX_MATH.values())
    canonicals.update(_UNICODE_MATH.values())
    assert canonicals.isdisjoint(STOPWORDS), canonicals & STOPWORDS


def test_greek_commands_match_unicode_greek():
    assert tokenize_math(r"\alpha") == tokenize_math("α")
    assert tokenize_math(r"\omega") == tokenize_math("ω")
