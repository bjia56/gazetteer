"""Full, sharded and incremental call-graph builds against a name-based stand-in for the language server."""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from conftest import NameQuerier
from gazetteer.callgraph import (
    FACT_FIELDS,
    aggregate_tests,
    apply_facts,
    call_graph,
    incremental_call_graph,
    name_based_tests,
    run_shard,
    shard_symbols,
)
from gazetteer.extract import extract_modules
from gazetteer.layout import Layout

CMD = ("unused",)


def _full(root: Path, layout: Layout) -> tuple[dict[str, Any], dict[str, Any]]:
    modules, symbols = extract_modules(root, layout)
    call_graph(root, layout, CMD, modules, symbols, querier_factory=NameQuerier)
    return modules, symbols


def _facts(symbols: dict[str, Any], sid: str) -> dict[str, Any]:
    return {k: symbols[sid][k] for k in FACT_FIELDS}


class TestFullBuild:
    def test_callers_callees_and_tests(self, sample: Path, layout: Layout) -> None:
        _, symbols = _full(sample, layout)
        assert symbols["sample.util.clamp"]["calls_in"] == ["sample.core.Engine.step"]
        assert "sample.util.clamp" in symbols["sample.core.Engine.step"]["calls_out"]
        assert symbols["sample.core.run"]["tests"] == {"tests/test_core.py": ["test_run"]}

    def test_module_test_counts(self, sample: Path, layout: Layout) -> None:
        modules, symbols = _full(sample, layout)
        aggregate_tests(modules, symbols)
        assert modules["sample.core"]["tests"]["tests/test_core.py"] >= 2


class TestShards:
    def test_shards_cover_all_symbols_once(self, sample: Path, layout: Layout) -> None:
        _, symbols = extract_modules(sample, layout)
        got = [s["id"] for i in range(2) for s in shard_symbols(symbols, i, 2)]
        assert sorted(got) == sorted(symbols)

    def test_merge_equals_full_build(self, sample: Path, layout: Layout, tmp_path: Path) -> None:
        modules, symbols = extract_modules(sample, layout)
        for i in range(2):
            run_shard(
                sample, layout, CMD, modules, symbols, i, 2, tmp_path / f"facts_{i}.jsonl", querier_factory=NameQuerier
            )
        _, merged = extract_modules(sample, layout)
        assert apply_facts(merged, tmp_path) == len(merged)
        _, full = _full(sample, layout)
        for sid in full:
            assert _facts(merged, sid) == _facts(full, sid)

    def test_resume_skips_done_symbols(self, sample: Path, layout: Layout, tmp_path: Path) -> None:
        modules, symbols = extract_modules(sample, layout)
        out = tmp_path / "facts_0.jsonl"
        first = run_shard(sample, layout, CMD, modules, symbols, 0, 1, out, querier_factory=NameQuerier)
        second = run_shard(sample, layout, CMD, modules, symbols, 0, 1, out, querier_factory=NameQuerier)
        assert first["queried"] == len(symbols)
        assert (second["queried"], second["resumed"]) == (0, len(symbols))
        assert len(out.read_text().strip().split("\n")) == len(symbols)


class TestIncremental:
    def _step(
        self, sample: Path, layout: Layout, tmp_path: Path, edit: Callable[[Path], None]
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        modules, symbols = _full(sample, layout)
        prev_root = tmp_path / "prev"
        shutil.copytree(sample, prev_root)
        prev = {"modules": modules, "symbols": json.loads(json.dumps(symbols))}
        edit(sample)
        new_modules, new_symbols = extract_modules(sample, layout)
        info = incremental_call_graph(
            sample, layout, CMD, new_modules, new_symbols, prev, prev_root, querier_factory=NameQuerier
        )
        _, expected = _full(sample, layout)
        return new_symbols, expected, info

    def _assert_exact(self, got: dict[str, Any], expected: dict[str, Any]) -> None:
        assert set(got) == set(expected)
        for sid in expected:
            assert _facts(got, sid) == _facts(expected, sid), sid

    def test_unchanged_tree_queries_nothing(self, sample: Path, layout: Layout, tmp_path: Path) -> None:
        got, expected, info = self._step(sample, layout, tmp_path, lambda r: None)
        assert info["queried"] == 0
        self._assert_exact(got, expected)

    def test_body_edit_requeries_few_and_stays_exact(self, sample: Path, layout: Layout, tmp_path: Path) -> None:
        def edit(root: Path) -> None:
            p = root / "src/sample/util.py"
            p.write_text(p.read_text().replace("min(value, LIMIT)", "min(value, LIMIT) + abs(0)"))

        got, expected, info = self._step(sample, layout, tmp_path, edit)
        assert 0 < info["queried"] < len(expected)
        self._assert_exact(got, expected)

    def test_new_override_stays_exact(self, sample: Path, layout: Layout, tmp_path: Path) -> None:
        def edit(root: Path) -> None:
            p = root / "src/sample/core.py"
            p.write_text(
                p.read_text() + "\n\nclass Turbo(Engine):\n    def step(self, n: int) -> int:\n        return n * 2\n"
            )

        got, expected, _ = self._step(sample, layout, tmp_path, edit)
        self._assert_exact(got, expected)

    def test_removed_symbol_drops_edges(self, sample: Path, layout: Layout, tmp_path: Path) -> None:
        def edit(root: Path) -> None:
            p = root / "src/sample/util.py"
            p.write_text(p.read_text().replace("def clamp", "def clamp_renamed"))

        got, expected, _ = self._step(sample, layout, tmp_path, edit)
        assert "sample.util.clamp" not in got
        self._assert_exact(got, expected)

    def test_new_test_function_updates_links(self, sample: Path, layout: Layout, tmp_path: Path) -> None:
        def edit(root: Path) -> None:
            p = root / "tests/test_core.py"
            p.write_text(p.read_text() + "\n\ndef test_clamp() -> None:\n    assert clamp(99) == 10\n")

        got, expected, info = self._step(sample, layout, tmp_path, edit)
        assert "test_clamp" in got["sample.util.clamp"]["tests"]["tests/test_core.py"]
        assert info["changed_tests"] == 1
        self._assert_exact(got, expected)


class TestNameBasedTests:
    def test_links_rare_long_names(self, sample: Path, layout: Layout) -> None:
        _, symbols = extract_modules(sample, layout)
        info = name_based_tests(sample, layout, symbols, min_len=3)
        assert info["test_files"] == 1
        assert symbols["sample.core.run"]["tests"] == {"tests/test_core.py": ["test_run"]}
