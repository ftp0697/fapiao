from pathlib import Path

import pytest
from PIL import Image

from fapiao_pdf.ocr import (
    FakeOcrEngine,
    OcrModelMissingError,
    ensure_ocr_ready,
)


def _blank_image() -> Image.Image:
    return Image.new("RGB", (10, 10), "white")


def test_ensure_ocr_ready_raises_when_cache_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PADDLE_OCR_CACHE_DIR", str(tmp_path / "missing"))
    with pytest.raises(OcrModelMissingError):
        ensure_ocr_ready(allow_download=False)


def test_ensure_ocr_ready_passes_when_cache_populated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cache = tmp_path / "cache"
    (cache / "det").mkdir(parents=True)
    (cache / "det" / "model.pdmodel").write_bytes(b"\x00")
    (cache / "det" / "model.pdiparams").write_bytes(b"\x00")
    monkeypatch.setenv("PADDLE_OCR_CACHE_DIR", str(cache))
    ensure_ocr_ready(allow_download=False)  # 不应抛错


def test_fake_engine_success_path() -> None:
    eng = FakeOcrEngine(lambda _img: "发票号码 123\n2024-05-01")
    res = eng.recognize(_blank_image())
    assert res.success is True
    assert res.error is None
    assert "发票" in res.text


def test_fake_engine_empty_text_counts_as_failure() -> None:
    eng = FakeOcrEngine(lambda _img: "")
    res = eng.recognize(_blank_image())
    assert res.success is False
    assert res.error is not None


def test_fake_engine_exception_counts_as_failure() -> None:
    def _boom(_img: Image.Image) -> str:
        raise RuntimeError("boom")

    eng = FakeOcrEngine(_boom)
    res = eng.recognize(_blank_image())
    assert res.success is False
    assert res.error == "boom"


def test_fake_engine_orientation_field_default_false() -> None:
    eng = FakeOcrEngine(lambda _img: "任何文本")
    res = eng.recognize(_blank_image())
    assert res.orientation_corrected is False
