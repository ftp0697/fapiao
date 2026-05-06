"""PDF 页面处理。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from PIL import Image


class EncryptedPdfError(RuntimeError):
    """加密 PDF 不支持。"""


class PdfOpenError(RuntimeError):
    """PDF 无法打开。"""


@dataclass(slots=True, frozen=True)
class RenderedPdfPage:
    page_index: int  # 1-based
    image: Image.Image
    display_key: str


def _import_fitz():  # type: ignore[no-untyped-def]
    try:
        import pymupdf  # type: ignore[import-not-found]
        return pymupdf
    except ImportError:  # pragma: no cover
        import fitz  # type: ignore[import-not-found]
        return fitz


@contextmanager
def open_pdf(path: Path):  # type: ignore[no-untyped-def]
    fitz = _import_fitz()
    try:
        doc = fitz.open(path)
    except Exception as exc:  # noqa: BLE001
        raise PdfOpenError(f"PDF 无法打开：{path}") from exc
    try:
        if getattr(doc, "needs_pass", False):
            raise EncryptedPdfError(f"加密PDF不支持，已跳过：{path}")
        yield doc
    finally:
        doc.close()


def iter_pages(path: Path, *, dpi: int) -> Iterator[RenderedPdfPage]:
    """渲染 PDF 每页为 PIL Image；逐页 yield 以控制内存。"""

    with open_pdf(path) as doc:
        for index in range(len(doc)):
            try:
                page = doc.load_page(index)
                pixmap = page.get_pixmap(dpi=dpi)
                image: Image.Image = pixmap.pil_image()
            except Exception as exc:  # noqa: BLE001
                raise PdfPageRenderError(
                    f"PDF 单页渲染失败：{path} #page={index + 1}"
                ) from exc
            yield RenderedPdfPage(
                page_index=index + 1,
                image=image,
                display_key=f"{path}#page={index + 1:04d}",
            )


class PdfPageRenderError(RuntimeError):
    """PDF 单页渲染失败。"""


def validate_dpi(dpi: int) -> int:
    if not 100 <= dpi <= 300:
        raise ValueError("PDF DPI 必须在 100 到 300 之间。")
    return dpi
