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

# Qualified test name -> (hash of its code, names it uses, bare name); plus a hash of everything outside tests.
TestTable = tuple[dict[str, tuple[str, set[str], str]], str]


class Language(Protocol):
    name: str
    file_exts: tuple[str, ...]
    """Extensions of source files, with the dot; used to pick out code files from git history."""
    index_files: tuple[str, ...]
    """File names that stand for their directory (``__init__.py``) and are not named in docs."""
    default_skip: tuple[str, ...]
    default_server: tuple[str, ...]
    """Language server command; it must support call hierarchy."""

    def detect(self, root: Path) -> Layout:
        """Work out the layout from the directory alone; raises ``LayoutError`` when it cannot."""

    def extract(self, root: Path, layout: Layout) -> tuple[dict[str, Module], dict[str, Symbol]]:
        """Parse every source file under ``layout.pkg_dir``; returns (modules, symbols) keyed by id."""

    def test_files(self, root: Path, layout: Layout) -> list[str]:
        """Test source files under ``layout.tests``, relative to ``root``."""

    def test_uses(self, path: Path) -> list[tuple[str, set[str]]] | None:
        """(test name, names it uses) for each test in a file, or None when the file does not parse."""

    def test_table(self, path: Path) -> TestTable:
        """Per-test hashes and used names, for finding which tests changed between two trees."""

    def coarse_names(self, path: Path) -> set[str]:
        """Every name a file mentions; the fallback when a change cannot be pinned to single tests."""


_REGISTRY: dict[str, Language] = {}


def register(lang: Language) -> None:
    _REGISTRY[lang.name] = lang


def get_language(name: str) -> Language:
    if not _REGISTRY:
        from . import python  # noqa: F401  (registers itself)
    try:
        return _REGISTRY[name]
    except KeyError:
        raise ValueError(f"unknown language {name!r}; known: {', '.join(sorted(_REGISTRY))}") from None


def language_names() -> list[str]:
    get_language("python")
    return sorted(_REGISTRY)
