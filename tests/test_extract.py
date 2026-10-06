"""Symbol and import extraction."""

from __future__ import annotations

from pathlib import Path

from gazetteer.extract import extract_modules
from gazetteer.layout import Layout


class TestExtractModules:
    def test_module_ids_and_symbols(self, sample: Path, layout: Layout) -> None:
        modules, symbols = extract_modules(sample, layout)
        assert set(modules) == {"sample", "sample.core", "sample.util"}
        assert {"sample.core.Engine", "sample.core.Engine.step", "sample.core.run"} <= set(symbols)

    def test_signature_and_async(self, sample: Path, layout: Layout) -> None:
        _, symbols = extract_modules(sample, layout)
        assert symbols["sample.core.Engine.spin"]["sig"] == "async spin(self, n: int) -> int"
        assert symbols["sample.core.Engine"]["sig"] == "class Engine(Base)"

    def test_private_and_consts(self, sample: Path, layout: Layout) -> None:
        modules, symbols = extract_modules(sample, layout)
        assert symbols["sample.util._unused_helper"]["private"]
        assert modules["sample.util"]["consts"] == [{"name": "LIMIT", "line": 5, "value": "10"}]

    def test_imports_link_both_ways(self, sample: Path, layout: Layout) -> None:
        modules, _ = extract_modules(sample, layout)
        assert modules["sample.core"]["imports"] == ["sample.util"]
        assert "sample.core" in modules["sample.util"]["imported_by"]

    def test_subclasses(self, sample: Path, layout: Layout) -> None:
        _, symbols = extract_modules(sample, layout)
        assert symbols["sample.core.Base"]["subclasses"] == ["sample.core.Engine"]

    def test_hash_changes_with_source(self, sample: Path, layout: Layout) -> None:
        _, before = extract_modules(sample, layout)
        path = sample / "src/sample/util.py"
        path.write_text(path.read_text().replace("min(value, LIMIT)", "max(value, LIMIT)"))
        _, after = extract_modules(sample, layout)
        assert before["sample.util.clamp"]["h"] != after["sample.util.clamp"]["h"]
        assert before["sample.core.run"]["h"] == after["sample.core.run"]["h"]

    def test_syntax_error_file_is_skipped(self, sample: Path, layout: Layout) -> None:
        (sample / "src/sample/broken.py").write_text("def (:\n")
        modules, _ = extract_modules(sample, layout)
        assert "sample.broken" not in modules
