"""图像读写。"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError


class ImageLoadError(RuntimeError):
    """图像加载失败。"""


def load_image(path: Path) -> Image.Image:
    """加载图片，立即载入像素并应用 EXIF 方向，最后关闭文件句柄。"""

    try:
        with Image.open(path) as src:
            src.load()
            normalized = ImageOps.exif_transpose(src)
        return normalized.convert("RGB")
    except (FileNotFoundError, UnidentifiedImageError, OSError) as exc:
        raise ImageLoadError(f"图片不可读取或已损坏：{path}") from exc


def encode_jpeg_bytes(image: Image.Image, *, quality: int = 90) -> bytes:
    """编码为 JPEG 字节流，便于 ReportLab 嵌入。"""

    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=quality, optimize=True)
    return buffer.getvalue()
