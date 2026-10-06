"""Call graph and test links per symbol, from a language server's call hierarchy."""

from __future__ import annotations

import ast
import collections
import hashlib
import json
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Protocol

from .extract import FUNCTION_NODES, Module, Symbol
from .layout import Layout
from .lsp import LSP

FACT_FIELDS = ("calls_in", "calls_in_x", "calls_out", "calls_out_x", "tests")
TESTS_PER_FILE = 6


class Querier(Protocol):
    requests: int
    init_seconds: float

    @property
    def timeouts(self) -> int: ...

    def query(self, sym: Symbol) -> None: ...
    def run(self, syms: list[Symbol], workers: int = ...) -> None: ...
    def close(self) -> None: ...


QuerierFactory = Callable[[Path, Layout, "tuple[str, ...]", dict[str, Module], dict[str, Symbol]], Querier]


class LspQuerier:
    """Call-hierarchy queries against one language-server session; fills the fact fields of symbols."""

    def __init__(
        self, root: Path, layout: Layout, cmd: tuple[str, ...], modules: dict[str, Module], symbols: dict[str, Symbol]
    ) -> None:
        t0 = time.time()
        self.root, self.layout, self.symbols = root.resolve(), layout, symbols
        self.lsp = LSP(root, cmd)
        for m in modules.values():
            self.lsp.open(m["path"])
        self.init_seconds = time.time() - t0
        self.loc = {(s["file"], s["line"]): s["id"] for s in symbols.values()}
        self.spans: dict[str, list[tuple[int, int, int, str]]] = collections.defaultdict(list)
        for s in symbols.values():
            self.spans[s["file"]].append((s["end"] - s["line"], s["line"], s["end"], s["id"]))
        self.requests = 0

    @property
    def timeouts(self) -> int:
        return self.lsp.timeouts

    def enclosing(self, rel: str, line: int) -> str | None:
        best: tuple[int, str] | None = None
        for width, a, b, sid in self.spans.get(rel, []):
            if a <= line <= b and (best is None or width < best[0]):
                best = (width, sid)
        return best[1] if best else None

    def classify(self, item: dict[str, Any]) -> tuple[str, ...]:
        rel = self.lsp.rel(item["uri"])
        line = item["selectionRange"]["start"]["line"] + 1
        if rel and (rel, line) in self.loc:
            return ("sym", self.loc[(rel, line)])
        if rel and self.layout.is_test(rel):
            return ("test", rel, item["name"])
        if rel and (enc := self.enclosing(rel, line)):
            return ("sym", enc)  # lambda or nested function: attribute to the enclosing symbol
        if rel:
            return ("local", f"{rel}:{line}", item["name"])
        return ("ext", item["name"])

    def query(self, sym: Symbol) -> None:
        lsp = self.lsp
        items = (
            lsp.request(
                "textDocument/prepareCallHierarchy",
                {
                    "textDocument": {"uri": (self.root / sym["file"]).as_uri()},
                    "position": {"line": sym["line"] - 1, "character": sym["col"]},
                },
            )
            or []
        )
        calls_in: set[str] = set()
        calls_in_x: list[dict[str, str]] = []
        calls_out: set[str] = set()
        calls_out_x: list[str] = []
        tests: dict[str, set[str]] = collections.defaultdict(set)
        self.requests += 1
        for it in items[:1]:
            self.requests += 2
            for c in lsp.request("callHierarchy/incomingCalls", {"item": it}) or []:
                k = self.classify(c["from"])
                if k[0] == "sym":
                    calls_in.add(k[1])
                elif k[0] == "test":
                    tests[k[1]].add(k[2])
                elif k[0] == "local":
                    calls_in_x.append({"name": k[2], "at": k[1]})
            for c in lsp.request("callHierarchy/outgoingCalls", {"item": it}) or []:
                k = self.classify(c["to"])
                if k[0] == "sym":
                    calls_out.add(k[1])
                elif k[0] in ("ext", "local"):
                    calls_out_x.append(k[-1])
        sym["calls_in"] = sorted(calls_in - {sym["id"]})
        sym["calls_in_x"] = calls_in_x[:12]
        sym["calls_out"] = sorted(calls_out - {sym["id"]})
        sym["calls_out_x"] = sorted(set(calls_out_x))[:25]
        sym["tests"] = {f: sorted(n)[:TESTS_PER_FILE] for f, n in tests.items()}

    def run(self, syms: list[Symbol], workers: int = 8) -> None:
        with ThreadPoolExecutor(workers) as ex:
            list(ex.map(self.query, syms))

    def close(self) -> None:
        self.lsp.close()


