"""FastAPI 应用组装与 HTTP 路由。"""

from __future__ import annotations

import asyncio
import errno
import os
import tempfile
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from fapiao_pdf import __version__, ocr, pipeline
from fapiao_pdf.ocr import OcrEngine
from fapiao_pdf.web.cleanup import CleanupManager
from fapiao_pdf.web.config import WebConfig, validate_web_config
from fapiao_pdf.web.errors import (
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
    to_http_exception,
)
from fapiao_pdf.web.progress import (
    EventBus,
    Subscriber,
    TaskEvent,
    event_name_for_snapshot,
    format_sse,
    snapshot_to_payload,
)
from fapiao_pdf.web.queue import RunMergeFn, SerialMergeExecutor
from fapiao_pdf.web.tasks import TaskRecord, TaskSnapshot, TaskStore, _now

_STATIC_DIR = Path(__file__).with_name("static")
_INDEX_PATH = _STATIC_DIR / "index.html"
_SUPPORTED_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".pdf"})
_MIN_PDF_DPI = 100
_MAX_PDF_DPI = 300
_SHUTDOWN_GRACE_SECONDS = 30.0
_CLEANUP_STOP_SECONDS = 5.0


class UploadLimitExceeded(Exception):
    pass


class UploadSizeLimitMiddleware:
    def __init__(self, app: ASGIApp, config: WebConfig) -> None:
        self.app = app
        self.config = config

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not _is_task_upload(scope):
            await self.app(scope, receive, send)
            return
        limit = _content_length_limit(self.config)
        length = _content_length_from_scope(scope)
        if length is not None and length > limit:
            await _send_error(scope, receive, send, UploadTooLargeError("content-length"))
            return
        try:
            replay_receive = await _buffer_receive_with_limit(
                receive, limit, self.config.upload_chunk_size
            )
        except UploadLimitExceeded:
            await _send_error(scope, receive, send, UploadTooLargeError("stream"))
            return
        await self.app(scope, replay_receive, send)


@dataclass(slots=True)
class WebServices:
    config: WebConfig
    store: TaskStore
    bus: EventBus
    executor: SerialMergeExecutor
    cleanup: CleanupManager
    loop: asyncio.AbstractEventLoop


