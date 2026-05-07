import json
from datetime import timedelta
from pathlib import Path

import pytest

from fapiao_pdf.web.errors import TaskExpiredError, TaskNotFoundError, TaskState
from fapiao_pdf.web.tasks import (
    SCHEMA_VERSION,
    ExpiredPlaceholder,
    ProgressSnapshot,
    SummarySnapshot,
    TaskRecord,
    TaskSnapshot,
    TaskStore,
    _now,
)


def _make_record(tmp_path: Path, *, task_id: str = "abc123") -> TaskRecord:
    task_dir = tmp_path / task_id
    task_dir.mkdir()
    now = _now()
    return TaskRecord(
        task_id=task_id,
        state=TaskState.QUEUED,
        queue_seq=1,
        created_at=now,
        updated_at=now,
        pdf_dpi=200,
        input_dir=task_dir / "input",
        output_path=task_dir / "out.pdf",
    )


def test_progress_snapshot_defaults() -> None:
    p = ProgressSnapshot()
    assert p.current == 0 and p.total == 0 and p.key == ""


def test_record_to_json_round_trip(tmp_path: Path) -> None:
    record = _make_record(tmp_path)
    record.warnings.append("OCR 失败：x.png")
    record.summary = SummarySnapshot(processed=5, invoices=3, orders=2, ocr_failures=0)
    record.progress = ProgressSnapshot(current=5, total=5, key="x.png")
    payload = record.to_json()
    assert payload["schema_version"] == SCHEMA_VERSION
    parsed = TaskRecord.from_json(json.loads(json.dumps(payload)))
    assert parsed.task_id == record.task_id
    assert parsed.state is TaskState.QUEUED
    assert parsed.warnings == ["OCR 失败：x.png"]
    assert parsed.summary == record.summary
    assert parsed.progress == record.progress


def test_record_from_json_rejects_wrong_schema(tmp_path: Path) -> None:
    record = _make_record(tmp_path)
    payload = record.to_json()
    payload["schema_version"] = 999
    with pytest.raises(ValueError, match="schema_version"):
        TaskRecord.from_json(payload)


def test_record_result_available(tmp_path: Path) -> None:
    record = _make_record(tmp_path)
    record.state = TaskState.DONE
    assert record.result_available is False
    record.output_path.parent.mkdir(parents=True, exist_ok=True)
    record.output_path.write_bytes(b"%PDF-1.4")
    assert record.result_available is True