def _sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


# ----------------------------------------------------------------------- tests


def aggregate_tests(modules: dict[str, Module], symbols: dict[str, Symbol]) -> None:
    for mod in modules.values():
        agg: dict[str, int] = collections.defaultdict(int)
        for sid in mod["symbols"]:
            for f, names in symbols[sid]["tests"].items():
                agg[f] += len(names)
        mod["tests"] = dict(sorted(agg.items(), key=lambda kv: -kv[1]))


def _used_names(node: ast.AST) -> set[str]:
    return {x.id for x in ast.walk(node) if isinstance(x, ast.Name)} | {
        x.attr for x in ast.walk(node) if isinstance(x, ast.Attribute)
    }


def name_based_tests(
    root: Path, layout: Layout, symbols: dict[str, Symbol], max_defs: int = 3, min_len: int = 5
) -> dict[str, int]:
    """Approximate test links: a test function covers a symbol when it uses the symbol's (rare) name.

    Used when tests stay out of the language server's project for speed.
    """
    by_name: dict[str, list[str]] = collections.defaultdict(list)
    for sid, s in symbols.items():
        by_name[s["name"]].append(sid)
    usable = {
        n: ids for n, ids in by_name.items() if len(n) >= min_len and len(ids) <= max_defs and not n.startswith("__")
    }
    hits: dict[str, dict[str, set[str]]] = collections.defaultdict(lambda: collections.defaultdict(set))
    files = 0
    for t in layout.tests:
        for p in sorted((root / t).rglob("*.py")):
            rel = p.relative_to(root).as_posix()
            try:
                tree = ast.parse(p.read_text(errors="replace"))
            except (SyntaxError, ValueError):
                continue
            files += 1
            for node in ast.walk(tree):
                if isinstance(node, FUNCTION_NODES) and node.name.startswith("test"):
                    for n in _used_names(node) & usable.keys():
                        for sid in usable[n]:
                            hits[sid][rel].add(node.name)
    for s in symbols.values():
        s["tests"] = {f: sorted(ns)[:TESTS_PER_FILE] for f, ns in hits.get(s["id"], {}).items()}
    return {"test_files": files, "symbols_with_tests": len(hits)}


# ------------------------------------------------------------------ full build


def call_graph(
    root: Path,
    layout: Layout,
    cmd: tuple[str, ...],
    modules: dict[str, Module],
    symbols: dict[str, Symbol],
    workers: int = 8,
    querier_factory: QuerierFactory = LspQuerier,
) -> dict[str, Any]:
    q = querier_factory(root, layout, cmd, modules, symbols)
    t0 = time.time()
    q.run(list(symbols.values()), workers)
    info = {
        "mode": "full",
        "queried": len(symbols),
        "requests": q.requests,
        "init_s": round(q.init_seconds, 1),
        "query_s": round(time.time() - t0, 1),
    }
    q.close()
    aggregate_tests(modules, symbols)
    return info


# ---------------------------------------------------------------------- shards


def shard_symbols(symbols: dict[str, Symbol], i: int, n: int) -> list[Symbol]:
    """Assign whole modules to shards, balanced by symbol count, so related queries share server caches."""
    mods: dict[str, list[Symbol]] = collections.defaultdict(list)
    for s in symbols.values():
        mods[s["module"]].append(s)
    loads = [0] * n
    assign: dict[str, int] = {}
    for m, ss in sorted(mods.items(), key=lambda kv: -len(kv[1])):
        k = loads.index(min(loads))
        assign[m] = k
        loads[k] += len(ss)
    return [s for m, ss in mods.items() if assign[m] == i for s in ss]


