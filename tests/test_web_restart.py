import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from fapiao_pdf.web.cleanup import CleanupManager
from fapiao_pdf.web.config import WebConfig
from fapiao_pdf.web.errors import TaskExpiredError, TaskState
from fapiao_pdf.web.tasks import SummarySnapshot, TaskRecord, TaskSnapshot, TaskStore

NOW = datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc)


class NoopBus:
    def publish_snapshot(
        self, task_id: str, snapshot: TaskSnapshot, *, event_name: str, terminal: bool = False, extra: dict[str, object] | None = None
    ) -> None:
        return None


def _task_id(ch: str) -> str:
    return ch * 32


def _write_task(root: Path, task_id: str, state: TaskState, *, completed_at: datetime | None = None, expires_at: datetime | None = None) -> None:
    task_dir = root / task_id
    record = TaskRecord(
        task_id=task_id,
        state=state,
        queue_seq=7,
        created_at=NOW - timedelta(minutes=5),
        updated_at=NOW - timedelta(minutes=4),
        completed_at=completed_at,
        expires_at=expires_at,
        pdf_dpi=200,
        input_dir=task_dir / "input",
        output_path=task_dir / "out.pdf",
        summary=SummarySnapshot(1, 1, 0, 0) if state is TaskState.DONE else None,
    )
    record.input_dir.mkdir(parents=True)
    with (task_dir / "task.json").open("w", encoding="utf-8") as fh:
        json.dump(record.to_json(), fh)


@pytest.mark.parametrize("state", [TaskState.RUNNING, TaskState.QUEUED])
def test_load_from_disk_marks_active_tasks_failed_restart(tmp_path: Path, state: TaskState) -> None:
    config = WebConfig(task_root=tmp_path)
    task_id = _task_id("a" if state is TaskState.RUNNING else "b")
    _write_task(config.task_root, task_id, state)
    store = TaskStore(config.task_root)

    store.load_from_disk(retain_minutes=config.retain_minutes)

    assert store.to_snapshot(task_id) is None
    assert store.is_expired(task_id)
    assert not (config.task_root / task_id).exists()
    assert next(p for p in store.list_placeholders() if p.task_id == task_id).reason == "failed-restart"
    with pytest.raises(TaskExpiredError):
        store.require_snapshot(task_id)


def test_load_from_disk_deletes_corrupt_json_and_adds_placeholder(tmp_path: Path) -> None:
    task_id = _task_id("c")
    task_dir = tmp_path / task_id
    task_dir.mkdir(parents=True)
    (task_dir / "task.json").write_text("{bad json", encoding="utf-8")
    store = TaskStore(tmp_path)

    store.load_from_disk(retain_minutes=60)

    assert not task_dir.exists()
    assert next(p for p in store.list_placeholders() if p.task_id == task_id).reason == "corrupt-startup"


def test_load_from_disk_keeps_unexpired_terminal_record(tmp_path: Path) -> None:
    task_id = _task_id("d")
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=30)
    _write_task(tmp_path, task_id, TaskState.DONE, completed_at=expires_at - timedelta(minutes=30), expires_at=expires_at)
    store = TaskStore(tmp_path)

    store.load_from_disk(retain_minutes=60)

    snapshot = store.require_snapshot(task_id)
    assert snapshot.state is TaskState.DONE
    assert snapshot.summary == SummarySnapshot(1, 1, 0, 0)
    assert (tmp_path / task_id).exists()


def test_load_from_disk_drops_terminal_record_beyond_retention(tmp_path: Path) -> None:
    task_id = _task_id("e")
    completed_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    _write_task(tmp_path, task_id, TaskState.DONE, completed_at=completed_at, expires_at=completed_at + timedelta(minutes=1))
    store = TaskStore(tmp_path)

    store.load_from_disk(retain_minutes=1)

    assert store.to_snapshot(task_id) is None
    assert not store.is_expired(task_id)
    assert not (tmp_path / task_id).exists()


def test_startup_sweep_removes_orphan_dir_and_leaves_random_file(tmp_path: Path) -> None:
    random_file = tmp_path / "notes.txt"
    random_file.write_text("ignore me", encoding="utf-8")
    orphan_dir = tmp_path / "not-a-task"
    orphan_dir.mkdir()
    (orphan_dir / "random.txt").write_text("orphan", encoding="utf-8")
    manager = CleanupManager(TaskStore(tmp_path), NoopBus(), WebConfig(task_root=tmp_path))

    manager.run_startup_sweep(datetime.now(timezone.utc))

    assert random_file.exists()
    assert not orphan_dir.exists()
