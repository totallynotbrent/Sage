"""Extraction registry: units, results, extractor protocol, dispatch.

Handlers register themselves by file extension. A handler whose backing library
is missing at import time registers an *unavailable* marker instead, so
``get_extractor`` raises an actionable ``ExtractionError`` ("missing
dependency") rather than ever producing a traceback at dispatch time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol

from app.errors import ExtractionError, UnsupportedFormatError

LocationKind = Literal["page", "slide", "section", "lines"]


@dataclass
class LocationInfo:
    """Where an extracted unit came from within its source file."""

    kind: LocationKind = "lines"
    page: int | None = None
    slide: int | None = None
    section: str | None = None
    start_line: int | None = None
    end_line: int | None = None

    def to_dict(self) -> dict:
        return {
            "location_kind": self.kind,
            "page": self.page,
            "slide": self.slide,
            "section": self.section,
            "start_line": self.start_line,
            "end_line": self.end_line,
        }


@dataclass
class ExtractedUnit:
    """A contiguous run of text with source metadata."""

    text: str
    location: LocationInfo | None = None


@dataclass
class ExtractionResult:
    """The outcome of extracting one file."""

    units: list[ExtractedUnit] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


class Extractor(Protocol):
    """Protocol implemented by every format handler."""

    def extract(self, data: bytes, *, filename: str) -> ExtractionResult:
        """Extract text units from ``data``; never raise."""


class MissingDependencyExtractor:
    """Placeholder registered when a handler's library cannot be imported."""

    def __init__(self, dependency: str, extension: str) -> None:
        self.dependency = dependency
        self.extension = extension

    def extract(self, data: bytes, *, filename: str) -> ExtractionResult:
        return ExtractionResult(
            error=(
                f"Cannot extract .{self.extension}: the {self.dependency} library "
                f"is not installed. Install it with `pip install {self.dependency}`."
            )
        )


EXTRACTORS: dict[str, Extractor] = {}


def register(extension: str):
    """Decorator registering an ``Extractor`` class for ``extension``."""

    def _decorator(cls: type[Extractor]) -> type[Extractor]:
        instance = cls()
        EXTRACTORS[extension.lower().lstrip(".")] = instance
        return cls

    return _decorator


def register_unavailable(extension: str, dependency: str) -> None:
    """Register an extension as unextractable until its library is installed."""
    EXTRACTORS[extension.lower().lstrip(".")] = MissingDependencyExtractor(
        dependency, extension.lower().lstrip(".")
    )


def get_extractor(extension: str) -> Extractor:
    """Return the extractor for ``extension`` or raise an actionable error.

    - Unknown extension           -> UnsupportedFormatError (415)
    - Known but missing library   -> ExtractionError (422, actionable)
    """
    key = extension.lower().lstrip(".")
    extractor = EXTRACTORS.get(key)
    if extractor is None:
        raise UnsupportedFormatError(
            f"Unsupported file format: .{key}. Supported: "
            + ", ".join(sorted(EXTRACTORS)) + "."
        )
    if isinstance(extractor, MissingDependencyExtractor):
        raise ExtractionError(
            extractor.extract(b"", filename="").error or f"Unavailable extractor for .{key}"
        )
    return extractor
