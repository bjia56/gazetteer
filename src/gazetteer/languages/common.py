"""Linking steps that work on the language-neutral ``Symbol`` dicts."""

from __future__ import annotations

import collections

from ..model import Symbol


def link_subclasses(symbols: dict[str, Symbol]) -> None:
    by_name: dict[str, list[str]] = collections.defaultdict(list)
    for s in symbols.values():
        if s["kind"] == "class":
            by_name[s["name"]].append(s["id"])
    for s in symbols.values():
        if s["kind"] != "class":
            continue
        for base in s["bases"]:
            for target in by_name.get(base.split(".")[-1].split("[")[0], []):
                if target != s["id"]:
                    symbols[target]["subclasses"].append(s["id"])
