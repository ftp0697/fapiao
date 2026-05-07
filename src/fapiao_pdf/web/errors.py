"""Web 层错误：状态枚举 + API 错误码 + 异常层级 + pipeline 映射。"""

from enum import StrEnum
from typing import Final

from fastapi import HTTPException

from fapiao_pdf.pipeline import (
    FatalRunError,
    NoProcessableInputError,
    OcrModelMissingError,
)


class TaskState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED_NO_INPUT = "failed-no-input"
    FAILED_OCR_MISSING = "failed-ocr-missing"
    FAILED_FATAL = "failed-fatal"
    FAILED_INTERNAL = "failed-internal"
    FAILED_RESTART = "failed-restart"
    EXPIRED = "expired"


_TERMINAL_STATES: Final[frozenset[TaskState]] = frozenset(
    {
        TaskState.DONE,
        TaskState.FAILED_NO_INPUT,
        TaskState.FAILED_OCR_MISSING,
        TaskState.FAILED_FATAL,
        TaskState.FAILED_INTERNAL,
        TaskState.FAILED_RESTART,
        TaskState.EXPIRED,
    }
)


def is_terminal(state: TaskState) -> bool:
    return state in _TERMINAL_STATES


class ApiErrorCode(StrEnum):
    NO_UPLOADED_FILES = "NoUploadedFiles"
    UPLOAD_TOO_LARGE = "UploadTooLarge"
    TOO_MANY_FILES = "TooManyFiles"
    SINGLE_FILE_TOO_LARGE = "SingleFileTooLarge"
    INVALID_PDF_DPI = "InvalidPdfDpi"
    INSUFFICIENT_STORAGE = "InsufficientStorage"
    TASK_NOT_FOUND = "TaskNotFound"
    TASK_EXPIRED = "TaskExpired"
    TASK_NOT_READY = "TaskNotReady"
    TASK_RUNNING = "TaskRunning"
    OCR_MODEL_MISSING = "OcrModelMissing"
    TOO_MANY_STREAMS = "TooManyStreams"


_STATUS_CODES: Final[dict[ApiErrorCode, int]] = {
    ApiErrorCode.NO_UPLOADED_FILES: 400,
    ApiErrorCode.UPLOAD_TOO_LARGE: 413,
    ApiErrorCode.TOO_MANY_FILES: 413,
    ApiErrorCode.SINGLE_FILE_TOO_LARGE: 413,
    ApiErrorCode.INVALID_PDF_DPI: 422,
    ApiErrorCode.INSUFFICIENT_STORAGE: 507,
    ApiErrorCode.TASK_NOT_FOUND: 404,
    ApiErrorCode.TASK_EXPIRED: 410,
    ApiErrorCode.TASK_NOT_READY: 409,
    ApiErrorCode.TASK_RUNNING: 409,
    ApiErrorCode.OCR_MODEL_MISSING: 503,
    ApiErrorCode.TOO_MANY_STREAMS: 429,
}


class WebError(Exception):
    """Web 层错误基类；子类绑定 ApiErrorCode。"""

    code: ApiErrorCode

    def __init__(self, message: str = "") -> None:
        super().__init__(message)


class NoUploadedFilesError(WebError):
    code = ApiErrorCode.NO_UPLOADED_FILES


class UploadTooLargeError(WebError):
    code = ApiErrorCode.UPLOAD_TOO_LARGE


class TooManyFilesError(WebError):
    code = ApiErrorCode.TOO_MANY_FILES


class SingleFileTooLargeError(WebError):
    code = ApiErrorCode.SINGLE_FILE_TOO_LARGE


class InvalidPdfDpiError(WebError):
    code = ApiErrorCode.INVALID_PDF_DPI


class InsufficientStorageError(WebError):
    code = ApiErrorCode.INSUFFICIENT_STORAGE


class TaskNotFoundError(WebError):
    code = ApiErrorCode.TASK_NOT_FOUND


class TaskExpiredError(WebError):
    code = ApiErrorCode.TASK_EXPIRED


class TaskNotReadyError(WebError):
    code = ApiErrorCode.TASK_NOT_READY


class TaskRunningError(WebError):
    code = ApiErrorCode.TASK_RUNNING


class OcrBrokenError(WebError):
    code = ApiErrorCode.OCR_MODEL_MISSING


class TooManyStreamsError(WebError):
    code = ApiErrorCode.TOO_MANY_STREAMS


def to_http_exception(error: WebError) -> HTTPException:
    """将 WebError 转换为 FastAPI HTTPException；仅暴露错误码，不泄漏内部消息。"""
    return HTTPException(
        status_code=_STATUS_CODES[error.code],
        detail={"error": error.code.value},
    )


def map_pipeline_exception(exc: BaseException) -> tuple[TaskState, str | None]:
    """按 pipeline 异常类型映射到终态；仅记录可控错误的消息，未知异常返回 None 防止泄漏。"""
    if isinstance(exc, NoProcessableInputError):
        return TaskState.FAILED_NO_INPUT, None
    if isinstance(exc, OcrModelMissingError):
        return TaskState.FAILED_OCR_MISSING, str(exc) or None
    if isinstance(exc, FatalRunError):
        return TaskState.FAILED_FATAL, str(exc) or None
    return TaskState.FAILED_INTERNAL, None
