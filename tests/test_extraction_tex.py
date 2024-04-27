from __future__ import annotations

import pytest

SRC_SECTIONS = r"""\documentclass{article}
\begin{document}
\section{First Section}
Some prose under the first section.

\subsection{Sub One}
Deeper content here.

\section{Second Section}
More prose.
\end{document}
"""

SRC_THEOREM = r"""\documentclass{article}
\begin{document}
\section{Calculus}
\begin{theorem}[Rolle]
If $f$ is continuous then $\int_a^b f(x)\,dx$ exists and $\alpha > 0$.
\label{thm:rolle}
\end{theorem}
\end{document}
"""

SRC_EQUATION = r"""\documentclass{article}
\begin{document}
\begin{equation}
\int_0^1 x^2 \, dx = \frac{1}{3}
\label{eq:euler}
\end{equation}
\end{document}
"""

SRC_VERB = r"""\documentclass{article}
\begin{document}
\section{Tough}
% a comment { with unbalanced brace
Text with \verb|a_b{c}| and ${a+b}$ and nested {braces {inside}}.
% another comment }
\end{document}
"""

SRC_MACRO = r"""\documentclass{article}
\newcommand{\R}{\mathbb{R}}
\def\half{\frac{1}{2}}
\DeclareMathOperator{\diam}{diam}
\begin{document}
\section{Reals}
The reals are \R, half is \half, and the diameter is \diam.
\end{document}
"""

SRC_ALIGN = r"""\documentclass{article}
\usepackage{amsmath}
\begin{document}
\begin{align}
a &= b + c \\
d &= e + f
\end{align}
\end{document}
"""


def _extract(source: str):
    pytest.importorskip("pylatexenc")
    from app.services.extraction.tex import TexExtractor

    return TexExtractor().extract(source.encode("utf-8"), filename="notes.tex")


def test_sections_and_prose_units():
    result = _extract(SRC_SECTIONS)
    assert result.ok
    sections = [u.location.section for u in result.units]
    assert sections == [
        "First Section",
        "First Section",
        "Sub One",
        "Sub One",
        "Second Section",
        "Second Section",
    ]
    assert result.units[0].location.kind == "section"
    assert result.units[1].text.strip().startswith("Some prose")
    assert result.units[1].location.start_line == 4
    assert result.units[1].location.end_line == 4
    assert result.units[2].text == "Sub One"
    assert result.units[3].location.section == "Sub One"
    assert result.units[3].location.start_line == 7
    assert result.units[5].location.start_line == 10


def test_no_preamble_unit():
    result = _extract(SRC_MACRO)
    assert result.ok
    combined = "\n".join(u.text for u in result.units)
    assert "newcommand" not in combined
    assert "documentclass" not in combined


def test_theorem_environment_with_title():
    result = _extract(SRC_THEOREM)
    assert result.ok
    theorem = next(u for u in result.units if u.location.environment == "theorem")
    assert theorem.location.section == "Calculus"
    assert theorem.location.label == "thm:rolle"
    assert theorem.location.kind == "lines"
    assert "[Rolle]" in theorem.text
    assert theorem.location.start_line == 4
    assert theorem.location.end_line == 7
    assert "\\label" not in theorem.text


def test_equation_environment_label_and_unicode():
    result = _extract(SRC_EQUATION)
    assert result.ok
    equation = next(u for u in result.units if u.location.environment == "equation")
    assert equation.location.label == "eq:euler"
    assert "\\int" in equation.text
    assert "\\label" not in equation.text
    assert equation.unicode_text is not None
    assert "∫" in equation.unicode_text
    assert "1/3" in equation.unicode_text


def test_comments_verbatim_and_nested_braces_robust():
    result = _extract(SRC_VERB)
    assert result.ok
    assert any("%" not in u.text for u in result.units if u.text.strip())
    prose = next(u for u in result.units if "Text with" in u.text)
    assert "a_b{c}" in prose.text
    assert "a+b" in prose.unicode_text


def test_macro_expansion_visible_in_text():
    result = _extract(SRC_MACRO)
    assert result.ok
    prose = next(u for u in result.units if "reals are" in u.text)
    assert "\\mathbb{R}" in prose.text
    assert "\\R" not in prose.text
    assert "\\frac{1}{2}" in prose.text
    assert "diam" in prose.text
    assert prose.unicode_text is not None
    assert "ℝ" in prose.unicode_text


def test_align_environment_multiline():
    result = _extract(SRC_ALIGN)
    assert result.ok
    align = next(u for u in result.units if u.location.environment == "align")
    assert "a &= b + c" in align.text
    assert "d &= e + f" in align.text
    assert align.unicode_text is not None


def test_unicode_renders_alpha_and_integral():
    result = _extract(SRC_THEOREM)
    assert result.ok
    theorem = next(u for u in result.units if u.location.environment == "theorem")
    assert "∫" in theorem.unicode_text
    assert "f" in theorem.unicode_text


def test_empty_document_errors():
    result = _extract(r"""\documentclass{article}
\begin{document}
\end{document}
""")
    assert not result.ok
    assert "no text" in result.error.lower()


def test_get_extractor_dispatches_tex():
    pytest.importorskip("pylatexenc")
    from app.services.extraction.base import get_extractor

    extractor = get_extractor("tex")
    result = extractor.extract(SRC_SECTIONS.encode("utf-8"), filename="n.tex")
    assert result.ok
    assert len(result.units) >= 2


def test_no_document_environment_still_extracts():
    result = _extract(r"""\section{Loose}
Content without a document wrapper.
""")
    assert result.ok
    assert result.units[0].text == "Loose"
    assert result.units[1].location.section == "Loose"
