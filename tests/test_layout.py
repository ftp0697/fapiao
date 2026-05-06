from datetime import date
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

from fapiao_pdf.layout import (
    A4_HEIGHT_MM,
    A4_WIDTH_MM,
    INVOICE_GAP_MM,
    PAGE_MARGIN_MM,
    fit_into_slot,
    plan_pages,
    slot_rects_for_page,
)
from fapiao_pdf.models import LogicalInput, ProcessedDocument


def _doc(key: str, doc_type: str) -> ProcessedDocument:
    return ProcessedDocument(
        original=LogicalInput(path=Path(key), display_key=key, doc_type="image"),
        image=None,  # type: ignore[arg-type]
        doc_type=doc_type,  # type: ignore[arg-type]
        date=date(2024, 1, 1),
        ocr_failure=False,
        warnings=[],
    )


def test_invoices_chunked_two_per_page() -> None:
    docs = [_doc(f"i{i}.png", "invoice") for i in range(5)]
    pages = plan_pages(docs)
    assert [len(p.slots) for p in pages] == [2, 2, 1]
    for page in pages:
        for slot in page.slots:
            assert slot.page_type == "invoice"


def test_orders_one_per_page() -> None:
    docs = [_doc(f"o{i}.png", "order") for i in range(3)]
    pages = plan_pages(docs)
    assert len(pages) == 3
    for page in pages:
        assert len(page.slots) == 1
        assert page.slots[0].page_type == "order"


def test_invoice_pages_precede_order_pages() -> None:
    docs = [
        _doc("o1.png", "order"),
        _doc("i1.png", "invoice"),
        _doc("o2.png", "order"),
        _doc("i2.png", "invoice"),
    ]
    pages = plan_pages(docs)
    types = [page.slots[0].page_type for page in pages]
    assert types == ["invoice", "order", "order"]


def test_no_mixed_type_pages() -> None:
    docs = [
        _doc("i1.png", "invoice"),
        _doc("o1.png", "order"),
    ]
    pages = plan_pages(docs)
    for page in pages:
        types = {slot.page_type for slot in page.slots}
        assert len(types) == 1


def test_invoice_double_slots_have_5mm_gap() -> None:
    pages = plan_pages([_doc("a.png", "invoice"), _doc("b.png", "invoice")])
    rects = slot_rects_for_page(pages[0])
    assert len(rects) == 2
    top, bottom = rects
    gap = bottom.y_mm - (top.y_mm + top.h_mm)
    assert abs(gap - INVOICE_GAP_MM) < 1e-6


def test_order_slot_uses_full_printable_area() -> None:
    pages = plan_pages([_doc("o.png", "order")])
    rect = slot_rects_for_page(pages[0])[0]
    assert abs(rect.x_mm - PAGE_MARGIN_MM) < 1e-6
    assert abs(rect.y_mm - PAGE_MARGIN_MM) < 1e-6
    assert abs(rect.w_mm - (A4_WIDTH_MM - 2 * PAGE_MARGIN_MM)) < 1e-6
    assert abs(rect.h_mm - (A4_HEIGHT_MM - 2 * PAGE_MARGIN_MM)) < 1e-6


@given(
    img_w=st.integers(min_value=1, max_value=4000),
    img_h=st.integers(min_value=1, max_value=4000),
    is_double=st.booleans(),
    is_invoice=st.booleans(),
)
def test_fit_preserves_aspect_and_stays_inside_slot(
    img_w: int, img_h: int, is_double: bool, is_invoice: bool
) -> None:
    if is_invoice:
        docs = [_doc("a.png", "invoice"), _doc("b.png", "invoice")] if is_double else [
            _doc("a.png", "invoice")
        ]
    else:
        docs = [_doc("o.png", "order")]
    page = plan_pages(docs)[0]
    rects = slot_rects_for_page(page)
    rect = rects[0]
    placed = fit_into_slot(img_w, img_h, rect)

    assert placed.x_mm >= rect.x_mm - 1e-6
    assert placed.y_mm >= rect.y_mm - 1e-6
    assert placed.x_mm + placed.w_mm <= rect.x_mm + rect.w_mm + 1e-6
    assert placed.y_mm + placed.h_mm <= rect.y_mm + rect.h_mm + 1e-6
    if placed.w_mm > 0 and placed.h_mm > 0:
        original_ratio = img_w / img_h
        placed_ratio = placed.w_mm / placed.h_mm
        assert abs(original_ratio - placed_ratio) < 1e-3
