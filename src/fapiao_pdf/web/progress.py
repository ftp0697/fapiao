"""进度捕获 + 事件总线 + SSE 流。"""

import asyncio
import io
import json
import re
import threading
from collections.abc import AsyncIterator, Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Final
from uuid import uuid4

from fapiao_pdf.web.errors import TooManyStreamsError, is_terminal
from fapiao_pdf.web.tasks import TaskSnapshot, TaskStore, _now

PROGRESS_RE: Final[re.Pattern[str]] = re.compile(
    r"^处理中 (?P<current>\d+)/(?P<total>\d+) - (?P<key>.+)$"
)


def parse_progress_line(line: str) -> tuple[int, int, str] | None:
    """命中返回 (current, total, key)；未命中返回 None。"""
    match = PROGRESS_RE.match(line.rstrip())
    if match is None:
        return None
    return (
        int(match.group("current")),
        int(match.group("total")),
        match.group("key"),
    )


class PipelineTextCapture(io.TextIOBase):
    """非 TTY 文本流捕获；按 \\r 或 \\n 切行回调。"""

    def __init__(self, on_line: Callable[[str], None]) -> None:
        super().__init__()
        self._on_line = on_line
        self._buffer = ""

    def writable(self) -> bool:  # type: ignore[override]
        return True

    def isatty(self) -> bool:  # type: ignore[override]
        return False

    def write(self, s: str) -> int:  # type: ignore[override]
        if not s:
            return 0
        self._buffer += s
        while True:
            cr = self._buffer.find("\r")
            lf = self._buffer.find("\n")
            if cr == -1 and lf == -1:
                break
            idx = lf if cr == -1 else (cr if lf == -1 else min(cr, lf))
            line = self._buffer[:idx]
            self._buffer = self._buffer[idx + 1 :]
            if line:
                self._on_line(line)
        return len(s)

    def flush(self) -> None:  # type: ignore[override]
        if self._buffer:
            line = self._buffer
            self._buffer = ""
            if line:
                self._on_line(line)


@dataclass(slots=True, frozen=True)
class TaskEvent:
    name: str
    payload: dict[str, Any]
    terminal: bool = False


@dataclass(slots=True)
class Subscriber:
    subscriber_id: str
    loop: asyncio.AbstractEventLoop
    queue: asyncio.Queue[TaskEvent] = field(repr=False)


class EventBus:
    """跨线程事件总线：worker 线程发布 → 通过 loop.call_soon_threadsafe 投递到订阅者 asyncio 队列。"""

    def __init__(
        self,
        *,
        max_subscribers_per_task: int = 16,
        subscriber_queue_maxsize: int = 32,
    ) -> None:
        self._max_subs = max_subscribers_per_task
        self._queue_max = subscriber_queue_maxsize
        self._lock = threading.RLock()
        self._subs: dict[str, dict[str, Subscriber]] = {}

    def subscribe(
        self, task_id: str, loop: asyncio.AbstractEventLoop
    ) -> Subscriber:
        with self._lock:
            bucket = self._subs.setdefault(task_id, {})
            if len(bucket) >= self._max_subs:
                raise TooManyStreamsError(task_id)
            sub = Subscriber(
                subscriber_id=uuid4().hex,
                loop=loop,
                queue=asyncio.Queue(maxsize=self._queue_max),
            )
            bucket[sub.subscriber_id] = sub
            return sub

    def unsubscribe(self, task_id: str, subscriber_id: str) -> None:
        with self._lock:
            bucket = self._subs.get(task_id)
            if bucket is None:
                return
            bucket.pop(subscriber_id, None)
            if not bucket:
                self._subs.pop(task_id, None)

    def subscriber_count(self, task_id: str) -> int:
        with self._lock:
            return len(self._subs.get(task_id, {}))

    def publish_snapshot(
        self,
        task_id: str,
        snapshot: TaskSnapshot,
        *,
        event_name: str,
        terminal: bool = False,
        extra: dict[str, Any] | None = None,
    ) -> None:
        payload = _snapshot_to_payload(snapshot)
        if extra:
            payload.update(extra)
        event = TaskEvent(name=event_name, payload=payload, terminal=terminal)
        self._dispatch(task_id, event)

    def publish_heartbeat(self, task_id: str) -> None:
        event = TaskEvent(
            name="heartbeat",
            payload={"task_id": task_id, "server_time": _now().isoformat()},
            terminal=False,
        )
        self._dispatch(task_id, event)

    def _dispatch(self, task_id: str, event: TaskEvent) -> None:
        with self._lock:
            recipients = list(self._subs.get(task_id, {}).values())
        for sub in recipients:
            try:
                sub.loop.call_soon_threadsafe(_deliver, sub, event)
            except RuntimeError:
                # loop 已关闭，忽略
                continue


