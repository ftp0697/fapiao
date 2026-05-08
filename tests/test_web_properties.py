import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from fapiao_pdf.web.cleanup import CleanupManager
from fapiao_pdf.web.config import WebConfig
from fapiao_pdf.web.errors import TaskState
from fapiao_pdf.web.tasks import SummarySnapshot, TaskRecord, TaskSnapshot, TaskStore

NOW = datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc)
FAILED_STATES = [TaskState.FAILED_NO_INPUT, TaskState.FAILED_OCR_MISSING, TaskState.FAILED_FATAL, TaskState.FAILED_INTERNAL, TaskState.FAILED_RESTART]


class NoopBus:
    def publish_snapshot(
        self, task_id: str, snapshot: TaskSnapshot, *, event_name: str, terminal: bool = False, extra: dict[str, object] | None = None
    ) -> None:
        return None


@given(count=st.integers(min_value=2, max_value=12))
@settings(max_examples=30, deadline=None)
def test_fifo_queue_positions_follow_submission_order(count: int) -> None:
    with TemporaryDirectory() as td:
        store = TaskStore(Path(td))
        records = [store.create_task(pdf_dpi=200) for _ in range(count)]
        assert [r.queue_seq for r in records] == sorted(r.queue_seq for r in records)
        store.set_running(records[0].task_id)
        assert store.require_snapshot(records[0].task_id).queue_position == 0
        for pos, record in enumerate(records[1:]):
            snap = store.require_snapshot(record.task_id)
            assert snap.state is TaskState.QUEUED
            assert snap.queue_position == pos


@given(currents=st.lists(st.integers(min_value=0, max_value=50), min_size=1, max_size=20))
@settings(max_examples=30, deadline=None)
def test_progress_monotonicity_for_ordered_updates(currents: list[int]) -> None:
    with TemporaryDirectory() as td:
        store = TaskStore(Path(td))
        record = store.create_task(pdf_dpi=200)
        store.set_running(record.task_id)
        seen: list[int] = []
        total = max(currents)
        for current in sorted(currents):
            store.set_progress(record.task_id, current=current, total=total, key=str(current))
            progress = store.require_snapshot(record.task_id).progress
            seen.append(progress.current)
            assert progress.current <= progress.total
        assert seen == sorted(seen)


@given(failed_state=st.sampled_from(FAILED_STATES))
@settings(max_examples=30, deadline=None)
def test_terminal_uniqueness_blocks_later_terminal_transition(failed_state: TaskState) -> None:
    with TemporaryDirectory() as td:
        store = TaskStore(Path(td))
        record = store.create_task(pdf_dpi=200)
        store.set_failed(record.task_id, failed_state, retain_minutes=60, error="boom")
        before = store.require_snapshot(record.task_id)
        with pytest.raises(ValueError):
            store.set_done(record.task_id, SummarySnapshot(0, 0, 0, 0), retain_minutes=60)
        after = store.require_snapshot(record.task_id)
        assert (after.state, after.error, after.summary) == (before.state, before.error, before.summary)


@given(messages=st.lists(st.text(max_size=30), min_size=0, max_size=20))
@settings(max_examples=30, deadline=None)
def test_warning_append_only_preserves_all_prefixes(messages: list[str]) -> None:
    with TemporaryDirectory() as td:
        store = TaskStore(Path(td))
        record = store.create_task(pdf_dpi=200)
        prefixes: list[tuple[str, ...]] = [store.require_snapshot(record.task_id).warnings]
        for message in messages:
            store.append_warning(record.task_id, message)
            warnings = store.require_snapshot(record.task_id).warnings
            assert warnings[:-1] == prefixes[-1]
            prefixes.append(warnings)
        assert prefixes[-1] == tuple(messages)
        for index, prefix in enumerate(prefixes):
            assert prefixes[-1][:index] == prefix


@given(state=st.sampled_from([TaskState.QUEUED, TaskState.RUNNING]), age=st.timedeltas(min_value=timedelta(0), max_value=timedelta(days=365)))
@settings(max_examples=30, deadline=None)
def test_cleanup_never_deletes_active_tasks(state: TaskState, age: timedelta) -> None:
    with TemporaryDirectory() as td:
        root = Path(td)
        manager = CleanupManager(TaskStore(root), NoopBus(), WebConfig(task_root=root))
        task_dir = root / ("a" * 32)
        task_dir.mkdir()
        record = TaskRecord(task_id="a" * 32, state=state, queue_seq=1, created_at=NOW - age, updated_at=NOW - age, completed_at=NOW - age, expires_at=NOW - age, pdf_dpi=200, input_dir=task_dir / "input", output_path=task_dir / "out.pdf")
        assert manager.should_delete(record, NOW) is False


@given(state=st.sampled_from(list(TaskState)), warnings=st.lists(st.text(max_size=20), max_size=8), current=st.integers(min_value=0, max_value=20), total=st.integers(min_value=20, max_value=40), active_downloads=st.integers(min_value=0, max_value=5))
@settings(max_examples=30, deadline=None)
def test_metadata_roundtrip_preserves_persisted_fields(state: TaskState, warnings: list[str], current: int, total: int, active_downloads: int) -> None:
    with TemporaryDirectory() as td:
        task_dir = Path(td) / ("b" * 32)
        record = TaskRecord(
            task_id="b" * 32,
            state=state,
            queue_seq=3,
            created_at=NOW - timedelta(minutes=3),
            updated_at=NOW - timedelta(minutes=2),
            completed_at=NOW - timedelta(minutes=1),
            expires_at=NOW + timedelta(minutes=30),
            pdf_dpi=240,
            input_dir=task_dir / "input",
            output_path=task_dir / "out.pdf",
            warnings=list(warnings),
            summary=SummarySnapshot(3, 1, 2, 0) if state is TaskState.DONE else None,
            error="failed" if state.name.startswith("FAILED") else None,
            active_downloads=active_downloads,
        )
        record.progress.current = current
        record.progress.total = total
        record.progress.key = "page.png"
        payload = json.loads(json.dumps(record.to_json()))
        parsed = TaskRecord.from_json(payload)
        assert parsed.to_json() == payload
        assert parsed.active_downloads == 0
