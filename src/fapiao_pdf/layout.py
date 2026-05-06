"""版面布局。"""

from dataclasses import dataclass
from typing import Literal

from fapiao_pdf.models import LayoutPage, LayoutSlot, ProcessedDocument

_MM_PER_PT: float = 72.0 / 25.4

A4_WIDTH_MM: float = 210.0
A4_HEIGHT_MM: float = 297.0
PAGE_MARGIN_MM: float = 10.0
INVOICE_GAP_MM: float = 5.0

INVOICE_PER_PAGE: int = 2
ORDER_PER_PAGE: int = 1


@dataclass(slots=True, frozen=True)
class SlotRect:
    """一个槽位的可绘制矩形（mm，原点位于页面左上）。"""

    x_mm: float
    y_mm: float
    w_mm: float
    h_mm: float


@dataclass(slots=True, frozen=True)
class PlacedImage:
    """图像在槽内居中等比缩放后的实际矩形（mm，原点位于页面左上）。"""

    slot: SlotRect
    x_mm: float
    y_mm: float
    w_mm: float
    h_mm: float


def mm_to_pt(value_mm: float) -> float:
    return value_mm * _MM_PER_PT


def _printable_size_mm() -> tuple[float, float]:
    return (
        A4_WIDTH_MM - 2 * PAGE_MARGIN_MM,
        A4_HEIGHT_MM - 2 * PAGE_MARGIN_MM,
    )


def _invoice_slots(slot_count: int) -> list[SlotRect]:
    """1 张占整个可打印区域；2 张上下排列、5mm 间隙。"""

    inner_w, inner_h = _printable_size_mm()
    if slot_count == 1:
        return [SlotRect(PAGE_MARGIN_MM, PAGE_MARGIN_MM, inner_w, inner_h)]
    cell_h = (inner_h - INVOICE_GAP_MM) / 2.0
    top = SlotRect(PAGE_MARGIN_MM, PAGE_MARGIN_MM, inner_w, cell_h)
    bottom = SlotRect(
        PAGE_MARGIN_MM,
        PAGE_MARGIN_MM + cell_h + INVOICE_GAP_MM,
        inner_w,
        cell_h,
    )
    return [top, bottom]


def _order_slots() -> list[SlotRect]:
    inner_w, inner_h = _printable_size_mm()
    return [SlotRect(PAGE_MARGIN_MM, PAGE_MARGIN_MM, inner_w, inner_h)]


def fit_into_slot(image_w_px: int, image_h_px: int, slot: SlotRect) -> PlacedImage:
    """等比缩放并居中放入槽位。"""

    if image_w_px <= 0 or image_h_px <= 0:
        return PlacedImage(slot, slot.x_mm, slot.y_mm, 0.0, 0.0)
    scale = min(slot.w_mm / image_w_px, slot.h_mm / image_h_px)
    drawn_w = image_w_px * scale
    drawn_h = image_h_px * scale
    offset_x = slot.x_mm + (slot.w_mm - drawn_w) / 2.0
    offset_y = slot.y_mm + (slot.h_mm - drawn_h) / 2.0
    return PlacedImage(slot, offset_x, offset_y, drawn_w, drawn_h)


def _chunk(
    docs: list[ProcessedDocument],
    per_page: int,
) -> list[list[ProcessedDocument]]:
    return [docs[i : i + per_page] for i in range(0, len(docs), per_page)]


def _build_page(
    docs: list[ProcessedDocument],
    page_num: int,
    page_type: Literal["invoice", "order"],
) -> LayoutPage:
    return LayoutPage(
        page_num=page_num,
        slots=[
            LayoutSlot(document=doc, slot_index=idx, page_type=page_type)
            for idx, doc in enumerate(docs)
        ],
    )


def plan_pages(docs: list[ProcessedDocument]) -> list[LayoutPage]:
    """按类型分组规划 A4 页面；不同类型不混排。"""

    invoices: list[ProcessedDocument] = [d for d in docs if d.doc_type == "invoice"]
    orders: list[ProcessedDocument] = [d for d in docs if d.doc_type == "order"]

    pages: list[LayoutPage] = []
    page_no: int = 1
    for chunk in _chunk(invoices, INVOICE_PER_PAGE):
        pages.append(_build_page(chunk, page_no, "invoice"))
        page_no += 1
    for chunk in _chunk(orders, ORDER_PER_PAGE):
        pages.append(_build_page(chunk, page_no, "order"))
        page_no += 1
    return pages


def slot_rects_for_page(page: LayoutPage) -> list[SlotRect]:
    """返回某 LayoutPage 各槽位的几何矩形。"""

    if not page.slots:
        return []
    page_type = page.slots[0].page_type
    if page_type == "invoice":
        return _invoice_slots(len(page.slots))
    return _order_slots()
