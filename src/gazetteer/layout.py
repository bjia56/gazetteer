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
    """

    pkg_dir: str
    module_root: str
    import_prefix: str
    tests: tuple[str, ...] = ()
    skip: tuple[str, ...] = DEFAULT_SKIP
    docs_dir: str = "docs"

    def is_test(self, rel: str) -> bool:
        return any(rel == t or rel.startswith(t.rstrip("/") + "/") for t in self.tests)

    def is_skipped(self, rel: str) -> bool:
        return any(s in f"/{rel}" for s in self.skip)

    @classmethod
    def detect(cls, root: Path) -> Layout:
        """Find the one importable package under ``root`` (``src/<pkg>`` or ``<pkg>``)."""
        root = Path(root)
        tests = tuple(d for d in TEST_DIR_NAMES if (root / d).is_dir())
        found: list[tuple[str, str, str]] = []
        src = root / "src"
        if src.is_dir():
            found = [
                (f"src/{d.name}", "src", d.name)
                for d in sorted(src.iterdir())
                if d.is_dir() and (d / "__init__.py").exists()
            ]
        if not found:
            found = [
                (d.name, ".", d.name)
                for d in sorted(root.iterdir())
                if d.is_dir()
                and (d / "__init__.py").exists()
                and d.name not in TEST_DIR_NAMES
                and not d.name.startswith((".", "_"))
            ]
        if not found:
            raise LayoutError(
                f"no Python package found under {root}; pass --pkg-dir, --module-root and --import-prefix"
            )
        if len(found) > 1:
            names = ", ".join(f[2] for f in found)
            raise LayoutError(f"several packages found ({names}); pass --pkg-dir to choose one")
        pkg_dir, module_root, prefix = found[0]
        return cls(pkg_dir=pkg_dir, module_root=module_root, import_prefix=prefix, tests=tests)
