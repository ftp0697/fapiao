"""串行 merge 执行器：单 worker 线程 + OCR 单例 + 进度事件桥接。"""

from __future__ import annotations

import asyncio
import logging
import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TextIO

from fapiao_pdf.ocr import OcrEngine, OcrModelMissingError, build_default_engine
from fapiao_pdf.pipeline import RunStats, run_merge
from fapiao_pdf.web.config import WebConfig
from fapiao_pdf.web.errors import (
    OcrBrokenError,
    TaskExpiredError,
    TaskNotFoundError,
    TaskState,
    map_pipeline_exception,
)
from fapiao_pdf.web.progress import PipelineTextCapture, parse_progress_line
from fapiao_pdf.web.tasks import SummarySnapshot, TaskSnapshot, TaskStore

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class WorkItem:
    task_id: str


class RunMergeFn(Protocol):
    def __call__(
        self,
        input_dir: Path,
        output: Path,
        *,
        force: bool,
        pdf_dpi: int,
        workers: int,
        engine: OcrEngine | None = None,
        stdout: TextIO | None = None,
        stderr: TextIO | None = None,
    ) -> RunStats:
        ...


class BusPublisher(Protocol):
    def publish_snapshot(
        self,
        task_id: str,
        snapshot: TaskSnapshot,
        *,
        event_name: str,
        terminal: bool = False,
        extra: dict[str, object] | None = None,
    ) -> None:
        ...


