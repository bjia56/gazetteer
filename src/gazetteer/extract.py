"""Static facts from the source: modules, classes, functions, signatures, docstrings and imports.

The parsing itself lives in the language plugins; this module picks the plugin for a layout.
"""

from __future__ import annotations

from pathlib import Path

from .languages import get_language
from .languages.python import FUNCTION_NODES as FUNCTION_NODES  # re-exported for callers that predate plugins
from .layout import Layout
from .model import Module as Module
from .model import Symbol as Symbol


def extract_modules(root: Path, layout: Layout) -> tuple[dict[str, Module], dict[str, Symbol]]:
    """Parse every source file under ``layout.pkg_dir``; returns (modules, symbols) keyed by id."""
    return get_language(layout.lang).extract(root, layout)
