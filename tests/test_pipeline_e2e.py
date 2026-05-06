"""端到端集成测试：使用 FakeOcrEngine 模拟 OCR，覆盖 invoice/order/无日期/同日期/PDF 输入路径。"""

from __future__ import annotations

from pathlib import Path

import pytest
import pymupdf
from PIL import Image

from fapiao_pdf import pipeline
from fapiao_pdf.ocr import FakeOcrEngine


def _make_image(path: Path, size: tuple[int, int] = (600, 400)) -> Path:
    img = Image.new("RGB", size, "white")
    img.save(path)
    return path


def _make_pdf(path: Path, page_count: int = 2) -> Path:
    doc = pymupdf.open()
    for _ in range(page_count):
        doc.new_page(width=600, height=800)
    doc.save(path)
    doc.close()
    return path


def _fake_engine_with_corpus(corpus: dict[str, str]) -> FakeOcrEngine:
    """根据图像尺寸映射到文本，简单可控。"""

    sizes_seen: list[tuple[int, int]] = []

    def _responder(image: Image.Image) -> str:
        key = f"{image.width}x{image.height}"
        sizes_seen.append((image.width, image.height))
        return corpus.get(key, "")

    return FakeOcrEngine(_responder)


def test_end_to_end_invoices_first_then_orders(tmp_path: Path) -> None:
    inputs = tmp_path / "input"
    inputs.mkdir()
    _make_image(inputs / "a_invoice.png", size=(600, 400))
    _make_image(inputs / "b_order.png", size=(601, 401))

    corpus = {
        "600x400": "发票号码 12345\n日期 2024-05-01",
        "601x401": "订单编号 99\n日期 2024-01-01",
    }
    engine = _fake_engine_with_corpus(corpus)

    out = tmp_path / "out.pdf"
    stats = pipeline.run_merge(
        inputs,
        out,
        force=False,
        pdf_dpi=150,
        workers=1,
        engine=engine,
    )

    assert out.exists()
    assert stats.processed == 2
    assert stats.invoices == 1
    assert stats.orders == 1
    assert stats.ocr_failures == 0
    assert stats.output_path == out

    doc = pymupdf.open(out)
    try:
        # 1 invoice + 1 order = 2 pages
        assert len(doc) == 2
        # A4 portrait pt: ~595 × 842
        rect = doc[0].rect
        assert 590 < rect.width < 600
        assert 838 < rect.height < 846
    finally:
        doc.close()


def test_end_to_end_undated_falls_to_group_tail(tmp_path: Path) -> None:
    inputs = tmp_path / "input"
    inputs.mkdir()
    _make_image(inputs / "dated.png", size=(600, 400))
    _make_image(inputs / "undated.png", size=(601, 401))

    corpus = {
        "600x400": "发票号码 1\n2024-05-01",
        "601x401": "发票号码 2",  # 无日期
    }
    engine = _fake_engine_with_corpus(corpus)

    out = tmp_path / "out.pdf"
    stats = pipeline.run_merge(
        inputs, out, force=False, pdf_dpi=150, workers=1, engine=engine
    )
    assert stats.invoices == 2
    assert stats.orders == 0


def test_end_to_end_ocr_failure_degrades_to_order(tmp_path: Path) -> None:
    inputs = tmp_path / "input"
    inputs.mkdir()
    _make_image(inputs / "broken.png", size=(600, 400))

    engine = FakeOcrEngine(lambda _img: "")  # OCR 空文本

    out = tmp_path / "out.pdf"
    stats = pipeline.run_merge(
        inputs, out, force=False, pdf_dpi=150, workers=1, engine=engine
    )
    assert stats.processed == 1
    assert stats.orders == 1
    assert stats.ocr_failures == 1
    assert out.exists()


def test_end_to_end_pdf_input_expands_to_pages(tmp_path: Path) -> None:
    inputs = tmp_path / "input"
    inputs.mkdir()
    _make_pdf(inputs / "two_pages.pdf", page_count=2)

    engine = FakeOcrEngine(lambda _img: "发票号码 1")
    out = tmp_path / "out.pdf"
    stats = pipeline.run_merge(
        inputs, out, force=False, pdf_dpi=150, workers=1, engine=engine
    )
    assert stats.processed == 2  # 两页扩展为两个文档
    assert stats.invoices == 2


def test_end_to_end_no_processable_input_raises(tmp_path: Path) -> None:
    inputs = tmp_path / "empty"
    inputs.mkdir()
    out = tmp_path / "out.pdf"
    engine = FakeOcrEngine(lambda _img: "发票")

    with pytest.raises(pipeline.NoProcessableInputError):
        pipeline.run_merge(
            inputs, out, force=False, pdf_dpi=150, workers=1, engine=engine
        )


def test_end_to_end_render_failure_does_not_replace_existing_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs = tmp_path / "input"
    inputs.mkdir()
    _make_image(inputs / "a.png", size=(600, 400))

    out = tmp_path / "out.pdf"
    out.write_bytes(b"ORIGINAL")

    from fapiao_pdf import renderer

    def _boom(*_a: object, **_k: object) -> None:
        raise renderer.RenderError("render failed")

    monkeypatch.setattr(renderer, "render_pdf", _boom)

    engine = FakeOcrEngine(lambda _img: "发票号码 1\n2024-05-01")
    with pytest.raises(pipeline.FatalRunError):
        pipeline.run_merge(
            inputs, out, force=True, pdf_dpi=150, workers=1, engine=engine
        )
    assert out.read_bytes() == b"ORIGINAL"
