"""Shared fixtures: a sample project and a language-server stand-in that resolves calls by name."""

from __future__ import annotations

import ast
import collections
import shutil
import time
from pathlib import Path

import pytest

from gazetteer.callgraph import TESTS_PER_FILE
from gazetteer.extract import FUNCTION_NODES, Module, Symbol
from gazetteer.layout import Layout

SAMPLE = Path(__file__).parent / "fixtures" / "sample"


class NameQuerier:
    """Stands in for the language server: a call to ``f`` or ``.f`` reaches every symbol named ``f``."""

    instances: list[NameQuerier] = []

    def __init__(
        self, root: Path, layout: Layout, cmd: tuple[str, ...], modules: dict[str, Module], symbols: dict[str, Symbol]
    ) -> None:
        self.root, self.layout, self.symbols = root, layout, symbols
        self.requests = 0
        self.timeouts = 0
        self.init_seconds = 0.0
        self.queried: list[str] = []
        self.callers: dict[str, set[str]] = collections.defaultdict(set)
        self.test_calls: dict[str, dict[str, set[str]]] = collections.defaultdict(lambda: collections.defaultdict(set))
        by_name: dict[str, list[str]] = collections.defaultdict(list)
        for s in symbols.values():
            by_name[s["name"]].append(s["id"])
        self.by_name = by_name
        self.called_by: dict[str, set[str]] = collections.defaultdict(set)
        for s in symbols.values():
            tree = ast.parse((root / s["file"]).read_text())
            for node in ast.walk(tree):
                if isinstance(node, FUNCTION_NODES) and node.lineno == s["line"]:
                    for n in self._calls(node):
                        for tid in by_name.get(n, []):
                            self.called_by[tid].add(s["id"])
        for t in layout.tests:
            for p in (root / t).rglob("*.py"):
                rel = p.relative_to(root).as_posix()
                for node in ast.walk(ast.parse(p.read_text())):
                    if isinstance(node, FUNCTION_NODES):
                        for n in self._calls(node):
                            for tid in by_name.get(n, []):
                                self.test_calls[tid][rel].add(node.name)
        NameQuerier.instances.append(self)

    @staticmethod
    def _calls(fn: ast.AST) -> set[str]:
        out: set[str] = set()
        for n in ast.walk(fn):
            if isinstance(n, ast.Call):
                f = n.func
                out.add(f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else "")
        return out - {""}

    def query(self, sym: Symbol) -> None:
        self.requests += 1
        self.queried.append(sym["id"])
        sym["calls_in"] = sorted(self.called_by[sym["id"]] - {sym["id"]})
        sym["calls_in_x"] = []
        sym["calls_out"] = sorted(t for t, callers in self.called_by.items() if sym["id"] in callers and t != sym["id"])
        sym["calls_out_x"] = []
        sym["tests"] = {f: sorted(n)[:TESTS_PER_FILE] for f, n in self.test_calls[sym["id"]].items()}

    def run(self, syms: list[Symbol], workers: int = 8) -> None:
        for s in syms:
            self.query(s)

    def close(self) -> None:
        time.sleep(0)


@pytest.fixture()
def sample(tmp_path: Path) -> Path:
    dest = tmp_path / "sample"
    shutil.copytree(SAMPLE, dest)
    return dest


@pytest.fixture()
def layout(sample: Path) -> Layout:
    return Layout.detect(sample)
