import pytest
from fastapi import HTTPException

from fapiao_pdf.pipeline import (
    FatalRunError,
    NoProcessableInputError,
    OcrModelMissingError,
)
from fapiao_pdf.web.errors import (
    ApiErrorCode,
    InsufficientStorageError,
    InvalidPdfDpiError,
    NoUploadedFilesError,
    OcrBrokenError,
    SingleFileTooLargeError,
    TaskExpiredError,
    TaskNotFoundError,
    TaskNotReadyError,
    TaskRunningError,
    TaskState,
    TooManyFilesError,
    TooManyStreamsError,
    UploadTooLargeError,
    WebError,
    is_terminal,
    map_pipeline_exception,
    to_http_exception,
)


def test_task_state_values() -> None:
    assert TaskState.QUEUED == "queued"
    assert TaskState.RUNNING == "running"
    assert TaskState.DONE == "done"
    assert TaskState.FAILED_NO_INPUT == "failed-no-input"
    assert TaskState.FAILED_OCR_MISSING == "failed-ocr-missing"
    assert TaskState.FAILED_FATAL == "failed-fatal"
    assert TaskState.FAILED_INTERNAL == "failed-internal"
    assert TaskState.FAILED_RESTART == "failed-restart"
    assert TaskState.EXPIRED == "expired"


@pytest.mark.parametrize(
    "state,expected",
    [
        (TaskState.QUEUED, False),
        (TaskState.RUNNING, False),
        (TaskState.DONE, True),
        (TaskState.FAILED_NO_INPUT, True),
        (TaskState.FAILED_OCR_MISSING, True),
        (TaskState.FAILED_FATAL, True),
        (TaskState.FAILED_INTERNAL, True),
        (TaskState.FAILED_RESTART, True),
        (TaskState.EXPIRED, True),
    ],
)
def test_is_terminal(state: TaskState, expected: bool) -> None:
    assert is_terminal(state) is expected


def test_api_error_code_values() -> None:
    assert ApiErrorCode.NO_UPLOADED_FILES == "NoUploadedFiles"
    assert ApiErrorCode.UPLOAD_TOO_LARGE == "UploadTooLarge"
    assert ApiErrorCode.TOO_MANY_FILES == "TooManyFiles"
    assert ApiErrorCode.SINGLE_FILE_TOO_LARGE == "SingleFileTooLarge"
    assert ApiErrorCode.INVALID_PDF_DPI == "InvalidPdfDpi"
    assert ApiErrorCode.INSUFFICIENT_STORAGE == "InsufficientStorage"
    assert ApiErrorCode.TASK_NOT_FOUND == "TaskNotFound"
    assert ApiErrorCode.TASK_EXPIRED == "TaskExpired"
    assert ApiErrorCode.TASK_NOT_READY == "TaskNotReady"
    assert ApiErrorCode.TASK_RUNNING == "TaskRunning"
    assert ApiErrorCode.OCR_MODEL_MISSING == "OcrModelMissing"
    assert ApiErrorCode.TOO_MANY_STREAMS == "TooManyStreams"


@pytest.mark.parametrize(
    "exc_cls,expected_code,expected_status",
    [
        (NoUploadedFilesError, ApiErrorCode.NO_UPLOADED_FILES, 400),
        (UploadTooLargeError, ApiErrorCode.UPLOAD_TOO_LARGE, 413),
        (TooManyFilesError, ApiErrorCode.TOO_MANY_FILES, 413),
        (SingleFileTooLargeError, ApiErrorCode.SINGLE_FILE_TOO_LARGE, 413),
        (InvalidPdfDpiError, ApiErrorCode.INVALID_PDF_DPI, 422),
        (InsufficientStorageError, ApiErrorCode.INSUFFICIENT_STORAGE, 507),
        (TaskNotFoundError, ApiErrorCode.TASK_NOT_FOUND, 404),
        (TaskExpiredError, ApiErrorCode.TASK_EXPIRED, 410),
        (TaskNotReadyError, ApiErrorCode.TASK_NOT_READY, 409),
        (TaskRunningError, ApiErrorCode.TASK_RUNNING, 409),
        (OcrBrokenError, ApiErrorCode.OCR_MODEL_MISSING, 503),
        (TooManyStreamsError, ApiErrorCode.TOO_MANY_STREAMS, 429),
    ],
)
def test_to_http_exception(
    exc_cls: type[WebError], expected_code: ApiErrorCode, expected_status: int
) -> None:
    err = exc_cls("内部诊断信息")
    http_exc = to_http_exception(err)
    assert isinstance(http_exc, HTTPException)
    assert http_exc.status_code == expected_status
    assert http_exc.detail == {"error": expected_code.value}


def test_to_http_exception_does_not_leak_message() -> None:
    err = TaskNotFoundError("内部任务路径 /tmp/foo")
    http_exc = to_http_exception(err)
    assert "message" not in http_exc.detail
    assert "/tmp/foo" not in str(http_exc.detail)


def test_map_pipeline_no_processable_input() -> None:
    state, msg = map_pipeline_exception(NoProcessableInputError("不应被使用"))
    assert state is TaskState.FAILED_NO_INPUT
    assert msg is None


def test_map_pipeline_ocr_missing() -> None:
    state, msg = map_pipeline_exception(OcrModelMissingError("缓存缺失"))
    assert state is TaskState.FAILED_OCR_MISSING
    assert msg == "缓存缺失"


def test_map_pipeline_ocr_missing_empty_message() -> None:
    state, msg = map_pipeline_exception(OcrModelMissingError())
    assert state is TaskState.FAILED_OCR_MISSING
    assert msg is None


def test_map_pipeline_fatal() -> None:
    state, msg = map_pipeline_exception(FatalRunError("渲染失败"))
    assert state is TaskState.FAILED_FATAL
    assert msg == "渲染失败"


def test_map_pipeline_internal_fallback() -> None:
    err = RuntimeError("敏感信息：/etc/passwd")
    state, msg = map_pipeline_exception(err)
    assert state is TaskState.FAILED_INTERNAL
    assert msg is None


def test_web_error_str() -> None:
    err = TaskNotFoundError("任务不存在")
    assert str(err) == "任务不存在"
    assert isinstance(err, Exception)


def test_web_error_subclasses_carry_codes() -> None:
    assert NoUploadedFilesError.code is ApiErrorCode.NO_UPLOADED_FILES
    assert OcrBrokenError.code is ApiErrorCode.OCR_MODEL_MISSING
