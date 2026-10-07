"""Language plugins: everything that knows how one language lays out, parses and tests code.

The stages after extraction (call graph over a language server, doc links, git facts, the page) work on
the language-neutral ``Module`` and ``Symbol`` dicts and ask the plugin for the few things that differ.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from ..model import Module, Symbol

if TYPE_CHECKING:
    from ..layout import Layout


class LanguageToolError(RuntimeError):
    """A tool a language plugin needs (parser helper, language server, toolchain) is missing or failed."""


# Qualified test name -> (hash of its code, names it uses, bare name); plus a hash of everything outside tests.
TestTable = tuple[dict[str, tuple[str, set[str], str]], str]


class Language(Protocol):
    name: str
    file_exts: tuple[str, ...]
    """Extensions of source files, with the dot; used to pick out code files from git history."""
    index_files: tuple[str, ...]
    """File names that stand for their directory (``__init__.py``) and are not named in docs."""
    default_skip: tuple[str, ...]

    @property
    def default_server(self) -> tuple[str, ...]:
        """Language server command; it must support call hierarchy."""

    def require(self, lsp: bool) -> None:
        """Raise ``LanguageToolError`` now if what a build needs is missing (the language server only if ``lsp``)."""

    def is_test_path(self, rel: str) -> bool:
        """Whether a file is a test by its name alone (``x_test.go``); directories of tests are in the layout."""

    def detect(self, root: Path) -> Layout:
        """Work out the layout from the directory alone; raises ``LayoutError`` when it cannot."""

    def extract(self, root: Path, layout: Layout) -> tuple[dict[str, Module], dict[str, Symbol]]:
        """Parse every source file under ``layout.pkg_dir``; returns (modules, symbols) keyed by id."""

    def test_files(self, root: Path, layout: Layout) -> list[str]:
        """Test source files under ``layout.tests``, relative to ``root``."""

    def test_uses(self, root: Path, rels: list[str]) -> dict[str, list[tuple[str, set[str]]] | None]:
        """For each test file: (test name, names it uses) per test, or None when the file does not parse."""

    def test_table(self, path: Path) -> TestTable:
        """Per-test hashes and used names, for finding which tests changed between two trees."""

    def coarse_names(self, path: Path) -> set[str]:
        """Every name a file mentions; the fallback when a change cannot be pinned to single tests."""


_REGISTRY: dict[str, Language] = {}
_BUILTIN = ("python", "go")


def register(lang: Language) -> None:
    _REGISTRY[lang.name] = lang


def _load_builtin() -> None:
    import importlib

    for name in _BUILTIN:
        if name not in _REGISTRY:
            importlib.import_module(f"{__name__}.{name}")  # the module registers itself


def get_language(name: str) -> Language:
    _load_builtin()
    try:
        return _REGISTRY[name]
    except KeyError:
        raise ValueError(f"unknown language {name!r}; known: {', '.join(sorted(_REGISTRY))}") from None


def language_names() -> list[str]:
    _load_builtin()
    return sorted(_REGISTRY)
