"""PDF 渲染。"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from fapiao_pdf.layout import (
    A4_HEIGHT_MM,
    fit_into_slot,
    mm_to_pt,
    slot_rects_for_page,
)
from fapiao_pdf.models import LayoutPage


class RenderError(RuntimeError):
    """PDF 渲染失败。"""


def _temp_path_for(target: Path) -> Path:
    fd, name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp.pdf", dir=str(target.parent)
    )
    os.close(fd)
    return Path(name)


def _draw_page(c: canvas.Canvas, page: LayoutPage) -> None:
    rects = slot_rects_for_page(page)
    for slot, rect in zip(page.slots, rects, strict=True):
        image = slot.document.image
        if image is None:
            continue
        placed = fit_into_slot(image.width, image.height, rect)
        # ReportLab 原点位于左下，layout 使用左上坐标；做 Y 轴翻转。
        x_pt = mm_to_pt(placed.x_mm)
        y_pt = mm_to_pt(A4_HEIGHT_MM - placed.y_mm - placed.h_mm)
        w_pt = mm_to_pt(placed.w_mm)
        h_pt = mm_to_pt(placed.h_mm)
        c.drawImage(
            ImageReader(image.convert("RGB")),
            x_pt,
            y_pt,
            width=w_pt,
            height=h_pt,
            preserveAspectRatio=True,
            mask="auto",
        )


def render_pdf(pages: list[LayoutPage], output: Path) -> None:
    """原子渲染：先写临时文件，成功后替换最终文件。"""

    if not pages:
        raise RenderError("没有可渲染的页面。")

    output.parent.mkdir(parents=True, exist_ok=True)
    temp_path = _temp_path_for(output)
    try:
        c = canvas.Canvas(str(temp_path), pagesize=A4)
        try:
            for page in pages:
                _draw_page(c, page)
                c.showPage()
            c.save()
        except Exception:
            try:
                c._filename  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass
            raise
        os.replace(temp_path, output)
    except Exception as exc:  # noqa: BLE001
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass
        raise RenderError(f"PDF 渲染失败：{exc}") from exc
    finally:
        if temp_path.exists() and not output.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass
