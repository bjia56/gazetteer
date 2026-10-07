"""Build the docs data for one tree: symbols, call graph, doc links, git facts. A tree is one or more units."""

from __future__ import annotations

import json
import shlex
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .callgraph import (
    LspQuerier,
    QuerierFactory,
    aggregate_tests,
    apply_facts,
    call_graph,
    incremental_call_graph,
    name_based_tests,
    run_shard,
)
from .extract import extract_modules
from .languages import get_language
from .layout import Layout, join_path
from .links import git_facts, link_docs
from .model import Module, Symbol
from .units import Unit, from_repo, to_repo


@dataclass
class BuildOptions:
    """``layout`` documents the whole tree as one bare unit; ``units`` (from gazetteer.toml) replaces it."""

    root: Path
    layout: Layout | None = None
    units: tuple[Unit, ...] = ()
    server: tuple[str, ...] | None = None  # default: the unit's own, else the language's own server
    git_dir: Path | None = None
    name: str | None = None
    use_lsp: bool = True
    workers: int = 8
    tests_by_name: Path | None = None
    facts_dir: Path | None = None
    prev_data: Path | None = None
    prev_root: Path | None = None
    querier_factory: QuerierFactory = field(default=LspQuerier)

    def all_units(self) -> tuple[Unit, ...]:
        if self.units:
            return self.units
        if self.layout is None:
            raise ValueError("BuildOptions needs a layout or units")
        return (Unit(name="", path=".", layout=self.layout),)


def parse_server(command: str) -> tuple[str, ...]:
    return tuple(shlex.split(command))


def _head_sha(git_dir: Path) -> str:
    proc = subprocess.run(["git", "-C", str(git_dir), "rev-parse", "HEAD"], capture_output=True, text=True)
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _server(opts: BuildOptions, unit: Unit) -> tuple[str, ...]:
    return unit.server or opts.server or get_language(unit.layout.lang).default_server


def _facts_dir(opts: BuildOptions, unit: Unit) -> Path | None:
    if opts.facts_dir is None:
        return None
    return opts.facts_dir / unit.name if unit.name else opts.facts_dir


def _build_unit(
    opts: BuildOptions, unit: Unit, prev: dict[str, Any] | None
) -> tuple[dict[str, Module], dict[str, Symbol], list[dict[str, str]], dict[str, Any]]:
    """Everything for one unit, in the unit's own ids and paths."""
    root, layout, server = unit.root(opts.root), unit.layout, _server(opts, unit)
    get_language(layout.lang).require(opts.use_lsp)
    modules, symbols = extract_modules(root, layout)
    cg_info: dict[str, Any] = {}
    if opts.use_lsp:
        facts_dir = _facts_dir(opts, unit)
        if facts_dir:
            got = apply_facts(symbols, facts_dir)
            aggregate_tests(modules, symbols)
            cg_info = {"mode": "merged-shards", "symbols_with_facts": got}
        elif prev is not None and opts.prev_root:
            cg_info = incremental_call_graph(
                root,
                layout,
                server,
                modules,
                symbols,
                prev,
                unit.root(opts.prev_root),
                opts.workers,
                opts.querier_factory,
            )
        else:
            cg_info = call_graph(root, layout, server, modules, symbols, opts.workers, opts.querier_factory)
    if opts.tests_by_name:
        t1 = time.time()
        tinfo = name_based_tests(unit.root(opts.tests_by_name), layout, symbols)
        aggregate_tests(modules, symbols)
        cg_info = {**cg_info, "tests_by_name": {**tinfo, "seconds": round(time.time() - t1, 1)}}
    docs = link_docs(root, layout, modules, symbols)
    return modules, symbols, docs, cg_info


def _project(opts: BuildOptions, units: tuple[Unit, ...]) -> dict[str, Any]:
    name = opts.name or opts.root.resolve().name
    if len(units) == 1 and not units[0].name:
        layout = units[0].layout
        return {
            "name": name,
            "lang": layout.lang,
            "prefix": layout.import_prefix,
            "tests": list(layout.tests),
            "docs_dir": layout.docs_dir,
            "pkg_dir": layout.pkg_dir,
        }
    repo_layouts = [(u, u.layout.under(u.path)) for u in units]
    return {
        "name": name,
        "lang": ",".join(sorted({u.layout.lang for u in units})),
        "prefix": "",
        "tests": [t for _, lo in repo_layouts for t in lo.tests],
        "docs_dir": "",
        "pkg_dir": "",
        "units": [
            {
                "name": u.name,
                "lang": u.layout.lang,
                "path": u.path,
                "pkg_dir": lo.pkg_dir,
                "prefix": u.layout.import_prefix,
                "tests": list(lo.tests),
                "docs_dir": lo.docs_dir,
            }
            for u, lo in repo_layouts
        ],
    }


def build(opts: BuildOptions) -> dict[str, Any]:
    """Return the data dict that ``pack`` embeds into the page."""
    t0 = time.time()
    units = opts.all_units()
    git_dir = opts.git_dir or opts.root
    prev = json.loads(opts.prev_data.read_text()) if opts.prev_data and opts.prev_root else None
    modules: dict[str, Module] = {}
    symbols: dict[str, Symbol] = {}
    docs: dict[str, dict[str, str]] = {}
    cg_infos: dict[str, dict[str, Any]] = {}
    commits_seen = 0
    for unit in units:
        unit_prev = None
        if prev is not None:
            pm, ps = from_repo(unit, prev["modules"], prev["symbols"])
            unit_prev = {**prev, "modules": pm, "symbols": ps}
        u_modules, u_symbols, u_docs, cg_info = _build_unit(opts, unit, unit_prev)
        u_modules, u_symbols = to_repo(unit, u_modules, u_symbols)
        for d in u_docs:  # units rooted at the same directory read the same docs
            docs.setdefault(join_path(unit.path, d["file"]), {**d, "file": join_path(unit.path, d["file"])})
        stats = git_facts(git_dir, unit.layout.under(unit.path), u_modules)
        commits_seen = max(commits_seen, stats.get("commits_seen", 0))  # a commit touching two units counts in both
        modules.update(u_modules)
        symbols.update(u_symbols)
        cg_infos[unit.name] = cg_info
    return {
        "commit": _head_sha(git_dir),
        "project": _project(opts, units),
        "stats": {
            "commits_seen": commits_seen,
            "modules": len(modules),
            "symbols": len(symbols),
            "docs": len(docs),
            "build_seconds": round(time.time() - t0, 1),
            "call_graph": cg_infos[""] if len(units) == 1 and not units[0].name else cg_infos,
        },
        "modules": modules,
        "symbols": symbols,
        "docs": list(docs.values()),
    }


def build_shard(opts: BuildOptions, i: int, n: int) -> dict[str, Any]:
    """Query shard ``i`` of ``n`` of every unit into ``opts.facts_dir`` (resumable)."""
    assert opts.facts_dir is not None
    infos: dict[str, Any] = {}
    for unit in opts.all_units():
        root = unit.root(opts.root)
        get_language(unit.layout.lang).require(True)
        modules, symbols = extract_modules(root, unit.layout)
        facts_dir = _facts_dir(opts, unit)
        assert facts_dir is not None
        infos[unit.name] = run_shard(
            root,
            unit.layout,
            _server(opts, unit),
            modules,
            symbols,
            i,
            n,
            facts_dir / f"facts_{i}.jsonl",
            opts.workers,
            opts.querier_factory,
        )
    return infos[""] if len(infos) == 1 and "" in infos else infos
