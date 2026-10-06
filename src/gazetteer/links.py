"""Cross-links from markdown docs to code, and facts from git history."""

from __future__ import annotations

import collections
import itertools
import subprocess
from pathlib import Path
from typing import Any

from .extract import Module, Symbol
from .layout import Layout

DOC_SNIPPET_CHARS = 200
MIN_DOC_NAME_LEN = 8
MAX_DOC_NAME_DEFS = 2
MAX_COMMIT_FILES = 25
MIN_COCHANGE = 3


def link_docs(
    root: Path, layout: Layout, modules: dict[str, Module], symbols: dict[str, Symbol]
) -> list[dict[str, str]]:
    """Attach doc lines that name a module path, a module id or a rare symbol name."""
    docs = []
    for p in sorted((root / layout.docs_dir).glob("*.md")):
        text = p.read_text(errors="replace")
        title = next((ln[2:].strip() for ln in text.split("\n") if ln.startswith("# ")), p.stem)
        docs.append({"file": f"{layout.docs_dir}/{p.name}", "title": title, "text": text})
    name_count = collections.Counter(s["name"] for s in symbols.values())
    for d in docs:
        lines = d["text"].split("\n")
        for mod in modules.values():
            base = Path(mod["path"]).name
            hits = [
                i
                for i, ln in enumerate(lines)
                if mod["path"] in ln or f"`{mod['id']}`" in ln or (base != "__init__.py" and f"`{base}`" in ln)
            ]
            if hits:
                mod["docs"].append(
                    {
                        "file": d["file"],
                        "title": d["title"],
                        "line": hits[0] + 1,
                        "text": lines[hits[0]].strip()[:DOC_SNIPPET_CHARS],
                    }
                )
        for sym in symbols.values():
            if (
                len(sym["name"]) < MIN_DOC_NAME_LEN
                or name_count[sym["name"]] > MAX_DOC_NAME_DEFS
                or sym["name"].startswith("__")
            ):
                continue
            for i, ln in enumerate(lines):
                if f"`{sym['name']}`" in ln or f"`{sym['name']}(" in ln:
                    sym.setdefault("docs", []).append(
                        {"file": d["file"], "title": d["title"], "line": i + 1, "text": ln.strip()[:DOC_SNIPPET_CHARS]}
                    )
                    break
    for sym in symbols.values():
        sym.setdefault("docs", [])
    return docs


def git_facts(git_dir: Path, layout: Layout, modules: dict[str, Module]) -> dict[str, int]:
    """Per-module commit counts, dates and co-changing files. Returns {} outside a git repository."""
    proc = subprocess.run(
        [
            "git",
            "-C",
            str(git_dir),
            "log",
            "--name-only",
            "--no-renames",
            "--format=@@%H|%ad|%an|%s",
            "--date=short",
            "--",
            layout.pkg_dir,
            *layout.tests,
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return {}
    commits: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    for ln in proc.stdout.split("\n"):
        if ln.startswith("@@"):
            h, d, a, s = ln[2:].split("|", 3)
            cur = {"sha": h, "date": d, "author": a, "subject": s, "files": []}
            commits.append(cur)
        elif ln.strip() and cur is not None:
            cur["files"].append(ln.strip())
    per: dict[str, dict[str, Any]] = {}
    pair: collections.Counter[tuple[str, str]] = collections.Counter()
    for c in commits:
        for f in c["files"]:
            e = per.setdefault(
                f, {"commits": 0, "authors": set(), "last": c["date"], "subject": c["subject"], "first": c["date"]}
            )
            e["commits"] += 1
            e["authors"].add(c["author"])
            e["first"] = c["date"]
        py = sorted({f for f in c["files"] if f.endswith(".py")})
        if 2 <= len(py) <= MAX_COMMIT_FILES:
            pair.update(itertools.combinations(py, 2))
    path_to_mod = {m["path"]: m["id"] for m in modules.values()}
    related: dict[str, list[tuple[int, str]]] = collections.defaultdict(list)
    for (a, b), n in pair.items():
        if n >= MIN_COCHANGE:
            related[a].append((n, b))
            related[b].append((n, a))
    for mod in modules.values():
        stat = per.get(mod["path"])
        if stat:
            mod["git"] = {
                "commits": stat["commits"],
                "authors": len(stat["authors"]),
                "last": stat["last"],
                "first": stat["first"],
                "subject": stat["subject"][:120],
            }
        mod["cochange"] = [
            {"file": f, "n": n, "module": path_to_mod.get(f)}
            for n, f in sorted(related.get(mod["path"], []), reverse=True)[:6]
        ]
    return {"commits_seen": len(commits)}
