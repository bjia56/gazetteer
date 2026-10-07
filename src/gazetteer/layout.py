"""Where the code, tests and docs live in the repository being documented."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

DEFAULT_SKIP = (
    "__pycache__",
    "/.venv/",
    "/venv/",
    "/site-packages/",
    "/node_modules/",
    "_pb2.py",
    "_pb2_grpc.py",
)
TEST_DIR_NAMES = ("tests", "test")


class LayoutError(ValueError):
    """The repository layout could not be worked out from the directory alone."""


@dataclass(frozen=True)
class Layout:
    """Paths are relative to the repository root.

    pkg_dir:       directory whose Python files are documented.
    module_root:   directory module names are relative to (``src`` for a src layout, ``.`` for a flat one).
    import_prefix: imports starting with this name are internal ("" treats every import as a candidate).
    tests:         directories holding tests; they are linked to code, not documented.
    skip:          path substrings to leave out (matched against ``/<relative path>``).
    docs_dir:      directory of markdown docs to cross-link with the code.
    lang:          name of the language plugin that reads this tree.
    """

    pkg_dir: str
    module_root: str
    import_prefix: str
    tests: tuple[str, ...] = ()
    skip: tuple[str, ...] = DEFAULT_SKIP
    docs_dir: str = "docs"
    lang: str = "python"

    def is_test(self, rel: str) -> bool:
        return any(rel == t or rel.startswith(t.rstrip("/") + "/") for t in self.tests)

    def is_skipped(self, rel: str) -> bool:
        return any(s in f"/{rel}" for s in self.skip)

    @classmethod
    def detect(cls, root: Path, lang: str = "python") -> Layout:
        """Work out the layout of ``root`` for one language; raises ``LayoutError`` when it cannot."""
        from .languages import get_language

        return get_language(lang).detect(Path(root))
