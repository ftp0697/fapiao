import os
import sys
from pathlib import Path

import pytest

from fapiao_pdf.scanner import scan_directory, scan_directory_with_warnings


def _relative_paths(base_dir: Path, files: list[Path]) -> list[str]:
    return [f.relative_to(base_dir).as_posix() for f in files]


def test_scan_directory_filters_supported_extensions_case_insensitive(
    tmp_path: Path,
) -> None:
    (tmp_path / "test.jpg").write_text("jpg", encoding="utf-8")
    (tmp_path / "TEST.JPEG").write_text("jpeg", encoding="utf-8")
    (tmp_path / "test.PNG").write_text("png", encoding="utf-8")
    (tmp_path / "test.pdf").write_text("pdf", encoding="utf-8")
    (tmp_path / "unsupported.txt").write_text("txt", encoding="utf-8")
    (tmp_path / "empty_dir").mkdir()

    files = scan_directory(tmp_path)

    assert _relative_paths(tmp_path, files) == [
        "TEST.JPEG",
        "test.jpg",
        "test.pdf",
        "test.PNG",
    ]


def test_scan_directory_returns_stable_sorted_order(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    nested_dir = tmp_path / "nested"
    nested_dir.mkdir()
    jpg_file = tmp_path / "b.jpg"
    jpeg_file = tmp_path / "A.JPEG"
    png_file = nested_dir / "c.PNG"
    pdf_file = tmp_path / "a.pdf"
    txt_file = tmp_path / "ignore.txt"

    for f in [jpg_file, jpeg_file, png_file, pdf_file, txt_file]:
        f.write_text(f.suffix, encoding="utf-8")

    def fake_rglob(self: Path, pattern: str):
        if self == tmp_path and pattern == "*":
            return iter([txt_file, png_file, jpg_file, pdf_file, nested_dir, jpeg_file])
        return Path.rglob(self, pattern)

    monkeypatch.setattr(Path, "rglob", fake_rglob)

    files = scan_directory(tmp_path)

    assert _relative_paths(tmp_path, files) == [
        "A.JPEG",
        "a.pdf",
        "b.jpg",
        "nested/c.PNG",
    ]


def test_scan_directory_warns_for_symlink_when_supported(tmp_path: Path) -> None:
    target = tmp_path / "target.pdf"
    target.write_text("pdf", encoding="utf-8")
    link = tmp_path / "link.pdf"

    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("符号链接在当前平台不可用")

    files, warnings = scan_directory_with_warnings(tmp_path)

    assert target in files
    assert link not in files
    assert f"跳过符号链接: {link}" in warnings


def test_scan_directory_warns_for_non_regular_file(tmp_path: Path) -> None:
    if sys.platform == "win32" or not hasattr(os, "mkfifo"):
        pytest.skip("命名管道在当前平台不可用")

    fifo = tmp_path / "named_pipe.pdf"
    os.mkfifo(fifo)

    files, warnings = scan_directory_with_warnings(tmp_path)

    assert fifo not in files
    assert f"跳过非普通文件: {fifo}" in warnings


def test_scan_directory_with_warnings_returns_empty_for_empty_directory(
    tmp_path: Path,
) -> None:
    files, warnings = scan_directory_with_warnings(tmp_path)

    assert files == []
    assert warnings == []
