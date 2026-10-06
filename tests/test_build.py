"""End to end: build data, pack the page, run the CLI."""

from __future__ import annotations

import base64
import gzip
import json
import subprocess
from pathlib import Path

from conftest import NameQuerier
from gazetteer.build import BuildOptions, build
from gazetteer.cli import main
from gazetteer.extract import extract_modules
from gazetteer.layout import Layout
from gazetteer.links import link_docs
from gazetteer.pack import PLACEHOLDER, pack


def _git_init(root: Path) -> None:
    for cmd in (
        ["init", "-q"],
        ["add", "-A"],
        ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "initial"],
    ):
        subprocess.run(["git", "-C", str(root), *cmd], check=True)


class TestBuild:
    def test_data_has_project_and_graph(self, sample: Path, layout: Layout) -> None:
        _git_init(sample)
        data = build(BuildOptions(root=sample, layout=layout, querier_factory=NameQuerier))
        assert data["project"]["prefix"] == "sample"
        assert len(data["commit"]) == 40
        assert data["symbols"]["sample.util.clamp"]["calls_in"] == ["sample.core.Engine.step"]
        assert data["modules"]["sample.core"]["git"]["commits"] == 1

    def test_no_git_repo_is_fine(self, sample: Path, layout: Layout) -> None:
        data = build(BuildOptions(root=sample, layout=layout, use_lsp=False))
        assert data["commit"] == ""
        assert data["modules"]["sample.core"]["git"] is None


class TestDocLinks:
    def test_doc_names_modules_and_symbols(self, sample: Path, layout: Layout) -> None:
        modules, symbols = extract_modules(sample, layout)
        docs = link_docs(sample, layout, modules, symbols)
        assert [d["title"] for d in docs] == ["Guide"]
        assert modules["sample.core"]["docs"][0]["line"] == 3
        assert modules["sample.util"]["docs"], "a path mention links the module"
        assert symbols["sample.util.clamp"]["docs"] == []  # name shorter than the minimum


class TestPack:
    def test_page_embeds_data_and_is_project_neutral(self, tmp_path: Path) -> None:
        data = tmp_path / "data.json"
        data.write_text(json.dumps({"x": 1}))
        out = tmp_path / "index.html"
        pack(data, out)
        html = out.read_text()
        packed = html.split('const PACKED = "')[1].split('"')[0]
        assert json.loads(gzip.decompress(base64.b64decode(packed))) == {"x": 1}
        assert PLACEHOLDER not in html


class TestCli:
    def test_build_writes_outputs(self, sample: Path, tmp_path: Path) -> None:
        out = tmp_path / "out"
        assert main(["build", str(sample), "--out", str(out), "--no-lsp"]) == 0
        assert (out / "data.json").exists()
        assert (out / "index.html").exists()
        assert json.loads((out / "data.json").read_text())["project"]["name"] == "sample"

    def test_undetectable_layout_is_an_error(self, tmp_path: Path) -> None:
        assert main(["build", str(tmp_path), "--out", str(tmp_path / "o"), "--no-lsp"]) == 2

    def test_prev_options_go_together(self, sample: Path, tmp_path: Path) -> None:
        assert main(["build", str(sample), "--out", str(tmp_path / "o"), "--prev-data", "x.json"]) == 2

    def test_shard_needs_facts_dir(self, sample: Path, tmp_path: Path) -> None:
        assert main(["build", str(sample), "--shard", "0/2"]) == 2