def run_shard(
    root: Path,
    layout: Layout,
    cmd: tuple[str, ...],
    modules: dict[str, Module],
    symbols: dict[str, Symbol],
    i: int,
    n: int,
    out: Path,
    workers: int = 8,
    querier_factory: QuerierFactory = LspQuerier,
) -> dict[str, Any]:
    """Query shard ``i`` of ``n``, appending each result to a JSONL file so a long run can resume."""
    out.parent.mkdir(parents=True, exist_ok=True)
    done: set[str] = set()
    if out.exists():
        done = {json.loads(line)["id"] for line in out.read_text().split("\n") if line.strip()}
    todo = [s for s in shard_symbols(symbols, i, n) if s["id"] not in done]
    q = querier_factory(root, layout, cmd, modules, symbols)
    lock, count, t0 = threading.Lock(), [0], time.time()

    with out.open("a") as fh:

        def work(sym: Symbol) -> None:
            q.query(sym)
            with lock:
                fh.write(json.dumps({"id": sym["id"], **{k: sym[k] for k in FACT_FIELDS}}) + "\n")
                fh.flush()
                count[0] += 1
                if count[0] % 100 == 0:
                    el = time.time() - t0
                    print(
                        f"  shard {i}/{n}: {count[0]}/{len(todo)} in {el:.0f}s "
                        f"(eta {el / count[0] * (len(todo) - count[0]):.0f}s)",
                        flush=True,
                    )

        with ThreadPoolExecutor(workers) as ex:
            list(ex.map(work, todo))
    info = {
        "shard": f"{i}/{n}",
        "queried": len(todo),
        "resumed": len(done),
        "seconds": round(time.time() - t0, 1),
        "requests": q.requests,
        "timeouts": q.timeouts,
    }
    q.close()
    return info


def apply_facts(symbols: dict[str, Symbol], facts_dir: Path) -> int:
    n = 0
    for f in sorted(facts_dir.glob("facts_*.jsonl")):
        for line in f.read_text().split("\n"):
            if line.strip():
                r = json.loads(line)
                if r["id"] in symbols:
                    for k in FACT_FIELDS:
                        symbols[r["id"]][k] = r[k]
                    n += 1
    return n


# ----------------------------------------------------------------- incremental


def _func_table(path: Path) -> tuple[dict[str, tuple[str, set[str], str]], str]:
    """Qualified function name -> (hash, names used, bare name); plus a hash of the code outside functions."""
    text = path.read_text(errors="replace")
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return {}, "syntax-error"
    funcs: dict[str, tuple[str, set[str], str]] = {}

    def walk(body: list[ast.stmt], prefix: str = "") -> None:
        for n in body:
            if isinstance(n, FUNCTION_NODES):
                funcs[prefix + n.name] = (_sha1(ast.dump(n).encode()), _used_names(n), n.name)
                walk(n.body, prefix + n.name + ".")  # nested helpers are call-hierarchy items of their own
            elif isinstance(n, ast.ClassDef):
                walk(n.body, prefix + n.name + ".")
            else:
                for field in ("body", "orelse", "finalbody", "handlers"):
                    for child in getattr(n, field, None) or []:
                        if isinstance(child, (*FUNCTION_NODES, ast.ClassDef)):
                            walk([child], prefix)
                        elif hasattr(child, "body"):
                            walk(child.body, prefix)

    walk(tree.body)

    class Strip(ast.NodeTransformer):
        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            return None  # functions compare individually; adding or removing one is not a residual change

        visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

    residual = _sha1(ast.dump(Strip().visit(ast.parse(text))).encode())
    return funcs, residual


def _file_hashes(root: Path, rels: list[str]) -> dict[str, str]:
    return {rel: _sha1((root / rel).read_bytes()) for rel in rels}


def _test_hashes(root: Path, layout: Layout) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): _sha1(p.read_bytes()) for t in layout.tests for p in (root / t).rglob("*.py")
    }


def _coarse_names(path: Path) -> set[str]:
    names: set[str] = set()
    try:
        for node in ast.walk(ast.parse(path.read_text(errors="replace"))):
            if isinstance(node, ast.Name):
                names.add(node.id)
            elif isinstance(node, ast.Attribute):
                names.add(node.attr)
            elif isinstance(node, ast.alias):
                names.add(node.name.split(".")[-1])
    except SyntaxError:
        pass
    return names


def _changed_test_names(
    root: Path, prev_root: Path, tchanged: set[str], pth: dict[str, str], nth: dict[str, str]
) -> tuple[set[str], set[str]]:
    """Names used inside changed test functions, and names of test functions that went away."""
    names: set[str] = set()
    old_names: set[str] = set()
    for p in tchanged:
        if p in pth and p in nth:
            of, ores = _func_table(prev_root / p)
            nf, nres = _func_table(root / p)
            if ores == nres:
                for q, v in nf.items():
                    if q not in of or of[q][0] != v[0]:
                        names |= v[1]
                old_names |= {v[2] for q, v in of.items() if q not in nf or nf[q][0] != v[0]}
            else:
                names |= _coarse_names(root / p)  # code outside functions changed: names used anywhere
        elif p in nth:  # new test file: every function is new
            nf, _ = _func_table(root / p)
            for v in nf.values():
                names |= v[1]
        else:  # removed test file
            of, _ = _func_table(prev_root / p)
            old_names |= {v[2] for v in of.values()}
    return names, old_names


