import io
from pathlib import Path

from fapiao_pdf.models import LogicalInput, ProcessedDocument
from fapiao_pdf.stats import (
    ProgressReporter,
    aggregate,
    emit_warning,
    format_summary,
)


def _doc(key: str, doc_type: str, ocr_fail: bool = False) -> ProcessedDocument:
    return ProcessedDocument(
        original=LogicalInput(path=Path(key), display_key=key, doc_type="image"),
        image=None,  # type: ignore[arg-type]
        doc_type=doc_type,  # type: ignore[arg-type]
        date=None,
        ocr_failure=ocr_fail,
        warnings=[],
    )


def test_aggregate_counts_match() -> None:
    docs = [
        _doc("a", "invoice"),
        _doc("b", "invoice"),
        _doc("c", "order"),
        _doc("d", "order", ocr_fail=True),
    ]
    snap = aggregate(docs)
    assert snap.processed == 4
    assert snap.invoices == 2
    assert snap.orders == 2
    assert snap.ocr_failures == 1


def test_format_summary_chinese_and_path() -> None:
    snap = aggregate([_doc("a", "invoice")])
    out = format_summary(snap, Path("/tmp/x.pdf"))
    assert "共处理 1" in out
    assert "发票 1" in out
    assert "订单 0" in out
    assert "OCR 失败 0" in out
    assert "输出至 /tmp/x.pdf" in out or "输出至 \\tmp\\x.pdf" in out


def test_progress_reporter_non_tty_writes_lines() -> None:
    buf = io.StringIO()
    reporter = ProgressReporter(total=2, stream=buf)
    reporter.advance("a")
    reporter.advance("b")
    reporter.finish()
    text = buf.getvalue()
    assert "处理中 1/2 - a" in text
    assert "处理中 2/2 - b" in text


def test_emit_warning_writes_to_stream() -> None:
    buf = io.StringIO()
    emit_warning("加密PDF不支持", stream=buf)
    assert "加密PDF不支持" in buf.getvalue()