class SerialMergeExecutor:
    """单线程 FIFO 执行器；所有 OCR 与 pipeline 调用都在 worker 内串行发生。"""

    def __init__(
        self,
        store: TaskStore,
        bus: BusPublisher,
        config: WebConfig,
        run_merge_fn: RunMergeFn = run_merge,
        engine_factory_fn: Callable[[], OcrEngine] = build_default_engine,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        self._store = store
        self._bus = bus
        self._config = config
        self._run_merge = run_merge_fn
        self._engine_factory = engine_factory_fn
        self._loop = loop
        self._queue: queue.Queue[WorkItem] = queue.Queue()
        self._stop_event = threading.Event()
        self._ocr_ready_flag = threading.Event()
        self._ocr_broken_flag = threading.Event()
        self._engine_lock = threading.RLock()
        self._state_lock = threading.RLock()
        self._engine: OcrEngine | None = None
        self._thread: threading.Thread | None = None
        self._current_task_id: str | None = None

    def start(self) -> None:
        with self._state_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run, name="fapiao-web-merge-worker", daemon=True
            )
            self._thread.start()

    def enqueue(self, task_id: str) -> int:
        if self._ocr_broken_flag.is_set():
            raise OcrBrokenError(task_id)
        self._store.set_queued(task_id)
        snapshot = self._store.require_snapshot(task_id)
        self._bus.publish_snapshot(task_id, snapshot, event_name="queued")
        self._queue.put(WorkItem(task_id=task_id))
        return self._store.queue_position(task_id) or 0

    def stop(self, grace_seconds: float) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is None:
            return
        thread.join(timeout=grace_seconds)
        if thread.is_alive():
            logger.warning(
                "merge worker did not stop within %.1f seconds", grace_seconds
            )

    def current_task_id(self) -> str | None:
        with self._state_lock:
            return self._current_task_id

    def queue_depth(self) -> int:
        return self._queue.qsize()

    def ocr_ready_flag(self) -> bool:
        return self._ocr_ready_flag.is_set()

    def ocr_broken_flag(self) -> bool:
        return self._ocr_broken_flag.is_set()

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                item = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue
            try:
                if self._stop_event.is_set():
                    self._queue.put(item)
                    return
                record = self._store.get_record(item.task_id)
                if record is None or record.state is not TaskState.QUEUED:
                    continue
                with self._state_lock:
                    self._current_task_id = item.task_id
                try:
                    self._process_one(item.task_id)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("merge worker failed for task %s", item.task_id)
                    self._set_failed(
                        item.task_id,
                        TaskState.FAILED_INTERNAL,
                        error=str(exc) or None,
                    )
            finally:
                with self._state_lock:
                    if self._current_task_id == item.task_id:
                        self._current_task_id = None
                self._queue.task_done()

    def _process_one(self, task_id: str) -> None:
        if self._ocr_broken_flag.is_set():
            self._set_failed(task_id, TaskState.FAILED_OCR_MISSING, error=None)
            return
        try:
            engine = self._get_engine()
        except OcrModelMissingError as exc:
            self._ocr_broken_flag.set()
            self._set_failed(
                task_id,
                TaskState.FAILED_OCR_MISSING,
                error=str(exc) or None,
            )
            return
        except Exception as exc:  # noqa: BLE001
            state, error = map_pipeline_exception(exc)
            self._set_failed(task_id, state, error=error)
            return

        try:
            self._store.set_running(task_id)
        except (TaskExpiredError, TaskNotFoundError, ValueError):
            return
        self._publish_snapshot(task_id, "progress")

        stdout = PipelineTextCapture(lambda line: self._handle_stdout(task_id, line))
        stderr = PipelineTextCapture(lambda line: self._handle_stderr(task_id, line))
        try:
            record = self._store.get_record(task_id)
            if record is None:
                return
            stats = self._run_merge(
                record.input_dir,
                record.output_path,
                force=True,
                pdf_dpi=record.pdf_dpi,
                workers=1,
                engine=engine,
                stdout=stdout,
                stderr=stderr,
            )
        except Exception as exc:  # noqa: BLE001
            stdout.flush()
            stderr.flush()
            state, error = map_pipeline_exception(exc)
            if state is TaskState.FAILED_OCR_MISSING:
                self._ocr_broken_flag.set()
            self._set_failed(task_id, state, error=error)
            return

        stdout.flush()
        stderr.flush()
        summary = SummarySnapshot(
            processed=stats.processed,
            invoices=stats.invoices,
            orders=stats.orders,
            ocr_failures=stats.ocr_failures,
        )
        try:
            self._store.set_done(
                task_id, summary, retain_minutes=self._config.retain_minutes
            )
        except (TaskExpiredError, TaskNotFoundError, ValueError):
            return
        self._publish_snapshot(task_id, "done", terminal=True)

    def _get_engine(self) -> OcrEngine:
        with self._engine_lock:
            if self._engine is None:
                self._engine = self._engine_factory()
                self._ocr_ready_flag.set()
            return self._engine

    def _handle_stdout(self, task_id: str, line: str) -> None:
        parsed = parse_progress_line(line)
        if parsed is None:
            return
        current, total, key = parsed
        try:
            self._store.set_progress(
                task_id, current=current, total=total, key=key
            )
        except (TaskExpiredError, TaskNotFoundError):
            return
        self._publish_snapshot(task_id, "progress")

    def _handle_stderr(self, task_id: str, line: str) -> None:
        message = line.strip()
        if not message:
            return
        try:
            self._store.append_warning(task_id, message)
        except (TaskExpiredError, TaskNotFoundError):
            return
        self._publish_snapshot(task_id, "warning", extra={"message": message})

    def _set_failed(
        self, task_id: str, state: TaskState, *, error: str | None
    ) -> None:
        try:
            self._store.set_failed(
                task_id,
                state,
                retain_minutes=self._config.retain_minutes,
                error=error,
            )
        except (TaskExpiredError, TaskNotFoundError, ValueError):
            return
        except Exception:  # noqa: BLE001
            logger.exception("failed to set task %s to %s", task_id, state.value)
            return
        self._publish_snapshot(task_id, "error", terminal=True)

    def _publish_snapshot(
        self,
        task_id: str,
        event_name: str,
        *,
        terminal: bool = False,
        extra: dict[str, object] | None = None,
    ) -> None:
        try:
            snapshot = self._store.require_snapshot(task_id)
        except (TaskExpiredError, TaskNotFoundError):
            return
        try:
            self._bus.publish_snapshot(
                task_id,
                snapshot,
                event_name=event_name,
                terminal=terminal,
                extra=extra,
            )
        except Exception:  # noqa: BLE001
            logger.exception("failed to publish %s event for task %s", event_name, task_id)