def create_app(
    config: WebConfig | None = None,
    *,
    run_merge: RunMergeFn = pipeline.run_merge,
    engine_factory: Callable[[], OcrEngine] = ocr.build_default_engine,
) -> FastAPI:
    cfg = validate_web_config(config or WebConfig())

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            services = _build_services(cfg, run_merge, engine_factory)
            app.state.web_services = services
            services.cleanup.run_startup_sweep(_now())
            services.executor.start()
            services.cleanup.start()
            yield
        except pipeline.OcrModelMissingError as exc:
            print(f"OCR 模型未就绪：{exc}。请先运行 `fapiao init`。")
            raise SystemExit(2) from exc
        finally:
            services = getattr(app.state, "web_services", None)
            if services is not None:
                services.executor.stop(grace_seconds=_SHUTDOWN_GRACE_SECONDS)
                services.cleanup.stop(timeout=_CLEANUP_STOP_SECONDS)

    app = FastAPI(lifespan=lifespan)
    app.add_middleware(UploadSizeLimitMiddleware, config=cfg)

    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(_INDEX_PATH)

    @app.post("/api/tasks", status_code=202, response_model=None)
    async def create_task(
        files: Annotated[list[UploadFile] | None, File()] = None,
        pdf_dpi: Annotated[int, Form()] = 200,
    ) -> dict[str, object] | JSONResponse:
        services = _services(app)
        try:
            _validate_pdf_dpi(pdf_dpi)
            if services.executor.ocr_broken_flag():
                raise OcrBrokenError("ocr broken")
            record = await _persist_uploads(files, pdf_dpi, services)
            try:
                queue_position = services.executor.enqueue(record.task_id)
            except WebError:
                services.store.delete_task(record.task_id)
                raise
            return {"task_id": record.task_id, "queue_position": queue_position}
        except WebError as exc:
            return _error_response(exc)

    @app.get("/api/tasks/{task_id}", response_model=None)
    async def get_task(task_id: str) -> dict[str, object] | JSONResponse:
        try:
            return snapshot_to_payload(_services(app).store.require_snapshot(task_id))
        except TaskExpiredError as exc:
            return _error_response(TaskNotFoundError(str(exc)))
        except WebError as exc:
            return _error_response(exc)

    @app.get("/api/tasks/{task_id}/events", response_model=None)
    async def task_events(task_id: str) -> StreamingResponse | JSONResponse:
        services = _services(app)
        try:
            snapshot = services.store.require_snapshot(task_id)
            if is_terminal(snapshot.state):
                return StreamingResponse(
                    _single_event(snapshot),
                    media_type="text/event-stream",
                    headers={"Cache-Control": "no-cache"},
                )
            sub = services.bus.subscribe(task_id, asyncio.get_running_loop())
            return StreamingResponse(
                _live_events(task_id, snapshot, services, sub),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache"},
            )
        except TooManyStreamsError as exc:
            return _error_response(exc)
        except WebError as exc:
            return _error_response(exc)

    @app.get("/api/tasks/{task_id}/result", response_model=None)
    async def task_result(task_id: str) -> FileResponse | JSONResponse:
        services = _services(app)
        try:
            snapshot = services.store.require_snapshot(task_id)
            if snapshot.state in {TaskState.QUEUED, TaskState.RUNNING}:
                raise TaskNotReadyError(task_id)
            if snapshot.state is not TaskState.DONE or not snapshot.result_available:
                raise TaskNotFoundError(task_id)
            services.store.note_download_started(task_id)
            record = services.store.get_record(task_id)
            if record is None or not record.output_path.is_file():
                services.store.note_download_finished(task_id)
                raise TaskNotFoundError(task_id)
            filename = f"fapiao-{task_id[:8]}.pdf"
            return FileResponse(
                record.output_path,
                media_type="application/pdf",
                headers={"Content-Disposition": _content_disposition(filename)},
                background=BackgroundTask(services.store.note_download_finished, task_id),
            )
        except WebError as exc:
            return _error_response(exc)

    @app.delete("/api/tasks/{task_id}", status_code=204, response_model=None)
    async def delete_task(task_id: str) -> None | JSONResponse:
        services = _services(app)
        try:
            snapshot = services.store.require_snapshot(task_id)
            record = services.store.get_record(task_id)
            if snapshot.state is TaskState.RUNNING or (
                record is not None and record.active_downloads > 0
            ):
                raise TaskRunningError(task_id)
            services.store.delete_task(task_id)
            return None
        except TaskExpiredError as exc:
            return _error_response(TaskNotFoundError(str(exc)))
        except WebError as exc:
            return _error_response(exc)

    @app.get("/api/health")
    async def health() -> dict[str, object]:
        services = _services(app)
        return {
            "ok": True,
            "version": __version__,
            "ocr_cache_present": ocr.ocr_cache_present(),
            "engine_loaded": services.executor.ocr_ready_flag(),
            "queue_depth": services.executor.queue_depth(),
            "ocr_broken": services.executor.ocr_broken_flag(),
        }

    return app


def _build_services(
    config: WebConfig,
    run_merge: RunMergeFn,
    engine_factory: Callable[[], OcrEngine],
) -> WebServices:
    loop = asyncio.get_running_loop()
    store = TaskStore(config.task_root, placeholder_max=config.expired_placeholder_max)
    bus = EventBus(
        max_subscribers_per_task=config.max_sse_subscribers_per_task,
        subscriber_queue_maxsize=config.subscriber_queue_maxsize,
    )
    executor = SerialMergeExecutor(
        store,
        bus,
        config,
        run_merge_fn=run_merge,
        engine_factory_fn=engine_factory,
        loop=loop,
    )
    cleanup = CleanupManager(store, bus, config)
    return WebServices(config, store, bus, executor, cleanup, loop)


def _services(app: FastAPI) -> WebServices:
    return app.state.web_services


async def _persist_uploads(
    files: list[UploadFile] | None,
    pdf_dpi: int,
    services: WebServices,
) -> TaskRecord:
    if not files:
        raise NoUploadedFilesError("no files")
    if len(files) > services.config.max_files:
        raise TooManyFilesError("too many files")
    record = services.store.create_task(pdf_dpi=pdf_dpi)
    try:
        await _write_upload_files(files, record.input_dir, services.config)
    except WebError:
        services.store.delete_task(record.task_id)
        raise
    except asyncio.CancelledError:
        services.store.delete_task(record.task_id)
        raise
    except OSError as exc:
        services.store.delete_task(record.task_id)
        if exc.errno == errno.ENOSPC:
            raise InsufficientStorageError("no space") from exc
        raise
    except Exception:
        services.store.delete_task(record.task_id)
        raise
    return record