def incremental_call_graph(
    root: Path,
    layout: Layout,
    cmd: tuple[str, ...],
    modules: dict[str, Module],
    symbols: dict[str, Symbol],
    prev: dict[str, Any],
    prev_root: Path,
    workers: int = 8,
    querier_factory: QuerierFactory = LspQuerier,
) -> dict[str, Any]:
    """Re-query only the symbols a diff can affect; carry over every other fact from the previous build."""
    ps: dict[str, Symbol] = prev["symbols"]
    prev_files = sorted({m["path"] for m in prev["modules"].values()})
    ph = _file_hashes(prev_root, prev_files)
    nh = _file_hashes(root, sorted({m["path"] for m in modules.values()}))
    changed = {p for p, h in nh.items() if ph.get(p) != h}
    removed = {p for p in ph if p not in nh}
    if all("h" in p for p in ps.values()):
        a_new = {sid for sid, s in symbols.items() if sid not in ps or ps[sid].get("h") != s["h"]}
        a_old = {sid for sid, p in ps.items() if sid not in symbols or symbols[sid]["h"] != p.get("h")}
    else:  # previous data predates symbol hashes: fall back to whole files
        a_new = {sid for sid, s in symbols.items() if s["file"] in changed}
        a_old = {sid for sid, s in ps.items() if s["file"] in changed | removed}

    for sid, s in symbols.items():  # carry over unchanged facts, dropping edges to vanished symbols
        if sid in a_new or sid not in ps:
            continue
        p = ps[sid]
        s["calls_in"] = [x for x in p["calls_in"] if x in symbols]
        s["calls_out"] = [x for x in p["calls_out"] if x in symbols]
        for k in ("calls_in_x", "calls_out_x", "tests"):
            s[k] = p[k]

    # changed tests can add or drop calls into symbols: requery the symbols they name or used to reach
    pth, nth = _test_hashes(prev_root, layout), _test_hashes(root, layout)
    tchanged = {p for p, h in nth.items() if pth.get(p) != h} | {p for p in pth if p not in nth}
    names, old_names = _changed_test_names(root, prev_root, tchanged, pth, nth)
    by_name: dict[str, list[str]] = collections.defaultdict(list)
    for sid, s in symbols.items():
        by_name[s["name"]].append(sid)
    test_hit = {sid for n in names for sid in by_name.get(n, [])}
    for sid in symbols:
        if sid in ps:
            for f, ns in ps[sid]["tests"].items():
                if f in tchanged and (len(ns) >= TESTS_PER_FILE or old_names & set(ns)):
                    test_hit.add(sid)

    # a symbol that appears or disappears can re-route calls elsewhere (new override, shadowed name)
    appeared = {symbols[x]["name"] for x in symbols if x not in ps} | {ps[x]["name"] for x in ps if x not in symbols}
    shadow = {
        sid
        for sid, p in ps.items()
        if sid in symbols and any(ps.get(c, {}).get("name") in appeared for c in p["calls_out"])
    }

    near = test_hit | shadow
    for sid in a_old:
        near.update(ps[sid]["calls_out"])
        near.update(ps[sid]["calls_in"])
    first = a_new | {x for x in near if x in symbols}

    q = querier_factory(root, layout, cmd, modules, symbols)
    t0 = time.time()
    q.run([symbols[x] for x in sorted(first)], workers)
    after = {x for sid in a_new for x in symbols[sid]["calls_out"] if x not in first}  # callees of new code
    q.run([symbols[x] for x in sorted(after)], workers)
    info = {
        "mode": "incremental",
        "changed_files": len(changed),
        "removed_files": len(removed),
        "changed_tests": len(tchanged),
        "test_hit": len(test_hit),
        "queried": len(first) + len(after),
        "requests": q.requests,
        "init_s": round(q.init_seconds, 1),
        "query_s": round(time.time() - t0, 1),
    }
    q.close()
    aggregate_tests(modules, symbols)
    return info
