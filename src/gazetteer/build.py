"""Build the docs data for one tree: symbols, call graph, doc links, git facts."""

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
from .layout import Layout
from .links import git_facts, link_docs


@dataclass
class BuildOptions:
    root: Path
    layout: Layout
    server: tuple[str, ...] | None = None  # default: the language's own server
    git_dir: Path | None = None
    name: str | None = None
    use_lsp: bool = True
    workers: int = 8
    tests_by_name: Path | None = None
    facts_dir: Path | None = None
    prev_data: Path | None = None
    prev_root: Path | None = None
    querier_factory: QuerierFactory = field(default=LspQuerier)


def parse_server(command: str) -> tuple[str, ...]:
    return tuple(shlex.split(command))


def _head_sha(git_dir: Path) -> str:
    proc = subprocess.run(["git", "-C", str(git_dir), "rev-parse", "HEAD"], capture_output=True, text=True)
    return proc.stdout.strip() if proc.returncode == 0 else ""


def build(opts: BuildOptions) -> dict[str, Any]:
    """Return the data dict that ``pack`` embeds into the page."""
    t0 = time.time()
    root, layout = opts.root, opts.layout
    git_dir = opts.git_dir or root
    server = opts.server or get_language(layout.lang).default_server
    modules, symbols = extract_modules(root, layout)
    cg_info: dict[str, Any] = {}
    if opts.use_lsp:
        if opts.facts_dir:
            got = apply_facts(symbols, opts.facts_dir)
            aggregate_tests(modules, symbols)
            cg_info = {"mode": "merged-shards", "symbols_with_facts": got}
        elif opts.prev_data and opts.prev_root:
            prev = json.loads(opts.prev_data.read_text())
            cg_info = incremental_call_graph(
                root, layout, server, modules, symbols, prev, opts.prev_root, opts.workers, opts.querier_factory
            )
        else:
            cg_info = call_graph(root, layout, server, modules, symbols, opts.workers, opts.querier_factory)
    if opts.tests_by_name:
        t1 = time.time()
        tinfo = name_based_tests(opts.tests_by_name, layout, symbols)
        aggregate_tests(modules, symbols)
        cg_info = {**cg_info, "tests_by_name": {**tinfo, "seconds": round(time.time() - t1, 1)}}
    docs = link_docs(root, layout, modules, symbols)
    stats = git_facts(git_dir, layout, modules)
    return {
        "commit": _head_sha(git_dir),
        "project": {
            "name": opts.name or root.resolve().name,
            "lang": layout.lang,
            "prefix": layout.import_prefix,
            "tests": list(layout.tests),
            "docs_dir": layout.docs_dir,
            "pkg_dir": layout.pkg_dir,
        },
        "stats": {
            "commits_seen": 0,
            **stats,
            "modules": len(modules),
            "symbols": len(symbols),
            "docs": len(docs),
            "build_seconds": round(time.time() - t0, 1),
            "call_graph": cg_info,
        },
        "modules": modules,
        "symbols": symbols,
        "docs": docs,
    }


def build_shard(opts: BuildOptions, i: int, n: int) -> dict[str, Any]:
    """Query one shard of the symbols into ``opts.facts_dir``/facts_<i>.jsonl (resumable)."""
    assert opts.facts_dir is not None
    modules, symbols = extract_modules(opts.root, opts.layout)
    return run_shard(
        opts.root,
        opts.layout,
        opts.server or get_language(opts.layout.lang).default_server,
        modules,
        symbols,
        i,
        n,
        opts.facts_dir / f"facts_{i}.jsonl",
        opts.workers,
        opts.querier_factory,
    )
