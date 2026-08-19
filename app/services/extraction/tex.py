from __future__ import annotations

import re

from app.services.extraction.base import (
    ExtractionResult,
    ExtractedUnit,
    LocationInfo,
    register,
    register_unavailable,
)

try:
    from pylatexenc.latex2text import LatexNodes2Text
    from pylatexenc.latexwalker import (
        LatexCommentNode,
        LatexEnvironmentNode,
        LatexGroupNode,
        LatexMacroNode,
        LatexWalker,
    )

    TEX_AVAILABLE = True
except ImportError:
    TEX_AVAILABLE = False

_SECTION_CMDS = {
    "part",
    "chapter",
    "section",
    "subsection",
    "subsubsection",
}

_ENV_UNITS = {
    "theorem",
    "definition",
    "example",
    "lemma",
    "proposition",
    "corollary",
    "proof",
    "remark",
}

_MATH_ENVS = {
    "equation",
    "equation*",
    "align",
    "align*",
    "alignat",
    "gather",
    "multline",
}

_ENV_NAMES = _ENV_UNITS | _MATH_ENVS

_OPTIONAL_BRACKETS = ("[", "]")

if not TEX_AVAILABLE:
    register_unavailable("tex", "pylatexenc")
else:

    @register("tex")
    class TexExtractor:
        def extract(self, data: bytes, *, filename: str) -> ExtractionResult:
            text = data.decode("utf-8", errors="replace")
            walker = LatexWalker(text)
            nodes = walker.get_latex_nodes()[0]
            macro_map = self._preamble_macros(nodes)
            body_nodes = self._body_nodes(nodes)
            if not body_nodes:
                return ExtractionResult(
                    error=(
                        "No \\begin{document} body could be found in this .tex file."
                    )
                )

            units: list[ExtractedUnit] = []
            current_section: str | None = None
            pending: list = []
            converter = LatexNodes2Text()

            def line_at(pos: int) -> int:
                return text.count("\n", 0, max(0, min(pos, len(text)))) + 1

            def flush() -> None:
                if not pending:
                    return
                raw = self._raw_text(pending)
                if raw.strip():
                    expanded = self._expand(raw, macro_map)
                    leading = len(raw) - len(raw.lstrip())
                    trailing = len(raw) - len(raw.rstrip())
                    base_pos = pending[0].pos
                    start_line = line_at(base_pos + leading)
                    end_line = line_at(base_pos + len(raw) - 1 - trailing)
                    units.append(
                        ExtractedUnit(
                            text=expanded,
                            unicode_text=converter.nodelist_to_text(
                                LatexWalker(expanded).get_latex_nodes()[0]
                            ),
                            location=LocationInfo(
                                kind="section",
                                section=current_section or "Document",
                                start_line=start_line,
                                end_line=end_line,
                            ),
                        )
                    )
                pending.clear()

            for node in body_nodes:
                if isinstance(node, LatexMacroNode) and node.macroname in _SECTION_CMDS:
                    flush()
                    title = self._arg_text(node)
                    current_section = self._expand(title, macro_map).strip()
                    units.append(
                        ExtractedUnit(
                            text=current_section or node.macroname,
                            unicode_text=converter.nodelist_to_text(
                                LatexWalker(current_section).get_latex_nodes()[0]
                            ),
                            location=LocationInfo(
                                kind="section",
                                section=current_section or node.macroname,
                                start_line=line_at(node.pos),
                                end_line=line_at(node.pos + (node.len or 0) - 1),
                            ),
                        )
                    )
                    continue
                if (
                    isinstance(node, LatexEnvironmentNode)
                    and node.environmentname in _ENV_NAMES
                ):
                    flush()
                    units.append(
                        self._environment_unit(
                            node, current_section, macro_map, converter, line_at
                        )
                    )
                    continue
                pending.append(node)

            flush()

            if not units:
                return ExtractionResult(error="No text content found in this file.")
            return ExtractionResult(units=units)

        def _environment_unit(
            self,
            node: LatexEnvironmentNode,
            section: str | None,
            macro_map: dict[str, str],
            converter: LatexNodes2Text,
            line_at,
        ) -> ExtractedUnit:
            name = node.environmentname
            if name.endswith("*"):
                name = name[:-1]
            raw = self._raw_text(node.nodelist)
            title = self._optional_title(node)
            if title:
                raw = f"[{self._expand(title, macro_map).strip()}]\n{raw}"
            expanded = self._expand(raw, macro_map)
            return ExtractedUnit(
                text=expanded,
                unicode_text=converter.nodelist_to_text(
                    LatexWalker(expanded).get_latex_nodes()[0]
                ),
                location=LocationInfo(
                    kind="lines",
                    section=section,
                    start_line=line_at(node.pos),
                    end_line=line_at(node.pos + (node.len or 0) - 1),
                    environment=name,
                    label=self._label(node),
                ),
            )

        @staticmethod
        def _body_nodes(nodes: list) -> list:
            for node in nodes:
                if (
                    isinstance(node, LatexEnvironmentNode)
                    and node.environmentname == "document"
                ):
                    return list(node.nodelist)
            return list(nodes)

        def _preamble_macros(self, nodes: list) -> dict[str, str]:
            macros: dict[str, str] = {}
            for index, node in enumerate(nodes):
                if (
                    isinstance(node, LatexEnvironmentNode)
                    and node.environmentname == "document"
                ):
                    break
                if not isinstance(node, LatexMacroNode):
                    continue
                name: str | None = None
                body: str | None = None
                if (
                    node.macroname == "newcommand"
                    and node.nodeargs
                    and len(node.nodeargs) >= 5
                ):
                    name_arg = node.nodeargs[1]
                    nargs_arg = node.nodeargs[2]
                    default_arg = node.nodeargs[3]
                    body_arg = node.nodeargs[4]
                    if (
                        name_arg is not None
                        and nargs_arg is None
                        and default_arg is None
                        and body_arg is not None
                    ):
                        name = self._strip_braces(name_arg.latex_verbatim())
                        body = self._strip_braces(body_arg.latex_verbatim())
                elif node.macroname == "def":
                    nxt = nodes[index + 1] if index + 1 < len(nodes) else None
                    nxt2 = nodes[index + 2] if index + 2 < len(nodes) else None
                    if isinstance(nxt, LatexMacroNode) and isinstance(
                        nxt2, LatexGroupNode
                    ):
                        name = "\\" + nxt.macroname
                        body = self._strip_braces(nxt2.latex_verbatim())
                elif (
                    node.macroname == "DeclareMathOperator"
                    and node.nodeargs
                    and len(node.nodeargs) >= 3
                ):
                    name_arg = node.nodeargs[1]
                    body_arg = node.nodeargs[2]
                    if name_arg is not None and body_arg is not None:
                        name = self._strip_braces(name_arg.latex_verbatim())
                        body = self._strip_braces(body_arg.latex_verbatim())
                if name and body:
                    macros[name.lstrip("\\")] = body
            return macros

        @staticmethod
        def _raw_text(nodes: list) -> str:
            return "".join(
                node.latex_verbatim()
                for node in nodes
                if not isinstance(node, LatexCommentNode)
                and not (isinstance(node, LatexMacroNode) and node.macroname == "label")
            )

        @staticmethod
        def _strip_braces(text: str) -> str:
            text = text.strip()
            if text.startswith("{") and text.endswith("}"):
                return text[1:-1].strip()
            return text

        @staticmethod
        def _expand(text: str, macro_map: dict[str, str]) -> str:
            for name, body in macro_map.items():
                text = re.sub(re.escape("\\" + name) + r"(?!\w)", lambda _m: body, text)
            return text

        @staticmethod
        def _arg_text(node: LatexMacroNode) -> str:
            for arg in node.nodeargs or []:
                if isinstance(arg, LatexGroupNode):
                    return "".join(
                        child.latex_verbatim()
                        for child in arg.nodelist
                        if not isinstance(child, LatexCommentNode)
                    )
            return node.macroname

        @staticmethod
        def _optional_title(node: LatexEnvironmentNode) -> str | None:
            args = getattr(node, "nodeargd", None)
            if args is None:
                return None
            for arg in args.argnlist:
                if (
                    isinstance(arg, LatexGroupNode)
                    and getattr(arg, "delimiters", None) == _OPTIONAL_BRACKETS
                ):
                    return "".join(
                        child.latex_verbatim()
                        for child in arg.nodelist
                        if not isinstance(child, LatexCommentNode)
                    )
            return None

        @staticmethod
        def _label(node: LatexEnvironmentNode) -> str | None:
            for child in node.nodelist:
                if (
                    isinstance(child, LatexMacroNode)
                    and child.macroname == "label"
                    and child.nodeargs
                ):
                    label_arg = child.nodeargs[0]
                    if isinstance(label_arg, LatexGroupNode):
                        return "".join(
                            part.latex_verbatim() for part in label_arg.nodelist
                        )
            return None
