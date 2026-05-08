"""任务保留期清理：启动清扫 + 周期 sweep + placeholder TTL。"""

from __future__ import annotations

import logging
import shutil
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol

from fapiao_pdf.web.config import WebConfig
from fapiao_pdf.web.errors import TaskState, is_terminal
from fapiao_pdf.web.tasks import TaskRecord, TaskSnapshot, TaskStore, _now

logger = logging.getLogger(__name__)

_TASK_FILE = "task.json"


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


class CleanupManager:
    def __init__(self, store: TaskStore, bus: BusPublisher, config: WebConfig) -> None:
        self._store = store
        self._bus = bus
        self._config = config
        self._stop_event = threading.Event()
        self._state_lock = threading.RLock()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        with self._state_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._loop, name="fapiao-web-cleanup", daemon=True
            )
            self._thread.start()

    def stop(self, timeout: float | None = None) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is None:
            return
        thread.join(timeout=timeout)
        if thread.is_alive():
            logger.warning("cleanup worker did not stop within %s seconds", timeout)

    def sweep_once(self, now: datetime) -> None:
        for record in self._store.list_active():
            if self.should_delete(record, now):
                self._expire_and_delete(record, now)
        self._sweep_placeholders(now)

    def run_startup_sweep(self, now: datetime) -> None:
        self._store.load_from_disk(retain_minutes=self._config.retain_minutes)
        self._delete_orphan_dirs()
        self._sweep_placeholders(now)

    def should_delete(self, task: TaskRecord, now: datetime) -> bool:
        if task.state is TaskState.EXPIRED or not is_terminal(task.state):
            return False
        if not self._retention_elapsed(task, now):
            return False
        if task.active_downloads > 0:
            return False
        if task.last_download_at is None:
            return True
        grace = timedelta(seconds=self._config.download_grace_seconds)
        return now - task.last_download_at >= grace

    def _loop(self) -> None:
        while not self._stop_event.wait(self._config.cleanup_interval_seconds):
            try:
                self.sweep_once(_now())
            except Exception:  # noqa: BLE001
                logger.exception("cleanup sweep failed")

    def _retention_elapsed(self, task: TaskRecord, now: datetime) -> bool:
        if task.expires_at is not None:
            return task.expires_at < now
        if task.completed_at is None:
            return False
        return task.completed_at + timedelta(minutes=self._config.retain_minutes) < now

    def _expire_and_delete(self, task: TaskRecord, now: datetime) -> None:
        expired = self._store.expire_if_deletable(
            task.task_id,
            now=now,
            retain_minutes=self._config.retain_minutes,
            download_grace_seconds=self._config.download_grace_seconds,
            reason="retention",
        )
        if expired is None:
            return
        try:
            self._bus.publish_snapshot(
                task.task_id, expired, event_name="expired", terminal=True
            )
        except Exception:  # noqa: BLE001
            logger.exception("failed to publish expired event for task %s", task.task_id)
        self._delete_task_dir(task.task_id)

    def _sweep_placeholders(self, now: datetime) -> None:
        cutoff = now - timedelta(hours=self._config.expired_placeholder_hours)
        for placeholder in self._store.list_placeholders():
            deleted = self._delete_task_dir(placeholder.task_id)
            if placeholder.expired_at <= cutoff and deleted:
                self._store.drop_placeholder(placeholder.task_id)

    def _delete_task_dir(self, task_id: str) -> bool:
        try:
            self._store.delete_task(task_id)
        except OSError:
            logger.exception("failed to delete task directory for task %s", task_id)
            return False
        return not (self._config.task_root / task_id).exists()

    def _delete_orphan_dirs(self) -> None:
        root = self._config.task_root
        if not root.is_dir():
            return
        active_ids = {record.task_id for record in self._store.list_active()}
        for task_dir in sorted(root.iterdir()):
            if self._is_orphan_dir(task_dir, active_ids):
                self._delete_dir(task_dir)

    def _is_orphan_dir(self, task_dir: Path, active_ids: set[str]) -> bool:
        return task_dir.is_dir() and task_dir.name not in active_ids and not (
            task_dir / _TASK_FILE
        ).is_file()

    def _delete_dir(self, path: Path) -> None:
        try:
            shutil.rmtree(path, ignore_errors=False)
        except OSError:
            logger.exception("failed to delete orphan task directory %s", path)
