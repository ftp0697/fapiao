"""票据切分。"""

from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from fapiao_pdf.models import SplitCrop

_MIN_REGION_COUNT: int = 2
_MIN_AREA_RATIO: float = 0.08
_MAX_AREA_RATIO: float = 0.95
_MIN_DIMENSION_RATIO: float = 0.15
_MIN_ASPECT_RATIO: float = 0.25
_MAX_ASPECT_RATIO: float = 4.0
_MAX_IOU: float = 0.2
_PADDING_RATIO: float = 0.02
_ROW_GROUP_TOLERANCE_RATIO: float = 0.10


def _iou(
    box1: tuple[int, int, int, int],
    box2: tuple[int, int, int, int],
) -> float:
    """交并比。"""

    x1_min, y1_min, x1_max, y1_max = box1
    x2_min, y2_min, x2_max, y2_max = box2

    inter_left = max(x1_min, x2_min)
    inter_top = max(y1_min, y2_min)
    inter_right = min(x1_max, x2_max)
    inter_bottom = min(y1_max, y2_max)

    inter_w = max(0, inter_right - inter_left)
    inter_h = max(0, inter_bottom - inter_top)
    inter = inter_w * inter_h
    if inter == 0:
        return 0.0

    area1 = max(0, x1_max - x1_min) * max(0, y1_max - y1_min)
    area2 = max(0, x2_max - x2_min) * max(0, y2_max - y2_min)
    union = area1 + area2 - inter
    return inter / union if union > 0 else 0.0


def _expand_bbox(
    bbox: tuple[int, int, int, int],
    width: int,
    height: int,
) -> tuple[int, int, int, int]:
    """加 2% 内边距。"""

    left, top, right, bottom = bbox
    pad_x = max(1, int(round(width * _PADDING_RATIO)))
    pad_y = max(1, int(round(height * _PADDING_RATIO)))
    return (
        max(0, left - pad_x),
        max(0, top - pad_y),
        min(width, right + pad_x),
        min(height, bottom + pad_y),
    )


def _sort_boxes(
    boxes: list[tuple[int, int, int, int]],
    image_height: int,
) -> list[tuple[int, int, int, int]]:
    """上→下，同行左→右。"""

    tolerance = image_height * _ROW_GROUP_TOLERANCE_RATIO
    annotated = [(box, (box[1] + box[3]) / 2.0, (box[0] + box[2]) / 2.0) for box in boxes]
    annotated.sort(key=lambda item: (item[1], item[2]))

    sorted_boxes: list[tuple[int, int, int, int]] = []
    current: list[tuple[tuple[int, int, int, int], float, float]] = []
    anchor: float | None = None

    for box, yc, xc in annotated:
        if anchor is None or abs(yc - anchor) <= tolerance:
            current.append((box, yc, xc))
            if anchor is None:
                anchor = yc
            continue
        current.sort(key=lambda entry: entry[2])
        sorted_boxes.extend(entry[0] for entry in current)
        current = [(box, yc, xc)]
        anchor = yc

    if current:
        current.sort(key=lambda entry: entry[2])
        sorted_boxes.extend(entry[0] for entry in current)
    return sorted_boxes


def split_page(image: Image.Image, path: Path) -> tuple[list[SplitCrop] | None, str | None]:
    """切分页面为多张票据。"""

    try:
        rgb = image.convert("RGB")
        arr = np.array(rgb)
        h, w = arr.shape[:2]
        page_area = w * h

        gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        thresh = cv2.adaptiveThreshold(
            blurred, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 31, 15
        )

        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        candidates: list[tuple[int, int, int, int]] = []
        for cnt in contours:
            x, y, cw, ch = cv2.boundingRect(cnt)
            area_ratio = (cw * ch) / page_area
            aspect = cw / ch if ch > 0 else 0.0
            if not (_MIN_AREA_RATIO <= area_ratio <= _MAX_AREA_RATIO):
                continue
            if cw <= w * _MIN_DIMENSION_RATIO or ch <= h * _MIN_DIMENSION_RATIO:
                continue
            if not (_MIN_ASPECT_RATIO <= aspect <= _MAX_ASPECT_RATIO):
                continue
            candidates.append((x, y, x + cw, y + ch))

        if len(candidates) < _MIN_REGION_COUNT:
            return None, "未检测到多票据区域，回退整页"

        for i, b1 in enumerate(candidates):
            for b2 in candidates[i + 1:]:
                if _iou(b1, b2) > _MAX_IOU:
                    return None, "检测到重叠票据区域，回退整页"

        sorted_boxes = _sort_boxes(candidates, h)
        crops: list[SplitCrop] = []
        for idx, box in enumerate(sorted_boxes, start=1):
            padded = _expand_bbox(box, w, h)
            cropped = rgb.crop(padded)
            crops.append(
                SplitCrop(
                    image=cropped,
                    bbox=padded,
                    display_key=f"{path}#crop={idx:04d}",
                )
            )
        return crops, None
    except Exception:
        return None, "票据切分失败，回退整页"