def _deliver(subscriber: Subscriber, event: TaskEvent) -> None:
    queue = subscriber.queue
    try:
        queue.put_nowait(event)
        return
    except asyncio.QueueFull:
        pass

    items: list[TaskEvent] = []
    while not queue.empty():
        items.append(queue.get_nowait())

    drop_index = next((i for i, item in enumerate(items) if not item.terminal), None)
    if drop_index is None:
        if not event.terminal:
            for item in items:
                queue.put_nowait(item)
            return
        items = []
    else:
        del items[drop_index]

    for item in items:
        queue.put_nowait(item)
    try:
        queue.put_nowait(event)
    except asyncio.QueueFull:
        return


def format_sse(event: TaskEvent) -> str:
    return f"event: {event.name}\ndata: {json.dumps(event.payload, ensure_ascii=False)}\n\n"


async def stream_task_events(
    task_id: str,
    *,
    store: TaskStore,
    bus: EventBus,
    heartbeat_seconds: float,
) -> AsyncIterator[str]:
    """订阅 task 事件，定期发心跳；终态后退出。"""
    snapshot = store.require_snapshot(task_id)
    if is_terminal(snapshot.state):
        yield format_sse(
            TaskEvent(
                name=_snapshot_event_name(snapshot),
                payload=_snapshot_to_payload(snapshot),
                terminal=True,
            )
        )
        return

    loop = asyncio.get_running_loop()
    sub = bus.subscribe(task_id, loop)
    try:
        snapshot = store.require_snapshot(task_id)
        seed = TaskEvent(
            name=_snapshot_event_name(snapshot),
            payload=_snapshot_to_payload(snapshot),
            terminal=is_terminal(snapshot.state),
        )
        yield format_sse(seed)
        if seed.terminal:
            return
        while True:
            try:
                event = await asyncio.wait_for(
                    sub.queue.get(), timeout=heartbeat_seconds
                )
            except asyncio.TimeoutError:
                heartbeat = TaskEvent(
                    name="heartbeat",
                    payload={"task_id": task_id, "server_time": _now().isoformat()},
                )
                yield format_sse(heartbeat)
                continue
            yield format_sse(event)
            if event.terminal:
                return
    finally:
        bus.unsubscribe(task_id, sub.subscriber_id)


def _snapshot_event_name(snapshot: TaskSnapshot) -> str:
    state = snapshot.state.value
    if state == "queued":
        return "queued"
    if state == "done":
        return "done"
    if state == "expired":
        return "expired"
    if state.startswith("failed-"):
        return "error"
    return "progress"


def _snapshot_to_payload(snapshot: TaskSnapshot) -> dict[str, Any]:
    """TaskSnapshot → JSON 友好 dict（snake_case）。"""
    return {
        "task_id": snapshot.task_id,
        "state": snapshot.state.value,
        "queue_position": snapshot.queue_position,
        "progress": asdict(snapshot.progress),
        "warnings": list(snapshot.warnings),
        "summary": asdict(snapshot.summary) if snapshot.summary else None,
        "error": snapshot.error,
        "created_at": _serialize_dt(snapshot.created_at),
        "expires_at": _serialize_dt(snapshot.expires_at),
        "result_available": snapshot.result_available,
    }


def _serialize_dt(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
