"""Embed data.json (gzip + base64) into the page template: one self-contained HTML file."""

from __future__ import annotations

import base64
import gzip
from importlib import resources
from pathlib import Path

PLACEHOLDER = "__DATA_B64__"


def template_text() -> str:
    return resources.files("gazetteer").joinpath("template.html").read_text(encoding="utf-8")


def pack(data: Path, out: Path, template: Path | None = None) -> int:
    """Write the page to ``out``; returns its size in bytes."""
    packed = base64.b64encode(gzip.compress(data.read_bytes(), 9)).decode()
    html = (template.read_text() if template else template_text()).replace(PLACEHOLDER, packed)
    out.write_text(html)
    return len(html.encode())