def test_create_task_assigns_unique_seq(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    a = store.create_task(pdf_dpi=200)
    b = store.create_task(pdf_dpi=200)
    assert a.queue_seq == 1 and b.queue_seq == 2
    assert a.task_id != b.task_id
    assert (tmp_path / a.task_id / "task.json").is_file()


def test_state_transitions_and_persist(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    record = store.create_task(pdf_dpi=200)
    store.set_running(record.task_id)
    store.set_progress(record.task_id, current=1, total=3, key="a.png")
    store.append_warning(record.task_id, "切分回退")
    store.set_done(
        record.task_id,
        SummarySnapshot(processed=3, invoices=2, orders=1, ocr_failures=0),
        retain_minutes=60,
    )
    record_after = store.get_record(record.task_id)
    assert record_after is not None
    assert record_after.state is TaskState.DONE
    assert record_after.completed_at is not None
    assert record_after.expires_at is not None
    assert record_after.warnings == ["切分回退"]
    on_disk = json.loads(
        (tmp_path / record.task_id / "task.json").read_text(encoding="utf-8")
    )
    assert on_disk["state"] == "done"
    assert on_disk["progress"]["current"] == 1


def test_set_failed_requires_terminal_state(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    record = store.create_task(pdf_dpi=200)
    with pytest.raises(ValueError, match="非失败终态"):
        store.set_failed(record.task_id, TaskState.RUNNING, retain_minutes=60)


def test_set_failed_records_error_message(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    record = store.create_task(pdf_dpi=200)
    store.set_failed(
        record.task_id, TaskState.FAILED_FATAL, retain_minutes=60, error="渲染失败"
    )
    after = store.get_record(record.task_id)
    assert after.state is TaskState.FAILED_FATAL
    assert after.error == "渲染失败"


def test_queue_position(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    a = store.create_task(pdf_dpi=200)
    b = store.create_task(pdf_dpi=200)
    c = store.create_task(pdf_dpi=200)
    assert store.queue_position(a.task_id) == 0
    assert store.queue_position(b.task_id) == 1
    assert store.queue_position(c.task_id) == 2
    store.set_running(a.task_id)
    assert store.queue_position(a.task_id) == 0
    assert store.queue_position(b.task_id) == 0
    store.set_done(
        a.task_id, SummarySnapshot(0, 0, 0, 0), retain_minutes=60
    )
    assert store.queue_position(a.task_id) is None


def test_mark_expired_evicts_oldest_when_capacity_full(tmp_path: Path) -> None:
    store = TaskStore(tmp_path, placeholder_max=2)
    for name in ("a", "b", "c"):
        store._placeholders[name] = ExpiredPlaceholder(
            task_id=name, expired_at=_now(), reason="test"
        )
    store._evict_placeholders()
    assert list(store._placeholders.keys()) == ["b", "c"]


def test_mark_expired_then_require_raises_expired(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    record = store.create_task(pdf_dpi=200)
    store.set_done(record.task_id, SummarySnapshot(0, 0, 0, 0), retain_minutes=60)
    store.mark_expired(record.task_id, reason="ttl")
    with pytest.raises(TaskExpiredError):
        store.require_snapshot(record.task_id)
    assert store.is_expired(record.task_id)


def test_require_snapshot_unknown_raises_not_found(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    with pytest.raises(TaskNotFoundError):
        store.require_snapshot("unknown")


def test_to_snapshot_returns_none_for_unknown(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    assert store.to_snapshot("missing") is None


def test_download_counters_persist_last_at(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    record = store.create_task(pdf_dpi=200)
    store.note_download_started(record.task_id)
    store.note_download_started(record.task_id)
    store.note_download_finished(record.task_id)
    after = store.get_record(record.task_id)
    assert after.active_downloads == 1
    assert after.last_download_at is not None
    on_disk = json.loads(
        (tmp_path / record.task_id / "task.json").read_text(encoding="utf-8")
    )
    assert "active_downloads" not in on_disk
    assert on_disk["last_download_at"] is not None


def test_delete_task_removes_directory(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    record = store.create_task(pdf_dpi=200)
    assert (tmp_path / record.task_id).is_dir()
    store.delete_task(record.task_id)
    assert not (tmp_path / record.task_id).exists()
    assert store.get_record(record.task_id) is None


def test_load_from_disk_marks_running_as_failed_restart(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    record = store.create_task(pdf_dpi=200)
    store.set_running(record.task_id)
    revived = TaskStore(tmp_path)
    revived.load_from_disk(retain_minutes=60)
    assert revived.is_expired(record.task_id)
    assert revived.get_record(record.task_id) is None
    assert not (tmp_path / record.task_id).exists()


def test_load_from_disk_handles_corrupt_json(tmp_path: Path) -> None:
    bad_dir = tmp_path / "bad"
    bad_dir.mkdir()
    (bad_dir / "task.json").write_text("not json", encoding="utf-8")
    store = TaskStore(tmp_path)
    store.load_from_disk(retain_minutes=60)
    assert "bad" in [p.task_id for p in store.list_placeholders()]
    assert not bad_dir.exists()


def test_load_from_disk_rejects_path_traversal(tmp_path: Path) -> None:
    """task.json 内 input_dir 越界应被拒绝。"""
    store = TaskStore(tmp_path)
    record = store.create_task(pdf_dpi=200)
    store.set_done(record.task_id, SummarySnapshot(0, 0, 0, 0), retain_minutes=60)
    on_disk_path = tmp_path / record.task_id / "task.json"
    data = json.loads(on_disk_path.read_text(encoding="utf-8"))
    data["input_dir"] = str(tmp_path.parent)  # 越界路径
    on_disk_path.write_text(json.dumps(data), encoding="utf-8")
    revived = TaskStore(tmp_path)
    revived.load_from_disk(retain_minutes=60)
    assert any(
        p.reason == "corrupt-startup" for p in revived.list_placeholders()
    )
    assert revived.get_record(record.task_id) is None


def test_load_from_disk_keeps_terminal_records(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    record = store.create_task(pdf_dpi=200)
    store.set_done(record.task_id, SummarySnapshot(1, 1, 0, 0), retain_minutes=60)
    revived = TaskStore(tmp_path)
    revived.load_from_disk(retain_minutes=60)
    snapshot = revived.require_snapshot(record.task_id)
    assert snapshot.state is TaskState.DONE


def test_load_from_disk_drops_already_expired_terminal(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    record = store.create_task(pdf_dpi=200)
    store.set_done(record.task_id, SummarySnapshot(0, 0, 0, 0), retain_minutes=60)
    rec = store.get_record(record.task_id)
    rec.expires_at = _now() - timedelta(minutes=1)
    store._persist(rec)
    revived = TaskStore(tmp_path)
    revived.load_from_disk(retain_minutes=60)
    assert revived.get_record(record.task_id) is None
    assert not (tmp_path / record.task_id).exists()


def test_snapshot_isolates_mutable_fields(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    record = store.create_task(pdf_dpi=200)
    store.append_warning(record.task_id, "first")
    snapshot = store.require_snapshot(record.task_id)
    assert isinstance(snapshot, TaskSnapshot)
    assert snapshot.warnings == ("first",)
    store.append_warning(record.task_id, "second")
    assert snapshot.warnings == ("first",)


def test_state_transitions_guard_against_terminal(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    record = store.create_task(pdf_dpi=200)
    store.set_running(record.task_id)
    store.set_done(record.task_id, SummarySnapshot(0, 0, 0, 0), retain_minutes=60)
    with pytest.raises(ValueError, match="已终态"):
        store.set_done(record.task_id, SummarySnapshot(0, 0, 0, 0), retain_minutes=60)
    store.set_progress(record.task_id, current=99, total=99, key="late")
    after = store.get_record(record.task_id)
    assert after.progress.current == 0  # 终态后进度更新被忽略
