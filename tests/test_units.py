"""Units: gazetteer.toml, namespaced ids, rebased paths, and per-unit shards and incremental builds."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from conftest import SAMPLE, NameQuerier
from gazetteer.build import BuildOptions, build, build_shard
from gazetteer.callgraph import FACT_FIELDS
from gazetteer.cli import main
from gazetteer.units import ConfigError, find_config, from_repo, load_config, to_repo

CONFIG = """
name = "mono"

[[unit]]
name = "alpha"
path = "services/a"

[[unit]]
path = "services/b"
"""


@pytest.fixture()
def mono(tmp_path: Path) -> Path:
    """Two services that both contain the sample package, so every id exists twice unless namespaced."""
    root = tmp_path / "mono"
    for svc in ("a", "b"):
        shutil.copytree(SAMPLE, root / "services" / svc)
    (root / "gazetteer.toml").write_text(CONFIG)
    return root


def _units(root: Path) -> tuple[Any, ...]:
    return load_config(root / "gazetteer.toml", root).units


def _opts(root: Path, **kw: Any) -> BuildOptions:
    return BuildOptions(root=root, units=_units(root), querier_factory=NameQuerier, **kw)


class TestConfig:
    def test_units_are_detected_below_their_paths(self, mono: Path) -> None:
        cfg = load_config(mono / "gazetteer.toml", mono)
        assert cfg.name == "mono"
        assert [(u.name, u.path) for u in cfg.units] == [("alpha", "services/a"), ("b", "services/b")]
        assert cfg.units[0].layout.pkg_dir == "src/sample"
        assert cfg.units[0].layout.tests == ("tests",)

    def test_find_config(self, mono: Path, tmp_path: Path) -> None:
        assert find_config(mono) == mono / "gazetteer.toml"
        assert find_config(tmp_path) is None

    def test_table_overrides_detection(self, mono: Path) -> None:
        (mono / "gazetteer.toml").write_text(
            '[[unit]]\npath = "services/a"\npkg_dir = "src/sample"\nmodule_root = "src"\n'
            'import_prefix = "sample"\ntests = ["tests"]\nskip = ["util"]\nserver = "my-ls --stdio"\n'
        )
        (unit,) = _units(mono)
        assert unit.layout.skip == ("util",)
        assert unit.server == ("my-ls", "--stdio")

    @pytest.mark.parametrize(
        ("text", "message"),
        [
            ("", "at least one"),
            ("[[unit]]\nbogus = 1\n", "unknown key"),
            ('[[unit]]\npath = "nope"\n', "not a directory"),
            ('[[unit]]\npath = "../x"\n', "stay inside"),
            ('[[unit]]\npath = "/etc"\n', "stay inside"),
            ('[[unit]]\npath = "services/a"\n[[unit]]\npath = "services/a"\n', "share this name"),
            ('[[unit]]\npath = "services/a"\nname = "a:b"\n', "name must be"),
            ('[[unit]]\npath = "services/a"\ntests = "tests"\n', "list of strings"),
            ('[[unit]]\npath = "services/a"\nlang = "cobol"\n', "unknown language"),
            ("[[unit]\n", "gazetteer.toml"),
        ],
    )
    def test_bad_config_is_an_error(self, mono: Path, text: str, message: str) -> None:
        (mono / "gazetteer.toml").write_text(text)
        with pytest.raises(ConfigError, match=message):
            load_config(mono / "gazetteer.toml", mono)

    def test_undetectable_unit_names_itself(self, mono: Path) -> None:
        (mono / "empty").mkdir()
        (mono / "gazetteer.toml").write_text('[[unit]]\npath = "empty"\n')
        with pytest.raises(ConfigError, match="unit 'empty'.*no Python package"):
            load_config(mono / "gazetteer.toml", mono)


class TestMultiUnitBuild:
    def test_ids_are_namespaced_and_paths_rebased(self, mono: Path) -> None:
        data = build(_opts(mono))
        _, single = _single_ids()
        assert set(data["symbols"]) == {f"{u}:{i}" for u in ("alpha", "b") for i in single}
        for sid, s in data["symbols"].items():
            unit = sid.split(":")[0]
            assert s["unit"] == unit
            assert s["module"].startswith(f"{unit}:")
            assert s["file"].startswith("services/" + ("a" if unit == "alpha" else "b") + "/src/sample/")
        m = data["modules"]["alpha:sample.core"]
        assert m["path"] == "services/a/src/sample/core.py"
        assert all(x.startswith("alpha:") for x in m["symbols"] + m["imports"] + m["imported_by"])

    def test_call_graph_and_tests_stay_inside_the_unit(self, mono: Path) -> None:
        data = build(_opts(mono))
        for sid, s in data["symbols"].items():
            unit = sid.split(":")[0]
            assert all(x.startswith(f"{unit}:") for x in s["calls_in"] + s["calls_out"]), sid
            prefix = "services/" + ("a" if unit == "alpha" else "b") + "/tests/"
            assert all(f.startswith(prefix) for f in s["tests"]), sid
        assert any(s["calls_in"] for s in data["symbols"].values())
        assert any(s["tests"] for s in data["symbols"].values())

    def test_project_and_stats(self, mono: Path) -> None:
        data = build(_opts(mono, name="mono"))
        p = data["project"]
        assert p["name"] == "mono"
        assert p["tests"] == ["services/a/tests", "services/b/tests"]
        assert [u["name"] for u in p["units"]] == ["alpha", "b"]
        assert set(data["stats"]["call_graph"]) == {"alpha", "b"}
        assert data["stats"]["symbols"] == len(data["symbols"])

    def test_docs_are_linked_per_unit(self, mono: Path) -> None:
        data = build(_opts(mono))
        assert sorted(d["file"] for d in data["docs"]) == ["services/a/docs/guide.md", "services/b/docs/guide.md"]
        linked = [d for m in data["modules"].values() for d in m["docs"]]
        assert linked
        assert {d["file"].split("/")[1] for d in linked} == {"a", "b"}

    def test_git_history_uses_repo_paths(self, mono: Path) -> None:
        for cmd in (["init", "-q"], ["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x"]):
            subprocess.run(["git", "-C", str(mono), *cmd], check=True)
        data = build(_opts(mono))
        assert data["modules"]["alpha:sample.core"]["git"]["commits"] == 1
        assert data["stats"]["commits_seen"] == 1

    def test_single_bare_unit_is_unchanged(self, sample: Path, layout: Any) -> None:
        data = build(BuildOptions(root=sample, layout=layout, querier_factory=NameQuerier))
        assert not any(":" in sid for sid in data["symbols"])
        assert "unit" not in next(iter(data["symbols"].values()))
        assert "units" not in data["project"]


def _single_ids() -> tuple[None, set[str]]:
    from gazetteer.extract import extract_modules
    from gazetteer.layout import Layout

    _, symbols = extract_modules(SAMPLE, Layout.detect(SAMPLE))
    return None, set(symbols)


class TestRebaseRoundTrip:
    def test_from_repo_inverts_to_repo(self, mono: Path) -> None:
        from gazetteer.callgraph import call_graph
        from gazetteer.extract import extract_modules

        a, b = _units(mono)
        mods, syms = {}, {}
        for u in (a, b):
            m, s = extract_modules(u.root(mono), u.layout)
            call_graph(u.root(mono), u.layout, ("x",), m, s, querier_factory=NameQuerier)
            m, s = to_repo(u, m, s)
            mods.update(m)
            syms.update(s)
        back_m, back_s = from_repo(a, mods, syms)
        m0, s0 = extract_modules(a.root(mono), a.layout)
        call_graph(a.root(mono), a.layout, ("x",), m0, s0, querier_factory=NameQuerier)
        assert back_s == s0
        assert back_m == m0


def _facts(data: dict[str, Any]) -> dict[str, Any]:
    return {sid: {k: s[k] for k in FACT_FIELDS} for sid, s in data["symbols"].items()}


class TestShardsAndIncremental:
    def test_shards_merge_to_the_full_build(self, mono: Path, tmp_path: Path) -> None:
        facts = tmp_path / "facts"
        for i in range(2):
            build_shard(_opts(mono, facts_dir=facts), i, 2)
        assert sorted(p.parent.name for p in facts.rglob("facts_*.jsonl")) == ["alpha", "alpha", "b", "b"]
        merged = build(_opts(mono, facts_dir=facts))
        assert _facts(merged) == _facts(build(_opts(mono)))

    def test_incremental_requeries_only_the_changed_unit(self, mono: Path, tmp_path: Path) -> None:
        prev = build(_opts(mono))
        prev_data = tmp_path / "prev.json"
        prev_data.write_text(json.dumps(prev))
        prev_root = tmp_path / "prev_root"
        shutil.copytree(mono, prev_root)
        util = mono / "services/a/src/sample/util.py"
        util.write_text(util.read_text().replace("min(value, LIMIT)", "min(value, LIMIT) + abs(0)"))

        NameQuerier.instances.clear()
        got = build(_opts(mono, prev_data=prev_data, prev_root=prev_root))
        queried = {sid for q in NameQuerier.instances for sid in q.queried}
        assert queried
        assert all(sid.startswith("a:") or not sid.startswith("b:") for sid in queried)
        info = got["stats"]["call_graph"]
        assert info["alpha"]["queried"] > 0
        assert info["b"]["queried"] == 0
        assert _facts(got) == _facts(build(_opts(mono)))


class TestCli:
    def test_config_in_root_is_used(self, mono: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        out = tmp_path / "out"
        assert main(["build", str(mono), "--out", str(out), "--no-lsp"]) == 0
        data = json.loads((out / "data.json").read_text())
        assert data["project"]["name"] == "mono"
        assert {s["unit"] for s in data["symbols"].values()} == {"alpha", "b"}
        assert "2 units" in capsys.readouterr().out

    def test_layout_flags_conflict_with_config(self, mono: Path, tmp_path: Path) -> None:
        assert main(["build", str(mono), "--out", str(tmp_path / "o"), "--no-lsp", "--pkg-dir", "x"]) == 2

    def test_bad_config_exits_2(self, mono: Path, tmp_path: Path) -> None:
        (mono / "gazetteer.toml").write_text("[[unit]]\nbogus = 1\n")
        assert main(["build", str(mono), "--out", str(tmp_path / "o"), "--no-lsp"]) == 2

    def test_name_flag_beats_config(self, mono: Path, tmp_path: Path) -> None:
        out = tmp_path / "o"
        assert main(["build", str(mono), "--out", str(out), "--no-lsp", "--name", "other"]) == 0
        assert json.loads((out / "data.json").read_text())["project"]["name"] == "other"
