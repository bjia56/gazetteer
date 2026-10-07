"""The language plugin registry."""

from __future__ import annotations

from pathlib import Path

import pytest

from gazetteer.languages import get_language, language_names
from gazetteer.layout import Layout


class TestRegistry:
    def test_python_is_registered(self) -> None:
        assert "python" in language_names()
        assert get_language("python").file_exts == (".py",)

    def test_unknown_language_is_an_error(self) -> None:
        with pytest.raises(ValueError, match="unknown language"):
            get_language("cobol")

    def test_layout_carries_its_language(self, sample: Path, layout: Layout) -> None:
        assert layout.lang == "python"
        assert Layout.detect(sample, "python") == layout
