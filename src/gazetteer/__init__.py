"""gazetteer: static code docs generator for Python repositories."""

from __future__ import annotations

from .build import BuildOptions, build
from .layout import Layout
from .pack import pack

__version__ = "0.1.0"
__all__ = ["BuildOptions", "Layout", "build", "pack", "__version__"]
