"""Where the code, tests and docs live in the repository being documented."""

from __future__ import annotations

from dataclasses import dataclass, replace
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


def join_path(prefix: str, rel: str) -> str:
    """``rel`` below the directory ``prefix`` (both posix, ``.`` is the current directory)."""
    if prefix in ("", "."):
        return rel
    return prefix if rel in ("", ".") else f"{prefix}/{rel}"


@dataclass(frozen=True)
class Layout:
    """Paths are relative to the directory of the unit being documented (the repository root for a plain build).

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

    def under(self, path: str) -> Layout:
        """The same layout with every path made relative to the directory that contains ``path``."""
        return replace(
            self,
            pkg_dir=join_path(path, self.pkg_dir),
            module_root=join_path(path, self.module_root),
            tests=tuple(join_path(path, t) for t in self.tests),
            docs_dir=join_path(path, self.docs_dir),
        )

    @classmethod
    def resolve(
        cls,
        root: Path,
        lang: str = "python",
        *,
        pkg_dir: str | None = None,
        module_root: str | None = None,
        import_prefix: str | None = None,
        tests: tuple[str, ...] | None = None,
        skip: tuple[str, ...] | None = None,
        docs_dir: str | None = None,
    ) -> Layout:
        """Detect the layout of ``root`` and apply explicit overrides; nothing is detected if all three of
        ``pkg_dir``, ``module_root`` and ``import_prefix`` are given."""
        if pkg_dir and module_root is not None and import_prefix is not None:
            base = cls(pkg_dir=pkg_dir, module_root=module_root, import_prefix=import_prefix, lang=lang)
        else:
            found = cls.detect(root, lang)
            base = cls(
                pkg_dir=pkg_dir or found.pkg_dir,
                module_root=module_root if module_root is not None else found.module_root,
                import_prefix=import_prefix if import_prefix is not None else found.import_prefix,
                tests=found.tests,
                lang=lang,
            )
        updates: dict[str, object] = {}
        if tests is not None:
            updates["tests"] = tests
        if skip is not None:
            updates["skip"] = skip
        if docs_dir is not None:
            updates["docs_dir"] = docs_dir
        return replace(base, **updates)  # type: ignore[arg-type]

    @classmethod
    def detect(cls, root: Path, lang: str = "python") -> Layout:
        """Work out the layout of ``root`` for one language; raises ``LayoutError`` when it cannot."""
        from .languages import get_language

        return get_language(lang).detect(Path(root))
