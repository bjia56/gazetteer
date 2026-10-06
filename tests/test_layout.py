"""Layout detection and path rules."""

from __future__ import annotations

from pathlib import Path

import pytest

from gazetteer.layout import Layout, LayoutError


class TestLayoutDetect:
    def test_src_layout(self, sample: Path) -> None:
        lay = Layout.detect(sample)
        assert (lay.pkg_dir, lay.module_root, lay.import_prefix) == ("src/sample", "src", "sample")
        assert lay.tests == ("tests",)

    def test_flat_layout(self, tmp_path: Path) -> None:
        (tmp_path / "pkg").mkdir()
        (tmp_path / "pkg" / "__init__.py").write_text("")
        lay = Layout.detect(tmp_path)
        assert (lay.pkg_dir, lay.module_root, lay.import_prefix) == ("pkg", ".", "pkg")

    def test_no_package_raises(self, tmp_path: Path) -> None:
        with pytest.raises(LayoutError, match="no Python package"):
            Layout.detect(tmp_path)

    def test_several_packages_raise(self, tmp_path: Path) -> None:
        for name in ("a", "b"):
            (tmp_path / name).mkdir()
            (tmp_path / name / "__init__.py").write_text("")
        with pytest.raises(LayoutError, match="several packages"):
            Layout.detect(tmp_path)


class TestLayoutRules:
    def test_is_test(self) -> None:
        lay = Layout("src/x", "src", "x", tests=("tests",))
        assert lay.is_test("tests/test_a.py")
        assert not lay.is_test("src/x/tests_helper.py")

    def test_is_skipped(self) -> None:
        lay = Layout("src/x", "src", "x")
        assert lay.is_skipped("src/x/a_pb2.py")
        assert not lay.is_skipped("src/x/a.py")
