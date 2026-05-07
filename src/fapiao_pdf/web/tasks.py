"""任务模型与持久化：dataclass + TaskStore（线程安全）+ task.json round-trip。"""

import json
import os
import re
import shutil
import threading
from collections import OrderedDict
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final
from uuid import uuid4

from fapiao_pdf.web.errors import TaskExpiredError, TaskNotFoundError, TaskState

SCHEMA_VERSION: Final[int] = 1
_TASK_FILE: Final[str] = "task.json"
_TASK_FILE_TMP: Final[str] = "task.json.tmp"
_TASK_ID_RE: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{32}$")

_TERMINAL_FAILED: Final[frozenset[TaskState]] = frozenset(
    {
        TaskState.FAILED_NO_INPUT,
        TaskState.FAILED_OCR_MISSING,
        TaskState.FAILED_FATAL,
        TaskState.FAILED_INTERNAL,
        TaskState.FAILED_RESTART,
    }
)

_TERMINAL_ALL: Final[frozenset[TaskState]] = _TERMINAL_FAILED | {TaskState.DONE}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _to_iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _from_iso(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


@dataclass(slots=True)
class ProgressSnapshot:
    current: int = 0
    total: int = 0
    key: str = ""


@dataclass(slots=True)
class SummarySnapshot:
    processed: int
    invoices: int
    orders: int
    ocr_failures: int


@dataclass(slots=True)
class TaskRecord:
    task_id: str
    state: TaskState
    queue_seq: int
    created_at: datetime
    updated_at: datetime
    pdf_dpi: int
    input_dir: Path
    output_path: Path
    progress: ProgressSnapshot = field(default_factory=ProgressSnapshot)
    warnings: list[str] = field(default_factory=list)
    summary: SummarySnapshot | None = None
    error: str | None = None
    completed_at: datetime | None = None
    expires_at: datetime | None = None
    last_download_at: datetime | None = None
    active_downloads: int = 0  # 进程内字段，不持久化

    @property
    def task_dir(self) -> Path:
        return self.input_dir.parent

    @property
    def result_available(self) -> bool:
        return self.state is TaskState.DONE and self.output_path.exists()

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "task_id": self.task_id,
            "state": self.state.value,
            "queue_seq": self.queue_seq,
            "created_at": _to_iso(self.created_at),
            "updated_at": _to_iso(self.updated_at),
            "completed_at": _to_iso(self.completed_at),
            "expires_at": _to_iso(self.expires_at),
            "pdf_dpi": self.pdf_dpi,
            "input_dir": str(self.input_dir),
            "output_path": str(self.output_path),
            "progress": asdict(self.progress),
            "warnings": list(self.warnings),
            "summary": asdict(self.summary) if self.summary else None,
            "error": self.error,
            "result_available": self.result_available,
            "last_download_at": _to_iso(self.last_download_at),
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "TaskRecord":
        version = data.get("schema_version")
        if version != SCHEMA_VERSION:
            raise ValueError(f"不支持的 schema_version：{version}")
        progress_data = data.get("progress") or {}
        summary_data = data.get("summary")
        return cls(
            task_id=data["task_id"],
            state=TaskState(data["state"]),
            queue_seq=int(data["queue_seq"]),
            created_at=_from_iso(data["created_at"]),  # type: ignore[arg-type]
            updated_at=_from_iso(data["updated_at"]),  # type: ignore[arg-type]
            completed_at=_from_iso(data.get("completed_at")),
            expires_at=_from_iso(data.get("expires_at")),
            pdf_dpi=int(data["pdf_dpi"]),
            input_dir=Path(data["input_dir"]),
            output_path=Path(data["output_path"]),
            progress=ProgressSnapshot(**progress_data),
            warnings=list(data.get("warnings") or []),
            summary=SummarySnapshot(**summary_data) if summary_data else None,
            error=data.get("error"),
            last_download_at=_from_iso(data.get("last_download_at")),
        )


@dataclass(slots=True, frozen=True)
class TaskSnapshot:
    task_id: str
    state: TaskState
    queue_position: int | None
    progress: ProgressSnapshot
    warnings: tuple[str, ...]
    summary: SummarySnapshot | None
    error: str | None
    created_at: datetime
    expires_at: datetime | None
    result_available: bool


@dataclass(slots=True)
class ExpiredPlaceholder:
    task_id: str
    expired_at: datetime
    reason: str


class TaskStore:
    """线程安全的任务记录中心；活动 dict + placeholder OrderedDict。"""

    def __init__(self, task_root: Path, *, placeholder_max: int = 1024) -> None:
        self._root = task_root
        self._placeholder_max = placeholder_max
        self._lock = threading.RLock()
        self._records: dict[str, TaskRecord] = {}
        self._placeholders: OrderedDict[str, ExpiredPlaceholder] = OrderedDict()
        self._next_seq = 1

    def create_task(self, *, pdf_dpi: int) -> TaskRecord:
        with self._lock:
            task_id = uuid4().hex
            task_dir = self._root / task_id
            input_dir = task_dir / "input"
            output_path = task_dir / f"fapiao-{task_id[:8]}.pdf"
            seq = self._next_seq
            self._next_seq += 1
            now = _now()
            record = TaskRecord(
                task_id=task_id,
                state=TaskState.QUEUED,
                queue_seq=seq,
                created_at=now,
                updated_at=now,
                pdf_dpi=pdf_dpi,
                input_dir=input_dir,
                output_path=output_path,
            )
            self._records[task_id] = record
            input_dir.mkdir(parents=True, exist_ok=True)
            self._persist(record)
            return record

    def set_running(self, task_id: str) -> None:
        with self._lock:
            record = self._require(task_id)
            if record.state is not TaskState.QUEUED:
                raise ValueError(f"非法状态迁移：{record.state} → running")
            record.state = TaskState.RUNNING
            record.updated_at = _now()
            self._persist(record)

    def set_progress(
        self, task_id: str, *, current: int, total: int, key: str
    ) -> None:
        with self._lock:
            record = self._require(task_id)
            if record.state is not TaskState.RUNNING:
                return  # 终态或非 running 时静默忽略，防陈旧事件污染
            record.progress = ProgressSnapshot(current=current, total=total, key=key)
            record.updated_at = _now()
            self._persist(record)

    def append_warning(self, task_id: str, message: str) -> None:
        with self._lock:
            record = self._require(task_id)
            if record.state in _TERMINAL_ALL:
                return  # 终态后不再追加，保证单调性
            record.warnings.append(message)
            record.updated_at = _now()
            self._persist(record)

    def set_done(
        self, task_id: str, summary: SummarySnapshot, *, retain_minutes: int
    ) -> None:
        with self._lock:
            record = self._require(task_id)
            if record.state in _TERMINAL_ALL:
                raise ValueError(f"任务已终态：{record.state}")
            now = _now()
            record.state = TaskState.DONE
            record.summary = summary
            record.completed_at = now
            record.expires_at = self._compute_expires(now, retain_minutes)
            record.updated_at = now
            self._persist(record)

    def set_failed(
        self,
        task_id: str,
        state: TaskState,
        *,
        retain_minutes: int,
        error: str | None = None,
    ) -> None:
        if state not in _TERMINAL_FAILED:
            raise ValueError(f"非失败终态：{state}")
        with self._lock:
            record = self._require(task_id)
            if record.state in _TERMINAL_ALL:
                raise ValueError(f"任务已终态：{record.state}")
            now = _now()
            record.state = state
            record.error = error
            record.completed_at = now
            record.expires_at = self._compute_expires(now, retain_minutes)
            record.updated_at = now
            self._persist(record)

    def mark_expired(self, task_id: str, *, reason: str) -> None:
        with self._lock:
            self._records.pop(task_id, None)
            placeholder = ExpiredPlaceholder(
                task_id=task_id, expired_at=_now(), reason=reason
            )
            self._placeholders[task_id] = placeholder
            self._evict_placeholders()

    def note_download_started(self, task_id: str) -> None:
        with self._lock:
            record = self._require(task_id)
            record.active_downloads += 1
            record.last_download_at = _now()
            self._persist(record)

    def note_download_finished(self, task_id: str) -> None:
        with self._lock:
            record = self._records.get(task_id)
            if record is None:
                return
            record.active_downloads = max(0, record.active_downloads - 1)
            record.last_download_at = _now()
            self._persist(record)

    def queue_position(self, task_id: str) -> int | None:
        with self._lock:
            record = self._records.get(task_id)
            if record is None or record.state is not TaskState.QUEUED:
                return 0 if record and record.state is TaskState.RUNNING else None
            return sum(
                1
                for other in self._records.values()
                if other.state is TaskState.QUEUED and other.queue_seq < record.queue_seq
            )

    def to_snapshot(self, task_id: str) -> TaskSnapshot | None:
        with self._lock:
            record = self._records.get(task_id)
            if record is None:
                return None
            return TaskSnapshot(
                task_id=record.task_id,
                state=record.state,
                queue_position=self.queue_position(task_id),
                progress=replace(record.progress),
                warnings=tuple(record.warnings),
                summary=replace(record.summary) if record.summary else None,
                error=record.error,
                created_at=record.created_at,
                expires_at=record.expires_at,
                result_available=record.result_available,
            )

    def require_snapshot(self, task_id: str) -> TaskSnapshot:
        with self._lock:
            snapshot = self.to_snapshot(task_id)
            if snapshot is not None:
                return snapshot
            if task_id in self._placeholders:
                raise TaskExpiredError(task_id)
            raise TaskNotFoundError(task_id)

    def is_expired(self, task_id: str) -> bool:
        with self._lock:
            return task_id in self._placeholders

    def get_record(self, task_id: str) -> TaskRecord | None:
        with self._lock:
            return self._records.get(task_id)

    def list_active(self) -> list[TaskRecord]:
        with self._lock:
            return list(self._records.values())

    def list_placeholders(self) -> list[ExpiredPlaceholder]:
        with self._lock:
            return list(self._placeholders.values())

    def delete_task(self, task_id: str) -> None:
        with self._lock:
            record = self._records.pop(task_id, None)
            if record is None:
                return
            shutil.rmtree(record.task_dir, ignore_errors=False)

    def load_from_disk(self, *, retain_minutes: int) -> None:
        """重启恢复：标 failed-restart + placeholder + 删除目录；保留终态记录。"""
        if not self._root.is_dir():
            return
        with self._lock:
            for task_dir in sorted(self._root.iterdir()):
                if not task_dir.is_dir():
                    continue
                self._recover_one(task_dir, retain_minutes=retain_minutes)
            if self._records:
                self._next_seq = max(r.queue_seq for r in self._records.values()) + 1

    def _recover_one(self, task_dir: Path, *, retain_minutes: int) -> None:
        meta_path = task_dir / _TASK_FILE
        if not meta_path.is_file():
            shutil.rmtree(task_dir, ignore_errors=True)
            return
        record = self._safe_load(task_dir, meta_path)
        if record is None:
            placeholder = ExpiredPlaceholder(
                task_id=task_dir.name, expired_at=_now(), reason="corrupt-startup"
            )
            self._placeholders[placeholder.task_id] = placeholder
            shutil.rmtree(task_dir, ignore_errors=True)
            self._evict_placeholders()
            return
        if record.state in {TaskState.QUEUED, TaskState.RUNNING}:
            placeholder = ExpiredPlaceholder(
                task_id=record.task_id, expired_at=_now(), reason="failed-restart"
            )
            self._placeholders[record.task_id] = placeholder
            shutil.rmtree(task_dir, ignore_errors=True)
            self._evict_placeholders()
            return
        if record.expires_at and record.expires_at < _now():
            shutil.rmtree(task_dir, ignore_errors=True)
            return
        self._records[record.task_id] = record

    def _safe_load(self, task_dir: Path, meta_path: Path) -> TaskRecord | None:
        """严格解析 + 路径校验；任何异常或越界都返回 None。"""
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
            record = TaskRecord.from_json(data)
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            return None
        if not _TASK_ID_RE.match(record.task_id):
            return None
        if record.task_id != task_dir.name:
            return None
        try:
            root = task_dir.resolve()
            input_resolved = record.input_dir.resolve()
            output_resolved = record.output_path.resolve()
        except OSError:
            return None
        if not _is_within(input_resolved, root):
            return None
        if not _is_within(output_resolved, root):
            return None
        return record

    def _persist(self, record: TaskRecord) -> None:
        record.task_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = record.task_dir / _TASK_FILE_TMP
        final_path = record.task_dir / _TASK_FILE
        tmp_path.write_text(
            json.dumps(record.to_json(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp_path, final_path)

    def _require(self, task_id: str) -> TaskRecord:
        record = self._records.get(task_id)
        if record is None:
            if task_id in self._placeholders:
                raise TaskExpiredError(task_id)
            raise TaskNotFoundError(task_id)
        return record

    def _compute_expires(self, now: datetime, retain_minutes: int) -> datetime:
        from datetime import timedelta

        return now + timedelta(minutes=retain_minutes)

    def _evict_placeholders(self) -> None:
        while len(self._placeholders) > self._placeholder_max:
            self._placeholders.popitem(last=False)


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False
