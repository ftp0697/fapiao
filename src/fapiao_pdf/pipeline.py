"""合并流程编排。"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from PIL import Image

from fapiao_pdf import classifier, date_parser, image_io, layout, ocr as ocr_mod
from fapiao_pdf import ordering, pdf_pages, renderer, scanner, splitter, stats
from fapiao_pdf.models import (
    LogicalInput,
    OcrResult,
    ProcessedDocument,
)


@dataclass(slots=True)
class RunStats:
    processed: int
    invoices: int
    orders: int
    ocr_failures: int
    output_path: Path | None = None


class NoProcessableInputError(Exception):
    """未发现支持文件或所有文件均无法处理。"""


class OcrModelMissingError(Exception):
    """OCR 模型不可用。"""


class FatalRunError(Exception):
    """致命运行错误。"""


@dataclass(slots=True)
class _LogicalImage:
    logical: LogicalInput
    image: Image.Image


def ensure_ocr_ready(*, allow_download: bool) -> None:
    try:
        ocr_mod.ensure_ocr_ready(allow_download=allow_download)
    except ocr_mod.OcrModelMissingError as exc:
        raise OcrModelMissingError(str(exc)) from exc


def _emit(message: str, stream: TextIO | None = None) -> None:
    stats.emit_warning(message, stream=stream)


def _expand_pdf(
    path: Path, *, dpi: int, stderr: TextIO
) -> Iterator[_LogicalImage]:
    try:
        pages = pdf_pages.iter_pages(path, dpi=dpi)
    except pdf_pages.EncryptedPdfError as exc:
        _emit(str(exc), stderr)
        return
    except pdf_pages.PdfOpenError as exc:
        _emit(str(exc), stderr)
        return

    while True:
        try:
            page = next(pages)
        except StopIteration:
            return
        except pdf_pages.PdfPageRenderError as exc:
            _emit(str(exc), stderr)
            continue
        except Exception as exc:  # noqa: BLE001
            _emit(f"PDF 处理异常：{path} ({exc})", stderr)
            return
        yield _LogicalImage(
            logical=LogicalInput(
                path=path, display_key=page.display_key, doc_type="pdf_page"
            ),
            image=page.image,
        )


def _expand_image(path: Path, *, stderr: TextIO) -> Iterator[_LogicalImage]:
    try:
        img = image_io.load_image(path)
    except image_io.ImageLoadError as exc:
        _emit(str(exc), stderr)
        return
    yield _LogicalImage(
        logical=LogicalInput(path=path, display_key=str(path), doc_type="image"),
        image=img,
    )


def _expand_input(
    path: Path, *, dpi: int, stderr: TextIO
) -> Iterator[_LogicalImage]:
    if path.suffix.lower() == ".pdf":
        yield from _expand_pdf(path, dpi=dpi, stderr=stderr)
    else:
        yield from _expand_image(path, stderr=stderr)


def _split_or_keep(item: _LogicalImage, *, stderr: TextIO) -> list[_LogicalImage]:
    crops, warning = splitter.split_page(item.image, item.logical.path)
    if warning is not None:
        _emit(f"切分警告：{item.logical.display_key} - {warning}", stderr)
    if crops is None:
        return [item]
    expanded: list[_LogicalImage] = []
    for crop in crops:
        crop_logical = LogicalInput(
            path=item.logical.path,
            display_key=crop.display_key,
            doc_type="pdf_page_crop",
        )
        expanded.append(_LogicalImage(logical=crop_logical, image=crop.image))
    return expanded


def _process_document(
    item: _LogicalImage,
    engine: ocr_mod.OcrEngine,
    *,
    stderr: TextIO,
) -> ProcessedDocument:
    ocr_result: OcrResult = engine.recognize(item.image)
    warnings: list[str] = []
    if not ocr_result.success:
        if ocr_result.error:
            _emit(
                f"OCR 失败：{item.logical.display_key} ({ocr_result.error})", stderr
            )
        return ProcessedDocument(
            original=item.logical,
            image=item.image,
            doc_type="order",
            date=None,
            ocr_failure=True,
            warnings=warnings,
        )

    doc_type, classify_warning = classifier.classify(ocr_result.text)
    if classify_warning is not None:
        message = f"{classify_warning}：{item.logical.display_key}"
        _emit(message, stderr)
        warnings.append(message)
    parsed_date = date_parser.parse_first_valid_date(ocr_result.text)
    return ProcessedDocument(
        original=item.logical,
        image=item.image,
        doc_type=doc_type,
        date=parsed_date,
        ocr_failure=False,
        warnings=warnings,
    )


def _resolve_engine(provided: ocr_mod.OcrEngine | None) -> ocr_mod.OcrEngine:
    if provided is not None:
        return provided
    try:
        return ocr_mod.build_default_engine()
    except ocr_mod.OcrModelMissingError as exc:
        raise OcrModelMissingError(str(exc)) from exc


def run_merge(
    input_dir: Path,
    output: Path,
    *,
    force: bool,
    pdf_dpi: int,
    workers: int,
    engine: ocr_mod.OcrEngine | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> RunStats:
    """主入口；workers 暂保留参数以兼容 CLI（MVP 串行执行）。"""

    _ = workers  # 保留签名兼容
    out_stream: TextIO = stdout if stdout is not None else sys.stdout
    err_stream: TextIO = stderr if stderr is not None else sys.stderr
    _ = force  # CLI 已校验覆盖语义；pipeline 直接信任传入的 output 可写

    files, scan_warnings = scanner.scan_directory_with_warnings(input_dir)
    for warn in scan_warnings:
        _emit(warn, err_stream)
    if not files:
        raise NoProcessableInputError(f"未在目录中发现可处理文件：{input_dir}")

    try:
        pdf_pages.validate_dpi(pdf_dpi)
    except ValueError as exc:
        raise FatalRunError(str(exc)) from exc

    expanded: list[_LogicalImage] = []
    for path in files:
        for item in _expand_input(path, dpi=pdf_dpi, stderr=err_stream):
            expanded.extend(_split_or_keep(item, stderr=err_stream))

    if not expanded:
        raise NoProcessableInputError("所有文件均无法处理或为空。")

    resolved_engine = _resolve_engine(engine)
    progress = stats.ProgressReporter(total=len(expanded), stream=out_stream)

    processed: list[ProcessedDocument] = []
    for item in expanded:
        progress.advance(stage=item.logical.display_key)
        processed.append(_process_document(item, resolved_engine, stderr=err_stream))
    progress.finish()

    if not processed:
        raise NoProcessableInputError("没有可输出的票据。")

    ordered = ordering.sort_documents(processed)
    layout_pages = layout.plan_pages(ordered)
    if not layout_pages:
        raise NoProcessableInputError("布局阶段未产生任何页面。")

    try:
        renderer.render_pdf(layout_pages, output)
    except renderer.RenderError as exc:
        raise FatalRunError(str(exc)) from exc

    snap = stats.aggregate(ordered)
    return RunStats(
        processed=snap.processed,
        invoices=snap.invoices,
        orders=snap.orders,
        ocr_failures=snap.ocr_failures,
        output_path=output,
    )
