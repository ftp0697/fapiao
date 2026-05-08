"""OCR 适配。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from PIL import Image

from fapiao_pdf.models import OcrResult

# Workaround for PaddlePaddle 3.3+ PIR + oneDNN incompatibility
# (ConvertPirAttribute2RuntimeAttribute unsupported on CPU MKLDNN path).
# Must be set before any `import paddle`. Users may override via env.
os.environ.setdefault("FLAGS_use_onednn", "0")
os.environ.setdefault("FLAGS_enable_pir_api", "0")

_PADDLE_CACHE_ENV: str = "PADDLE_OCR_CACHE_DIR"
_DEFAULT_CACHE_DIRS: tuple[Path, ...] = (
    Path.home() / ".paddlex" / "official_models",  # PaddleOCR 3.x
    Path.home() / ".paddleocr",  # 旧版与自定义 dir 兼容
)


class OcrModelMissingError(RuntimeError):
    """OCR 本地模型不可用。"""


@runtime_checkable
class OcrEngine(Protocol):
    """OCR 引擎统一接口，便于测试注入。"""

    def recognize(self, image: Image.Image) -> OcrResult:
        ...


@dataclass(slots=True, frozen=True)
class _PaddleConfig:
    lang: str = "ch"
    use_angle_cls: bool = True
    # CPU 场景默认使用 mobile 模型（约 server 的 1/8 大小、>5× 推理速度，
    # 精度对发票/订单关键词匹配场景充足）。可通过环境变量切换。
    text_detection_model_name: str = "PP-OCRv5_mobile_det"
    text_recognition_model_name: str = "PP-OCRv5_mobile_rec"


def _paddle_cache_dirs() -> tuple[Path, ...]:
    import os

    override: str | None = os.environ.get(_PADDLE_CACHE_ENV)
    if override:
        return (Path(override).expanduser(),)
    return _DEFAULT_CACHE_DIRS


def _is_cache_populated(cache_dir: Path) -> bool:
    if not cache_dir.exists():
        return False
    return any(cache_dir.rglob("*.pdmodel")) or any(cache_dir.rglob("*.pdiparams"))


def ocr_cache_present() -> bool:
    return any(_is_cache_populated(path) for path in _paddle_cache_dirs())


def ensure_ocr_ready(*, allow_download: bool) -> None:
    """校验 PaddleOCR 模型可用；merge 模式禁止联网下载。"""

    candidates = _paddle_cache_dirs()
    if any(_is_cache_populated(d) for d in candidates):
        return
    if not allow_download:
        raise OcrModelMissingError(
            f"未在 {', '.join(str(d) for d in candidates)} 找到 PaddleOCR 本地模型，"
            "请先运行 `fapiao init`。"
        )
    _bootstrap_paddleocr_cache()


def _build_paddleocr_kwargs(cfg: _PaddleConfig) -> dict[str, object]:
    """PaddleOCR 3.x 兼容初始化：

    - 禁用 oneDNN 以规避 PaddlePaddle 3.3+ PIR 不兼容
    - 默认 mobile 模型，对纯文字票据足够且速度快 ~7×
    - 关闭文档矫正/方向分类等冗余子模型，扫描件直入识别
    - 通过环境变量 FAPIAO_OCR_MODEL=server 可切回高精度大模型
    """

    import os

    use_server = os.environ.get("FAPIAO_OCR_MODEL", "").lower() == "server"
    det_model = (
        "PP-OCRv5_server_det" if use_server else cfg.text_detection_model_name
    )
    rec_model = (
        "PP-OCRv5_server_rec" if use_server else cfg.text_recognition_model_name
    )

    return {
        "lang": cfg.lang,
        "text_detection_model_name": det_model,
        "text_recognition_model_name": rec_model,
        "use_doc_orientation_classify": False,
        "use_doc_unwarping": False,
        "use_textline_orientation": False,
        "engine": "paddle_static",
        "engine_config": {
            "device_type": "cpu",
            "run_mode": "paddle",
        },
    }


def _bootstrap_paddleocr_cache() -> None:
    """实例化 PaddleOCR 触发首次模型下载。"""

    try:
        from paddleocr import PaddleOCR  # type: ignore[import-not-found]
    except ImportError as exc:
        raise OcrModelMissingError(
            "未安装 paddleocr。请先安装依赖再执行 `fapiao init`。"
        ) from exc

    PaddleOCR(**_build_paddleocr_kwargs(_PaddleConfig()))


def _extract_text(prediction: object) -> str:
    """从 PaddleOCR 3.x 预测结果中提取纯文本流。"""

    if prediction is None:
        return ""
    if isinstance(prediction, list):
        parts = (_extract_text(item) for item in prediction)
        return "\n".join(p for p in parts if p)

    # PaddleOCR 3.x: prediction.json == {'res': {'rec_texts': [...], ...}}
    json_attr = getattr(prediction, "json", None)
    if isinstance(json_attr, dict):
        res = json_attr.get("res")
        if isinstance(res, dict):
            texts = res.get("rec_texts")
            if isinstance(texts, list):
                return "\n".join(str(t) for t in texts if t)
        # 旧版直接放在顶层
        for key in ("rec_texts", "texts"):
            texts = json_attr.get(key)
            if isinstance(texts, list):
                return "\n".join(str(t) for t in texts if t)

    # 旧版兼容：直接挂在对象属性
    rec_texts = getattr(prediction, "rec_texts", None)
    if isinstance(rec_texts, list):
        return "\n".join(str(t) for t in rec_texts if t)
    return ""


class PaddleOcrEngine:
    """PaddleOCR 适配器；merge 期间不再联网。"""

    __slots__ = ("_engine",)

    def __init__(self) -> None:
        try:
            from paddleocr import PaddleOCR  # type: ignore[import-not-found]
        except ImportError as exc:
            raise OcrModelMissingError(
                "未安装 paddleocr。请先 `pip install paddleocr`。"
            ) from exc
        self._engine = PaddleOCR(**_build_paddleocr_kwargs(_PaddleConfig()))

    def recognize(self, image: Image.Image) -> OcrResult:
        import numpy as np

        try:
            arr = np.array(image.convert("RGB"))
            prediction = self._engine.predict(input=arr)
        except Exception as exc:  # noqa: BLE001
            return OcrResult(text="", orientation_corrected=False, success=False, error=str(exc))

        text: str = _extract_text(prediction)
        if not text:
            return OcrResult(
                text="",
                orientation_corrected=False,
                success=False,
                error="OCR 未识别到任何文本",
            )
        return OcrResult(text=text, orientation_corrected=False, success=True, error=None)


class FakeOcrEngine:
    """用于测试与样例运行的可注入 OCR 实现。"""

    __slots__ = ("_responder",)

    def __init__(self, responder) -> None:  # type: ignore[no-untyped-def]
        self._responder = responder

    def recognize(self, image: Image.Image) -> OcrResult:
        try:
            text = self._responder(image)
        except Exception as exc:  # noqa: BLE001
            return OcrResult(
                text="", orientation_corrected=False, success=False, error=str(exc)
            )
        if not text:
            return OcrResult(
                text="",
                orientation_corrected=False,
                success=False,
                error="OCR 未识别到任何文本",
            )
        return OcrResult(text=text, orientation_corrected=False, success=True, error=None)


def build_default_engine() -> OcrEngine:
    """生产环境默认 OCR 引擎；模型缺失会抛 OcrModelMissingError。"""

    ensure_ocr_ready(allow_download=False)
    return PaddleOcrEngine()
