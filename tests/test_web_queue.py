import threading
import time
from pathlib import Path
from typing import TextIO

import pytest

from fapiao_pdf.ocr import OcrModelMissingError
from fapiao_pdf.pipeline import FatalRunError, RunStats
from fapiao_pdf.web.config import WebConfig
from fapiao_pdf.web.errors import OcrBrokenError, TaskState
from fapiao_pdf.web.queue import SerialMergeExecutor
from fapiao_pdf.web.tasks import TaskSnapshot, TaskStore


class FakeOcrEngine:
    pass


class RecordingBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, bool, dict[str, object] | None]] = []
        self._lock = threading.Lock()

    def publish_snapshot(
        self,
        task_id: str,
        snapshot: TaskSnapshot,
        *,
        event_name: str,
        terminal: bool = False,
        extra: dict[str, object] | None = None,
    ) -> None:
        with self._lock:
            self.events.append((task_id, event_name, terminal, extra))

    def names_for(self, task_id: str) -> list[str]:
        with self._lock:
            return [name for tid, name, _, _ in self.events if tid == task_id]


def _executor(
    tmp_path: Path,
    run_merge_fn,
    engine_factory_fn=lambda: FakeOcrEngine(),
) -> tuple[SerialMergeExecutor, TaskStore, RecordingBus]:
    store = TaskStore(tmp_path)
    bus = RecordingBus()
    executor = SerialMergeExecutor(
        store,
        bus,
        WebConfig(task_root=tmp_path, retain_minutes=1),
        run_merge_fn=run_merge_fn,
        engine_factory_fn=engine_factory_fn,
    )
    return executor, store, bus


