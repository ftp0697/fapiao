from datetime import date

import pytest
from hypothesis import given
from hypothesis import strategies as st

from fapiao_pdf.classifier import classify
from fapiao_pdf.date_parser import parse_first_valid_date
from fapiao_pdf.models import LogicalInput, ProcessedDocument
from fapiao_pdf.ordering import sort_documents


def _doc(
    key: str,
    doc_type: str,
    d: date | None,
    *,
    ocr_failure: bool = False,
) -> ProcessedDocument:
    return ProcessedDocument(
        original=LogicalInput(
            path=__import__("pathlib").Path(key),
            display_key=key,
            doc_type="image",
        ),
        image=None,  # type: ignore[arg-type]
        doc_type=doc_type,  # type: ignore[arg-type]
        date=d,
        ocr_failure=ocr_failure,
        warnings=[],
    )


# ----- classifier -----


@pytest.mark.parametrize(
    "text",
    ["这是发票文本", "包含税额信息", "价税合计 100", "发票号码 123", "发票代码 ABC"],
)
def test_classify_invoice_keyword_wins(text: str) -> None:
    doc_type, warn = classify(text)
    assert doc_type == "invoice"
    assert warn is None


@pytest.mark.parametrize(
    "text",
    ["订单详情", "订单号 456", "订单编号 XYZ", "商品清单如下", "收货地址：北京"],
)
def test_classify_order_keyword(text: str) -> None:
    doc_type, warn = classify(text)
    assert doc_type == "order"
    assert warn is None


def test_classify_invoice_overrides_order_when_both_present() -> None:
    doc_type, warn = classify("订单号 1\n发票号码 2")
    assert doc_type == "invoice"
    assert warn is None


def test_classify_unknown_falls_back_to_order_with_warning() -> None:
    doc_type, warn = classify("无法识别的文本")
    assert doc_type == "order"
    assert warn is not None
    assert "类型识别失败" in warn


# ----- date parser -----


@pytest.mark.parametrize(
    "text,expected",
    [
        ("开票日期 2024-03-05", date(2024, 3, 5)),
        ("日期：2024/3/5", date(2024, 3, 5)),
        ("日期 2024.03.05 完", date(2024, 3, 5)),
        ("2024年3月5日 开", date(2024, 3, 5)),
        ("2024年12月31日", date(2024, 12, 31)),
    ],
)
def test_parse_first_valid_date_supports_required_formats(
    text: str, expected: date
) -> None:
    assert parse_first_valid_date(text) == expected


def test_parse_first_valid_date_skips_invalid_calendar_dates() -> None:
    assert parse_first_valid_date("2024-13-40 然后 2024-02-15") == date(2024, 2, 15)


def test_parse_first_valid_date_chooses_earliest_in_text_stream() -> None:
    text = "首发 2024-05-01\n再 2023-01-01"
    assert parse_first_valid_date(text) == date(2024, 5, 1)


def test_parse_first_valid_date_returns_none_when_absent() -> None:
    assert parse_first_valid_date("无日期文本") is None


@given(
    year=st.integers(min_value=1000, max_value=9999),
    month=st.integers(min_value=1, max_value=12),
    day=st.integers(min_value=1, max_value=31),
)
def test_date_parser_total_on_well_formed_strings(year: int, month: int, day: int) -> None:
    result = parse_first_valid_date(f"{year}-{month}-{day}")
    try:
        expected: date | None = date(year, month, day)
    except ValueError:
        expected = None
    assert result == expected


# ----- ordering -----


def test_sort_invoice_before_order() -> None:
    a = _doc("a.png", "order", date(2024, 1, 1))
    b = _doc("b.png", "invoice", date(2024, 6, 1))
    out = sort_documents([a, b])
    assert [d.original.display_key for d in out] == ["b.png", "a.png"]


def test_sort_within_group_by_date_then_displaykey() -> None:
    docs = [
        _doc("c.png", "invoice", date(2024, 5, 1)),
        _doc("a.png", "invoice", date(2024, 1, 1)),
        _doc("b.png", "invoice", date(2024, 1, 1)),
    ]
    out = sort_documents(docs)
    assert [d.original.display_key for d in out] == ["a.png", "b.png", "c.png"]


def test_sort_undated_at_group_tail() -> None:
    docs = [
        _doc("z.png", "invoice", None),
        _doc("a.png", "invoice", date(2024, 5, 1)),
        _doc("b.png", "order", None),
        _doc("c.png", "order", date(2024, 1, 1)),
    ]
    out = sort_documents(docs)
    assert [d.original.display_key for d in out] == [
        "a.png",
        "z.png",
        "c.png",
        "b.png",
    ]


@given(seed=st.integers())
def test_sort_is_deterministic_under_shuffle(seed: int) -> None:
    import random

    base = [
        _doc("a.png", "invoice", date(2024, 1, 1)),
        _doc("b.png", "invoice", date(2024, 2, 1)),
        _doc("c.png", "invoice", None),
        _doc("d.png", "order", date(2024, 1, 1)),
        _doc("e.png", "order", None),
    ]
    rnd = random.Random(seed)
    shuffled = base.copy()
    rnd.shuffle(shuffled)
    out = sort_documents(shuffled)
    expected = sort_documents(base)
    assert [d.original.display_key for d in out] == [
        d.original.display_key for d in expected
    ]
