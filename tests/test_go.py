"""The Go plugin: extraction through the helper, test links, and call graphs through gopls.

Needs gazetteer-gohelper (built from go/helper when a Go toolchain is there) and, for the call graph, gopls and
`go` on PATH; GAZETTEER_GOPLS points at a gopls (python scripts/build_go.py builds one into src/gazetteer/_bin).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from conftest import NameQuerier
from gazetteer.build import BuildOptions, build
from gazetteer.callgraph import FACT_FIELDS, aggregate_tests, call_graph, incremental_call_graph
from gazetteer.cli import main
from gazetteer.extract import extract_modules
from gazetteer.languages import LanguageToolError, get_language
from gazetteer.languages._gotools import gopls_path, helper_path
from gazetteer.layout import Layout, LayoutError
from gazetteer.links import link_docs


def _have_helper() -> bool:
    try:
        helper_path()
    except LanguageToolError:
        return False
    return True


def _have_gopls() -> bool:
    return _have_helper() and gopls_path() is not None and shutil.which("go") is not None


needs_helper = pytest.mark.skipif(not _have_helper(), reason="no gazetteer-gohelper and no Go toolchain to build it")
needs_gopls = pytest.mark.skipif(not _have_gopls(), reason="needs gopls and the go command")
CMD = get_language("go").default_server


@pytest.fixture()
def golayout(gosample: Path) -> Layout:
    return Layout.detect(gosample, "go")


class TestLayout:
    @needs_helper
    def test_module_path_is_the_import_prefix(self, gosample: Path, golayout: Layout) -> None:
        assert (golayout.lang, golayout.pkg_dir, golayout.import_prefix) == ("go", ".", "example.com/gosample")

    def test_a_go_mod_is_required(self, tmp_path: Path) -> None:
        with pytest.raises(LayoutError, match="no go.mod"):
            Layout.detect(tmp_path, "go")

    def test_test_files_are_recognised_by_name(self) -> None:
        layout = Layout(pkg_dir=".", module_root=".", import_prefix="m", lang="go")
        assert layout.is_test("engine/engine_test.go")
        assert not layout.is_test("engine/engine.go")
        assert not Layout(pkg_dir="p", module_root=".", import_prefix="p").is_test("tests_helper_test.go")


@needs_helper
class TestExtract:
    def test_modules_are_source_files_in_packages(self, gosample: Path, golayout: Layout) -> None:
        modules, _ = extract_modules(gosample, golayout)
        assert sorted(modules) == ["cmd/run/main", "engine/clamp", "engine/engine", "engine/turbo"]
        m = modules["engine/engine"]
        assert (m["path"], m["package"], m["doc"]) == ("engine/engine.go", "engine", "Package engine runs steps.")
        assert m["consts"] == [{"name": "Limit", "line": 5, "value": "10"}]

    def test_symbols_and_signatures(self, gosample: Path, golayout: Layout) -> None:
        _, symbols = extract_modules(gosample, golayout)
        step = symbols["engine/engine.Engine.Step"]
        assert step["kind"] == "method"
        assert step["sig"] == "func (e *Engine) Step(n int) int"
        assert step["doc"] == "Step advances the engine by n, clamped to Limit."
        assert (step["name"], step["qual"], step["private"]) == ("Step", "Engine.Step", False)
        assert "e.total += Clamp(n)" in step["code"]
        assert symbols["engine/engine.New"]["sig"] == "func New() *Engine"
        assert symbols["engine/engine.Engine"]["sig"] == "type Engine struct"
        assert symbols["engine/engine.Stepper"]["decl"] == "interface"
        assert not any("Generated" in sid or "_test" in sid for sid in symbols)

    def test_methods_belong_to_their_type_across_files(self, gosample: Path, golayout: Layout) -> None:
        _, symbols = extract_modules(gosample, golayout)
        engine = symbols["engine/engine.Engine"]
        assert symbols["engine/clamp.Engine.Reset"]["class"] == "engine/engine.Engine"
        assert "engine/clamp.Engine.Reset" in engine["methods"]
        assert "engine/engine.Engine.Step" in engine["methods"]
        iface = symbols["engine/engine.Stepper.Step"]
        assert iface["class"] == "engine/engine.Stepper"
        assert iface["sig"] == "Step(n int) int"
        assert symbols["engine/engine.Stepper"]["methods"] == ["engine/engine.Stepper.Step"]

    def test_embedding_is_inheritance(self, gosample: Path, golayout: Layout) -> None:
        _, symbols = extract_modules(gosample, golayout)
        assert symbols["engine/turbo.Turbo"]["bases"] == ["Engine"]
        assert symbols["engine/engine.Engine"]["subclasses"] == ["engine/turbo.Turbo"]

    def test_repeated_init_functions_keep_distinct_ids(self, gosample: Path, golayout: Layout) -> None:
        _, symbols = extract_modules(gosample, golayout)
        assert {"engine/clamp.init", "engine/clamp.init#2"} <= set(symbols)

    def test_column_is_utf16_for_the_language_server(self, gosample: Path, golayout: Layout) -> None:
        _, symbols = extract_modules(gosample, golayout)
        clamp = symbols["engine/clamp.Clamp"]
        line = (gosample / "engine/clamp.go").read_text().split("\n")[clamp["line"] - 1]
        assert len(line[: line.index("Clamp")].encode("utf-16-le")) // 2 == clamp["col"]
        assert clamp["col"] != line.index("Clamp")  # the emoji is one character but two units

    def test_imports_follow_the_names_a_file_uses(self, gosample: Path, golayout: Layout) -> None:
        modules, _ = extract_modules(gosample, golayout)
        assert modules["cmd/run/main"]["imports"] == ["engine/engine"]  # Limit and New are declared there
        assert modules["engine/engine"]["imported_by"] == ["cmd/run/main"]
        assert modules["engine/clamp"]["imported_by"] == []

    def test_skip_patterns_apply(self, gosample: Path, golayout: Layout) -> None:
        from dataclasses import replace

        modules, _ = extract_modules(gosample, replace(golayout, skip=(*golayout.skip, "/cmd/")))
        assert "cmd/run/main" not in modules

    def test_docs_name_files_and_symbols(self, gosample: Path, golayout: Layout) -> None:
        modules, symbols = extract_modules(gosample, golayout)
        docs = link_docs(gosample, golayout, modules, symbols)
        assert [d["file"] for d in docs] == ["docs/guide.md"]
        assert modules["engine/engine"]["docs"][0]["line"] == 3

    def test_nested_modules_are_other_units(self, gosample: Path, golayout: Layout) -> None:
        (gosample / "tools").mkdir()
        (gosample / "tools" / "go.mod").write_text("module example.com/tools\n")
        (gosample / "tools" / "t.go").write_text("package tools\nfunc T() {}\n")
        modules, _ = extract_modules(gosample, golayout)
        assert not any(m.startswith("tools/") for m in modules)


@needs_helper
class TestTests:
    def test_test_files_exclude_everything_the_go_tool_ignores(self, gosample: Path, golayout: Layout) -> None:
        (gosample / "engine" / "testdata").mkdir()
        (gosample / "engine" / "testdata" / "x_test.go").write_text("package x\n")
        assert get_language("go").test_files(gosample, golayout) == ["engine/engine_test.go"]

    def test_test_uses(self, gosample: Path) -> None:
        got = get_language("go").test_uses(gosample, ["engine/engine_test.go", "engine/engine.go"])
        tests = got["engine/engine_test.go"]
        assert tests is not None
        assert [name for name, _ in tests] == ["TestEngineStep", "TestClampLimits"]
        assert {"New", "Step"} <= dict(tests)["TestEngineStep"]

    def test_unparsable_file_is_none(self, gosample: Path) -> None:
        (gosample / "engine" / "bad_test.go").write_text("package engine\nfunc (")
        assert get_language("go").test_uses(gosample, ["engine/bad_test.go"]) == {"engine/bad_test.go": None}

    def test_test_table_and_names(self, gosample: Path) -> None:
        lang = get_language("go")
        funcs, residual = lang.test_table(gosample / "engine/engine_test.go")
        assert set(funcs) == {"TestEngineStep", "TestClampLimits"}
        h, uses, bare = funcs["TestClampLimits"]
        assert bare == "TestClampLimits" and "Clamp" in uses and len(h) == 40
        (gosample / "engine/engine_test.go").write_text(
            (gosample / "engine/engine_test.go").read_text().replace("Clamp(100)", "Clamp(101)")
        )
        funcs2, residual2 = lang.test_table(gosample / "engine/engine_test.go")
        assert funcs2["TestClampLimits"][0] != h and funcs2["TestEngineStep"][0] == funcs["TestEngineStep"][0]
        assert residual2 == residual
        assert {"Clamp", "Limit", "testing"} <= lang.coarse_names(gosample / "engine/engine_test.go")

    def test_name_based_links(self, gosample: Path, golayout: Layout) -> None:
        data = build(BuildOptions(root=gosample, layout=golayout, use_lsp=False, tests_by_name=gosample))
        assert data["symbols"]["engine/clamp.Clamp"]["tests"] == {"engine/engine_test.go": ["TestClampLimits"]}
        assert data["modules"]["engine/clamp"]["tests"] == {"engine/engine_test.go": 1}


@needs_helper
class TestBuildAndCli:
    def test_build_without_the_language_server(self, gosample: Path, golayout: Layout) -> None:
        data = build(BuildOptions(root=gosample, layout=golayout, use_lsp=False))
        assert data["project"]["lang"] == "go"
        assert data["project"]["prefix"] == "example.com/gosample"
        assert data["stats"]["modules"] == 4

    def test_cli(self, gosample: Path, tmp_path: Path) -> None:
        out = tmp_path / "out"
        assert main(["build", str(gosample), "--lang", "go", "--out", str(out), "--no-lsp"]) == 0
        assert "engine/engine.Engine.Step" in json.loads((out / "data.json").read_text())["symbols"]

    def test_missing_helper_is_a_clean_error(
        self, gosample: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("GAZETTEER_GOHELPER", str(tmp_path / "nope"))
        assert main(["build", str(gosample), "--lang", "go", "--out", str(tmp_path / "o"), "--no-lsp"]) == 2
        assert "GAZETTEER_GOHELPER" in capsys.readouterr().err

    def test_go_and_python_units_share_a_page(self, gosample: Path, sample: Path, tmp_path: Path) -> None:
        root = tmp_path / "mono"
        shutil.copytree(sample, root / "svc" / "py")
        shutil.copytree(gosample, root / "svc" / "go")
        (root / "gazetteer.toml").write_text(
            '[[unit]]\nname = "py"\npath = "svc/py"\n\n[[unit]]\nname = "go"\npath = "svc/go"\nlang = "go"\n'
        )
        out = tmp_path / "out"
        assert main(["build", str(root), "--out", str(out), "--no-lsp"]) == 0
        data = json.loads((out / "data.json").read_text())
        assert data["project"]["lang"] == "go,python"
        assert {m["unit"] for m in data["modules"].values()} == {"py", "go"}
        assert "go:engine/engine.Engine.Step" in data["symbols"]
        assert "py:sample.core.run" in data["symbols"]
        assert data["modules"]["go:engine/engine"]["path"] == "svc/go/engine/engine.go"
        assert data["modules"]["go:cmd/run/main"]["imports"] == ["go:engine/engine"]
        assert main(["build", str(root), "--out", str(out), "--no-lsp", "--server", "x"]) == 2


def _facts(symbols: dict[str, Any]) -> dict[str, Any]:
    return {sid: {k: s[k] for k in FACT_FIELDS} for sid, s in symbols.items()}


@needs_gopls
class TestCallGraph:
    def test_callers_callees_and_tests(self, gosample: Path, golayout: Layout) -> None:
        data = build(BuildOptions(root=gosample, layout=golayout))
        sym = data["symbols"]
        assert data["stats"]["call_graph"]["queried"] == len(sym)
        assert sym["engine/clamp.Clamp"]["calls_in"] == ["engine/engine.Engine.Step"]
        assert sym["engine/engine.Engine.Step"]["calls_out"] == ["engine/clamp.Clamp"]
        assert {"engine/engine.New", "engine/engine.Engine.Step"} <= set(sym["cmd/run/main.main"]["calls_out"])
        assert sym["engine/clamp.Clamp"]["tests"] == {"engine/engine_test.go": ["TestClampLimits"]}
        assert sym["engine/engine.New"]["tests"] == {"engine/engine_test.go": ["TestEngineStep"]}
        assert data["modules"]["engine/engine"]["tests"] == {"engine/engine_test.go": 3}

    def test_tests_in_test_files_are_not_counted_as_callers(self, gosample: Path, golayout: Layout) -> None:
        data = build(BuildOptions(root=gosample, layout=golayout))
        assert not any("_test" in c for s in data["symbols"].values() for c in s["calls_in"])

    def _step(
        self, gosample: Path, golayout: Layout, tmp_path: Path, edit: Any
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        modules, symbols = extract_modules(gosample, golayout)
        call_graph(gosample, golayout, CMD, modules, symbols, workers=2)
        prev = {"modules": modules, "symbols": json.loads(json.dumps(symbols))}
        prev_root = tmp_path / "prev"
        shutil.copytree(gosample, prev_root)
        edit(gosample)
        new_modules, new_symbols = extract_modules(gosample, golayout)
        info = incremental_call_graph(gosample, golayout, CMD, new_modules, new_symbols, prev, prev_root, 2)
        full_modules, full = extract_modules(gosample, golayout)
        call_graph(gosample, golayout, CMD, full_modules, full, workers=2)
        return new_symbols, full, info

    def test_unchanged_tree_queries_nothing(self, gosample: Path, golayout: Layout, tmp_path: Path) -> None:
        got, full, info = self._step(gosample, golayout, tmp_path, lambda r: None)
        assert info["queried"] == 0
        assert _facts(got) == _facts(full)

    def test_body_edit_stays_exact(self, gosample: Path, golayout: Layout, tmp_path: Path) -> None:
        def edit(root: Path) -> None:
            p = root / "engine/clamp.go"
            p.write_text(p.read_text().replace("return Limit", "return Limit - 0"))

        got, full, info = self._step(gosample, golayout, tmp_path, edit)
        assert 0 < info["queried"] < len(full)
        assert _facts(got) == _facts(full)

    def test_new_caller_stays_exact(self, gosample: Path, golayout: Layout, tmp_path: Path) -> None:
        def edit(root: Path) -> None:
            (root / "engine/extra.go").write_text("package engine\n\nfunc Extra() int { return Clamp(1) }\n")

        got, full, _ = self._step(gosample, golayout, tmp_path, edit)
        assert "engine/extra.Extra" in got["engine/clamp.Clamp"]["calls_in"]
        assert _facts(got) == _facts(full)

    def test_changed_test_relinks(self, gosample: Path, golayout: Layout, tmp_path: Path) -> None:
        def edit(root: Path) -> None:
            p = root / "engine/engine_test.go"
            p.write_text(p.read_text() + "\nfunc TestReset(t *testing.T) { New().Reset() }\n")

        got, full, _ = self._step(gosample, golayout, tmp_path, edit)
        assert got["engine/clamp.Engine.Reset"]["tests"] == {"engine/engine_test.go": ["TestReset"]}
        assert _facts(got) == _facts(full)

    def test_aggregate_matches_symbol_tests(self, gosample: Path, golayout: Layout) -> None:
        modules, symbols = extract_modules(gosample, golayout)
        call_graph(gosample, golayout, CMD, modules, symbols, workers=2)
        aggregate_tests(modules, symbols)
        assert modules["engine/clamp"]["tests"] == {"engine/engine_test.go": 1}


def test_python_querier_is_unaffected(sample: Path, layout: Layout) -> None:
    """The python name-based stand-in still drives the shared pipeline (guards the language-neutral plumbing)."""
    data = build(BuildOptions(root=sample, layout=layout, querier_factory=NameQuerier))
    assert data["project"]["lang"] == "python"
