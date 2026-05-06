"""数据模型。"""

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Literal

from PIL import Image

DocType = Literal["invoice", "order"]
InputKind = Literal["image", "pdf_page", "pdf_page_crop"]


@dataclass(slots=True)
class LogicalInput:
    path: Path
    display_key: str
    doc_type: InputKind


@dataclass(slots=True)
class SplitCrop:
    image: Image.Image
    bbox: tuple[int, int, int, int]
    display_key: str


@dataclass(slots=True)
class SplitDocument:
    original_path: Path
    crops: list[SplitCrop]
    success: bool
    warning: str | None


@dataclass(slots=True)
class OcrResult:
    text: str
    orientation_corrected: bool
    success: bool
    error: str | None


@dataclass(slots=True)
class ProcessedDocument:
    original: LogicalInput
    image: Image.Image
    doc_type: DocType
    date: date | None
    ocr_failure: bool
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class LayoutSlot:
    document: ProcessedDocument
    slot_index: int
    page_type: DocType


@dataclass(slots=True)
class LayoutPage:
    page_num: int
    slots: list[LayoutSlot]


@dataclass(slots=True)
class WarningEntry:
    message: str
    file: str | None
    stage: str