def _wait_for_state(
    store: TaskStore,
    task_id: str,
    state: TaskState,
    *,
    timeout: float = 2.0,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        record = store.get_record(task_id)
        if record is not None and record.state is state:
            return
        time.sleep(0.01)
    record = store.get_record(task_id)
    actual = None if record is None else record.state
    raise AssertionError(f"timed out waiting for {state}, got {actual}")


def _wait_for_event(bus: RecordingBus, task_id: str, event_name: str) -> None:
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if event_name in bus.names_for(task_id):
            return
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for event {event_name}")


def test_executor_runs_tasks_serially_and_records_progress(tmp_path: Path) -> None:
    active = 0
    max_active = 0
    call_order: list[str] = []
    lock = threading.Lock()

    def fake_run_merge(
        input_dir: Path,
        output: Path,
        *,
        force: bool,
        pdf_dpi: int,
        workers: int,
        engine: FakeOcrEngine,
        stdout: TextIO,
        stderr: TextIO,
    ) -> RunStats:
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
            call_order.append(input_dir.parent.name)
        stdout.write("处理中 1/2 - a.pdf\n")
        stderr.write("切分警告：a.pdf\n")
        time.sleep(0.02)
        output.write_bytes(b"%PDF-1.4\n")
        with lock:
            active -= 1
        return RunStats(processed=2, invoices=1, orders=1, ocr_failures=0)

    executor, store, bus = _executor(tmp_path, fake_run_merge)
    first = store.create_task(pdf_dpi=200)
    second = store.create_task(pdf_dpi=200)

    assert executor.enqueue(first.task_id) == 0
    assert executor.enqueue(second.task_id) == 1
    assert executor.queue_depth() == 2
    executor.start()

    _wait_for_state(store, first.task_id, TaskState.DONE)
    _wait_for_state(store, second.task_id, TaskState.DONE)
    executor.stop(grace_seconds=1.0)

    assert max_active == 1
    assert call_order == [first.task_id, second.task_id]
    assert executor.ocr_ready_flag() is True
    for task_id in (first.task_id, second.task_id):
        record = store.get_record(task_id)
        assert record is not None
        assert record.progress.current == 1
        assert record.progress.total == 2
        assert record.warnings == ["切分警告：a.pdf"]
        assert record.summary is not None
        assert record.summary.processed == 2
        assert bus.names_for(task_id) == [
            "queued",
            "progress",
            "progress",
            "warning",
            "done",
        ]


def test_executor_maps_pipeline_exception_to_failed_state(tmp_path: Path) -> None:
    def fake_run_merge(*args, **kwargs) -> RunStats:
        raise FatalRunError("渲染失败")

    executor, store, bus = _executor(tmp_path, fake_run_merge)
    record = store.create_task(pdf_dpi=200)

    executor.start()
    executor.enqueue(record.task_id)
    _wait_for_state(store, record.task_id, TaskState.FAILED_FATAL)
    executor.stop(grace_seconds=1.0)

    after = store.get_record(record.task_id)
    assert after is not None
    assert after.error == "渲染失败"
    assert bus.names_for(record.task_id)[-1] == "error"
    assert any(terminal for tid, _, terminal, _ in bus.events if tid == record.task_id)


def test_worker_survives_unexpected_task_error(tmp_path: Path) -> None:
    class FlakyStore(TaskStore):
        def __init__(self, root: Path) -> None:
            super().__init__(root)
            self.fail_task_id = ""

        def set_running(self, task_id: str) -> None:
            if task_id == self.fail_task_id:
                raise RuntimeError("状态写入失败")
            super().set_running(task_id)

    def fake_run_merge(
        input_dir: Path,
        output: Path,
        *,
        force: bool,
        pdf_dpi: int,
        workers: int,
        engine: FakeOcrEngine,
        stdout: TextIO,
        stderr: TextIO,
    ) -> RunStats:
        output.write_bytes(b"%PDF-1.4\n")
        return RunStats(processed=1, invoices=1, orders=0, ocr_failures=0)

    store = FlakyStore(tmp_path)
    bus = RecordingBus()
    executor = SerialMergeExecutor(
        store,
        bus,
        WebConfig(task_root=tmp_path, retain_minutes=1),
        run_merge_fn=fake_run_merge,
        engine_factory_fn=lambda: FakeOcrEngine(),
    )
    broken = store.create_task(pdf_dpi=200)
    next_task = store.create_task(pdf_dpi=200)
    store.fail_task_id = broken.task_id

    executor.start()
    executor.enqueue(broken.task_id)
    executor.enqueue(next_task.task_id)
    _wait_for_state(store, broken.task_id, TaskState.FAILED_INTERNAL)
    _wait_for_state(store, next_task.task_id, TaskState.DONE)
    executor.stop(grace_seconds=1.0)


def test_ocr_model_missing_sets_broken_latch_and_rejects_enqueue(tmp_path: Path) -> None:
    def fake_run_merge(*args, **kwargs) -> RunStats:
        raise AssertionError("run_merge should not be called")

    executor, store, bus = _executor(
        tmp_path,
        fake_run_merge,
        engine_factory_fn=lambda: (_ for _ in ()).throw(
            OcrModelMissingError("缓存缺失")
        ),
    )
    record = store.create_task(pdf_dpi=200)

    executor.start()
    executor.enqueue(record.task_id)
    _wait_for_state(store, record.task_id, TaskState.FAILED_OCR_MISSING)

    assert executor.ocr_broken_flag() is True
    assert executor.ocr_ready_flag() is False
    assert store.get_record(record.task_id).error == "缓存缺失"  # type: ignore[union-attr]
    _wait_for_event(bus, record.task_id, "error")
    assert bus.names_for(record.task_id)[-1] == "error"
    with pytest.raises(OcrBrokenError):
        executor.enqueue(store.create_task(pdf_dpi=200).task_id)
    executor.stop(grace_seconds=1.0)


def test_stop_sets_latch_and_does_not_interrupt_running_task(tmp_path: Path) -> None:
    entered = threading.Event()
    release = threading.Event()

    def fake_run_merge(
        input_dir: Path,
        output: Path,
        *,
        force: bool,
        pdf_dpi: int,
        workers: int,
        engine: FakeOcrEngine,
        stdout: TextIO,
        stderr: TextIO,
    ) -> RunStats:
        entered.set()
        release.wait(timeout=2.0)
        output.write_bytes(b"%PDF-1.4\n")
        return RunStats(processed=1, invoices=1, orders=0, ocr_failures=0)

    executor, store, _bus = _executor(tmp_path, fake_run_merge)
    record = store.create_task(pdf_dpi=200)
    queued_after_stop = store.create_task(pdf_dpi=200)

    executor.start()
    executor.enqueue(record.task_id)
    assert entered.wait(timeout=1.0)
    assert executor.current_task_id() == record.task_id
    executor.enqueue(queued_after_stop.task_id)

    executor.stop(grace_seconds=0.01)
    running_record = store.get_record(record.task_id)
    queued_record = store.get_record(queued_after_stop.task_id)
    assert running_record is not None
    assert queued_record is not None
    assert running_record.state is TaskState.RUNNING
    assert queued_record.state is TaskState.QUEUED

    release.set()
    executor.stop(grace_seconds=1.0)
    _wait_for_state(store, record.task_id, TaskState.DONE)
    queued_record = store.get_record(queued_after_stop.task_id)
    assert queued_record is not None
    assert queued_record.state is TaskState.QUEUED