async def _write_upload_files(
    files: list[UploadFile], input_dir: Path, config: WebConfig
) -> None:
    total = 0
    used_names: set[str] = set()
    max_total = config.max_upload_mb * 1024 * 1024
    max_single = config.max_single_file_mb * 1024 * 1024
    for index, upload in enumerate(files, start=1):
        if Path(upload.filename or "").suffix.lower() not in _SUPPORTED_SUFFIXES:
            continue
        filename = _safe_filename(upload.filename or f"upload-{index}", used_names)
        used_names.add(filename)
        final_path = input_dir / filename
        part_path = input_dir / f".upload-{index}.part"
        single = 0
        with part_path.open("wb") as handle:
            while chunk := await upload.read(config.upload_chunk_size):
                single += len(chunk)
                total += len(chunk)
                if single > max_single:
                    raise SingleFileTooLargeError(filename)
                if total > max_total:
                    raise UploadTooLargeError(filename)
                handle.write(chunk)
        os.replace(part_path, final_path)
    if total == 0:
        raise NoUploadedFilesError("empty upload")


def _safe_filename(filename: str, used_names: set[str]) -> str:
    name = Path(filename).name.replace("/", "").replace("\\", "").strip()
    if not name or name in {".", ".."}:
        name = "upload"
    candidate = name
    stem = Path(name).stem or "upload"
    suffix = Path(name).suffix
    counter = 1
    while candidate in used_names:
        candidate = f"{stem} ({counter}){suffix}"
        counter += 1
    return candidate


def _validate_pdf_dpi(pdf_dpi: int) -> None:
    if not _MIN_PDF_DPI <= pdf_dpi <= _MAX_PDF_DPI:
        raise InvalidPdfDpiError(str(pdf_dpi))


def _content_length_limit(config: WebConfig) -> int:
    return config.max_upload_mb * 1024 * 1024


def _is_task_upload(scope: Scope) -> bool:
    return (
        scope["type"] == "http"
        and scope.get("method") == "POST"
        and scope.get("path") == "/api/tasks"
    )


def _content_length_from_scope(scope: Scope) -> int | None:
    for name, value in scope.get("headers", []):
        if name.lower() != b"content-length":
            continue
        try:
            return int(value)
        except ValueError:
            return None
    return None


async def _buffer_receive_with_limit(
    receive: Receive, limit: int, spool_limit: int
) -> Receive:
    received = 0
    body = tempfile.SpooledTemporaryFile(max_size=spool_limit)
    disconnected = False
    while True:
        message = await receive()
        if message.get("type") != "http.request":
            disconnected = True
            break
        chunk = message.get("body", b"")
        received += len(chunk)
        if received > limit:
            body.close()
            raise UploadLimitExceeded
        body.write(chunk)
        if not message.get("more_body", False):
            break
    body.seek(0)
    if disconnected:
        body.close()

        async def replay_disconnect() -> Message:
            return {"type": "http.disconnect"}

        return replay_disconnect

    async def replay_receive() -> Message:
        chunk = body.read(spool_limit)
        if chunk:
            more_body = body.tell() < received
            if not more_body:
                body.close()
            return {
                "type": "http.request",
                "body": chunk,
                "more_body": more_body,
            }
        body.close()
        return {"type": "http.request", "body": b"", "more_body": False}

    return replay_receive


async def _send_error(
    scope: Scope, receive: Receive, send: Send, error: WebError
) -> None:
    await _error_response(error)(scope, receive, send)


def _content_disposition(filename: str) -> str:
    encoded = quote(filename, safe="")
    return f"attachment; filename=\"{filename}\"; filename*=UTF-8''{encoded}"


def _error_response(error: WebError) -> JSONResponse:
    http = to_http_exception(error)
    return JSONResponse(status_code=http.status_code, content=http.detail)


async def _single_event(snapshot: TaskSnapshot) -> AsyncIterator[str]:
    yield format_sse(
        TaskEvent(
            name=event_name_for_snapshot(snapshot),
            payload=snapshot_to_payload(snapshot),
            terminal=True,
        )
    )


async def _live_events(
    task_id: str,
    seed_snapshot: TaskSnapshot,
    services: WebServices,
    sub: Subscriber,
) -> AsyncIterator[str]:
    try:
        seed = TaskEvent(
            name=event_name_for_snapshot(seed_snapshot),
            payload=snapshot_to_payload(seed_snapshot),
            terminal=is_terminal(seed_snapshot.state),
        )
        yield format_sse(seed)
        while True:
            try:
                event = await asyncio.wait_for(
                    sub.queue.get(), timeout=services.config.heartbeat_seconds
                )
            except asyncio.TimeoutError:
                yield format_sse(
                    TaskEvent(
                        name="heartbeat",
                        payload={"task_id": task_id, "server_time": _now().isoformat()},
                    )
                )
                continue
            yield format_sse(event)
            if event.terminal:
                return
    finally:
        services.bus.unsubscribe(task_id, sub.subscriber_id)

