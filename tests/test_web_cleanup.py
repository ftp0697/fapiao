import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fapiao_pdf.web.cleanup import CleanupManager
from fapiao_pdf.web.config import WebConfig
from fapiao_pdf.web.errors import TaskState
from fapiao_pdf.web.tasks import (
    ExpiredPlaceholder,
    SummarySnapshot,
    TaskRecord,
    TaskSnapshot,
    TaskStore,
)

NOW = datetime(2026, 5, 7, 12, 0, tzinfo=timezone.utc)


class RecordingBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, TaskState, str, bool]] = []
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
            self.events.append((task_id, snapshot.state, event_name, terminal))


def _manager(tmp_path: Path) -> tuple[CleanupManager, TaskStore, RecordingBus]:
    store = TaskStore(tmp_path, placeholder_max=10)
    bus = RecordingBus()
    config = WebConfig(
        task_root=tmp_path,
        retain_minutes=1,
        cleanup_interval_seconds=1,
        download_grace_seconds=60,
        expired_placeholder_hours=24,
    )
    return CleanupManager(store, bus, config), store, bus


def _expired_done(store: TaskStore, now: datetime) -> TaskRecord:
    record = store.create_task(pdf_dpi=200)
    store.set_done(
        record.task_id,
        SummarySnapshot(processed=1, invoices=1, orders=0, ocr_failures=0),
        retain_minutes=1,
    )
    current = store.get_record(record.task_id)
    assert current is not None
    current.completed_at = now - timedelta(minutes=2)
    current.expires_at = now - timedelta(seconds=1)
    return current


def test_sweep_expires_terminal_task_and_deletes_directory(tmp_path: Path) -> None:
    manager, store, bus = _manager(tmp_path)
    record = _expired_done(store, NOW)
    task_dir = record.task_dir

    manager.sweep_once(NOW)

    assert bus.events == [(record.task_id, TaskState.EXPIRED, "expired", True)]
    assert store.get_record(record.task_id) is None
    assert store.is_expired(record.task_id)
    assert not task_dir.exists()


def test_active_tasks_never_expire(tmp_path: Path) -> None:
    manager, store, bus = _manager(tmp_path)
    queued = store.create_task(pdf_dpi=200)
    running = store.create_task(pdf_dpi=200)
    store.set_running(running.task_id)
    queued.expires_at = NOW - timedelta(minutes=1)
    running.expires_at = NOW - timedelta(minutes=1)

    manager.sweep_once(NOW)

    assert bus.events == []
    assert store.get_record(queued.task_id) is queued
    assert store.get_record(running.task_id) is running


def test_expired_active_record_is_not_reexpired(tmp_path: Path) -> None:
    manager, store, _bus = _manager(tmp_path)
    record = store.create_task(pdf_dpi=200)
    record.state = TaskState.EXPIRED
    record.completed_at = NOW - timedelta(minutes=2)
    record.expires_at = NOW - timedelta(seconds=1)

    assert manager.should_delete(record, NOW) is False


def test_active_downloads_prevent_deletion(tmp_path: Path) -> None:
    manager, store, bus = _manager(tmp_path)
    record = _expired_done(store, NOW)
    record.active_downloads = 1

    manager.sweep_once(NOW)

    assert bus.events == []
    assert store.get_record(record.task_id) is record
    assert record.task_dir.exists()


def test_recent_download_grace_prevents_deletion(tmp_path: Path) -> None:
    manager, store, bus = _manager(tmp_path)
    record = _expired_done(store, NOW)
    record.last_download_at = NOW - timedelta(seconds=59)

    manager.sweep_once(NOW)

    assert bus.events == []
    assert store.get_record(record.task_id) is record
    assert record.task_dir.exists()

    record.last_download_at = NOW - timedelta(seconds=60)
    manager.sweep_once(NOW)

    assert bus.events[-1] == (record.task_id, TaskState.EXPIRED, "expired", True)
    assert store.get_record(record.task_id) is None
    assert not record.task_dir.exists()


def test_startup_sweep_loads_records_and_deletes_orphan_dirs(tmp_path: Path) -> None:
    source = TaskStore(tmp_path)
    record = source.create_task(pdf_dpi=200)
    source.set_done(
        record.task_id,
        SummarySnapshot(processed=1, invoices=1, orders=0, ocr_failures=0),
        retain_minutes=60,
    )
    orphan = tmp_path / "orphan"
    orphan.mkdir()

    revived = TaskStore(tmp_path)
    bus = RecordingBus()
    manager = CleanupManager(
        revived,
        bus,
        WebConfig(task_root=tmp_path, retain_minutes=60),
    )

    manager.run_startup_sweep(NOW)

    assert revived.get_record(record.task_id) is not None
    assert not orphan.exists()


def test_atomic_expiry_recheck_blocks_new_download(tmp_path: Path) -> None:
    class RaceManager(CleanupManager):
        def should_delete(self, task, now: datetime) -> bool:
            task.active_downloads = 1
            return True

    manager, store, bus = _manager(tmp_path)
    record = _expired_done(store, NOW)
    manager = RaceManager(store, bus, manager._config)

    manager.sweep_once(NOW)

    assert bus.events == []
    assert store.get_record(record.task_id) is record
    assert record.task_dir.exists()


def test_placeholder_ttl_eviction(tmp_path: Path) -> None:
    manager, store, _bus = _manager(tmp_path)
    old_id = "a" * 32
    fresh_id = "b" * 32
    store._placeholders[old_id] = ExpiredPlaceholder(
        task_id=old_id,
        expired_at=NOW - timedelta(hours=25),
        reason="old",
    )
    store._placeholders[fresh_id] = ExpiredPlaceholder(
        task_id=fresh_id,
        expired_at=NOW - timedelta(hours=23),
        reason="fresh",
    )

    manager.sweep_once(NOW)

    assert not store.is_expired(old_id)
    assert store.is_expired(fresh_id)


def test_old_placeholder_is_kept_when_delete_fails(tmp_path: Path, monkeypatch) -> None:
    manager, store, _bus = _manager(tmp_path)
    old_id = "c" * 32
    store._placeholders[old_id] = ExpiredPlaceholder(
        task_id=old_id,
        expired_at=NOW - timedelta(hours=25),
        reason="old",
    )

    def fail_delete(task_id: str) -> None:
        raise OSError("locked")

    monkeypatch.setattr(store, "delete_task", fail_delete)

    manager.sweep_once(NOW)

    assert store.is_expired(old_id)


def test_start_stop_are_idempotent(tmp_path: Path) -> None:
    manager, _store, _bus = _manager(tmp_path)

    manager.start()
    thread = manager._thread
    manager.start()
    assert manager._thread is thread
    assert thread is not None and thread.is_alive()

    manager.stop(timeout=1.0)
    manager.stop(timeout=1.0)
    assert not thread.is_alive()
