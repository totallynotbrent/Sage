"""Extraction handlers.

Handlers import their backing libraries lazily inside guarded ``try/except``
blocks; a missing library registers an unavailable extractor so dispatch stays
actionable instead of raising ImportError.
"""

from __future__ import annotations

from .base import EXTRACTORS, ExtractionResult, ExtractedUnit, Extractor, LocationInfo, get_extractor, register, register_unavailable  # noqa: F401

try:
    from . import text  # noqa: F401
except Exception:  # noqa: BLE001 - never let handler import failures break the app
    pass

try:
    from . import markdown  # noqa: F401
except Exception:  # noqa: BLE001
    pass

try:
    from . import pdf  # noqa: F401
except Exception:  # noqa: BLE001
    register_unavailable("pdf", "pymupdf")

try:
    from . import docx  # noqa: F401
except Exception:  # noqa: BLE001
    register_unavailable("docx", "python-docx")

try:
    from . import pptx  # noqa: F401
except Exception:  # noqa: BLE001
    register_unavailable("pptx", "python-pptx")
