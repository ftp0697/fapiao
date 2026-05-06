"""统计汇总。"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from fapiao_pdf.models import ProcessedDocument


@dataclass(slots=True)
class StatsSnapshot:
    processed: int
    invoices: int
    orders: int
    ocr_failures: int


def aggregate(docs: list[ProcessedDocument]) -> StatsSnapshot:
    invoices = sum(1 for d in docs if d.doc_type == "invoice")
    orders = sum(1 for d in docs if d.doc_type == "order")
    ocr_failures = sum(1 for d in docs if d.ocr_failure)
    return StatsSnapshot(
        processed=len(docs),
        invoices=invoices,
        orders=orders,
        ocr_failures=ocr_failures,
    )


def format_summary(snapshot: StatsSnapshot, output: Path | None) -> str:
    rendered = str(output) if output is not None else ""
    return (
        f"共处理 {snapshot.processed} 张，"
        f"发票 {snapshot.invoices}，"
        f"订单 {snapshot.orders}，"
        f"OCR 失败 {snapshot.ocr_failures}，"
        f"输出至 {rendered}"
    )


class ProgressReporter:
    """TTY 显示动态行；非 TTY 走简单换行输出。"""

    __slots__ = ("_total", "_count", "_stream", "_is_tty")

    def __init__(self, total: int, *, stream: TextIO | None = None) -> None:
        self._total = total
        self._count = 0
        self._stream = stream if stream is not None else sys.stdout
        self._is_tty = bool(getattr(self._stream, "isatty", lambda: False)())

    def advance(self, stage: str) -> None:
        self._count += 1
        if self._is_tty:
            self._stream.write(f"\r处理中 {self._count}/{self._total} - {stage}")
            self._stream.flush()
        else:
            self._stream.write(f"处理中 {self._count}/{self._total} - {stage}\n")

    def finish(self) -> None:
        if self._is_tty:
            self._stream.write("\n")
            self._stream.flush()


def emit_warning(message: str, *, stream: TextIO | None = None) -> None:
    """中文警告统一进入 stderr；不输出敏感字段。"""

    target = stream if stream is not None else sys.stderr
    target.write(f"{message}\n")
