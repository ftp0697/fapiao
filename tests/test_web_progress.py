import asyncio
import json
from pathlib import Path

import pytest

from fapiao_pdf.web.errors import TaskState, TooManyStreamsError
from fapiao_pdf.web.progress import (
    EventBus,
    PROGRESS_RE,
    PipelineTextCapture,
    TaskEvent,
    _deliver,
    format_sse,
    parse_progress_line,
    stream_task_events,
)
from fapiao_pdf.web.tasks import SummarySnapshot, TaskStore


def _sse_payload(chunk: str) -> dict[str, object]:
    return json.loads(chunk.split("data: ", 1)[1].rstrip("\n"))


def test_progress_re_matches_canonical_line() -> None:
    match = PROGRESS_RE.match("处理中 12/47 - 发票A.pdf")
    assert match is not None
    assert match.group("current") == "12"
    assert match.group("total") == "47"
    assert match.group("key") == "发票A.pdf"


def test_parse_progress_line_returns_tuple() -> None:
    assert parse_progress_line("处理中 1/3 - x.png") == (1, 3, "x.png")


def test_parse_progress_line_strips_trailing_newline() -> None:
    assert parse_progress_line("处理中 1/3 - x.png\n") == (1, 3, "x.png")


@pytest.mark.parametrize(
    "line",
    [
        "",
        "完成",
        "处理中 invalid - x",
        "其他 1/3 - x",
        "处理中 1/3-x",
    ],
)
def test_parse_progress_line_misses(line: str) -> None:
    assert parse_progress_line(line) is None


def test_pipeline_text_capture_splits_on_newline() -> None:
    captured: list[str] = []
    cap = PipelineTextCapture(captured.append)
    cap.write("第一行\n第二行\n")
    assert captured == ["第一行", "第二行"]


def test_pipeline_text_capture_splits_on_carriage_return() -> None:
    captured: list[str] = []
    cap = PipelineTextCapture(captured.append)
    cap.write("a\rb\rc\r")
    assert captured == ["a", "b", "c"]


def test_pipeline_text_capture_buffers_partial() -> None:
    captured: list[str] = []
    cap = PipelineTextCapture(captured.append)
    cap.write("part")
    assert captured == []
    cap.write("ial\nfinal")
    assert captured == ["partial"]
    cap.flush()
    assert captured == ["partial", "final"]


def test_pipeline_text_capture_isatty_false() -> None:
    cap = PipelineTextCapture(lambda _line: None)
    assert cap.writable() is True
    assert cap.isatty() is False


def test_pipeline_text_capture_skips_empty_lines() -> None:
    captured: list[str] = []
    cap = PipelineTextCapture(captured.append)
    cap.write("\n\nreal\n\n")
    assert captured == ["real"]


def test_format_sse_shape() -> None:
    event = TaskEvent(name="progress", payload={"k": "v"})
    out = format_sse(event)
    assert out.startswith("event: progress\n")
    assert "data: " in out
    assert out.endswith("\n\n")
    parsed = json.loads(out.split("data: ", 1)[1].rstrip("\n"))
    assert parsed == {"k": "v"}


def test_format_sse_unicode_preserved() -> None:
    event = TaskEvent(name="warning", payload={"message": "切分回退"})
    out = format_sse(event)
    assert "切分回退" in out


def test_event_bus_subscribe_unsubscribe(tmp_path: Path) -> None:
    bus = EventBus(max_subscribers_per_task=2)
    loop = asyncio.new_event_loop()
    try:
        sub_a = bus.subscribe("t1", loop)
        sub_b = bus.subscribe("t1", loop)
        assert bus.subscriber_count("t1") == 2
        bus.unsubscribe("t1", sub_a.subscriber_id)
        assert bus.subscriber_count("t1") == 1
        bus.unsubscribe("t1", sub_b.subscriber_id)
        assert bus.subscriber_count("t1") == 0
    finally:
        loop.close()


def test_event_bus_rejects_over_capacity() -> None:
    bus = EventBus(max_subscribers_per_task=1)
    loop = asyncio.new_event_loop()
    try:
        bus.subscribe("t1", loop)
        with pytest.raises(TooManyStreamsError):
            bus.subscribe("t1", loop)
    finally:
        loop.close()


def test_event_bus_unsubscribe_unknown_is_idempotent() -> None:
    bus = EventBus()
    bus.unsubscribe("missing", "absent")  # 不抛


def test_deliver_drops_oldest_non_terminal_when_full() -> None:
    bus = EventBus(subscriber_queue_maxsize=2)
    loop = asyncio.new_event_loop()
    try:
        sub = bus.subscribe("t", loop)
        sub.queue.put_nowait(TaskEvent("a", {"i": 1}))
        sub.queue.put_nowait(TaskEvent("b", {"i": 2}))
        _deliver(sub, TaskEvent("c", {"i": 3}))
        items: list[TaskEvent] = []
        while not sub.queue.empty():
            items.append(sub.queue.get_nowait())
        names = [e.name for e in items]
        assert "c" in names
        assert "a" not in names  # 最旧被丢
    finally:
        loop.close()


def test_deliver_drops_only_oldest_non_terminal_when_full() -> None:
    bus = EventBus(subscriber_queue_maxsize=3)
    loop = asyncio.new_event_loop()
    try:
        sub = bus.subscribe("t", loop)
        sub.queue.put_nowait(TaskEvent("done", {"i": 1}, terminal=True))
        sub.queue.put_nowait(TaskEvent("a", {"i": 2}))
        sub.queue.put_nowait(TaskEvent("b", {"i": 3}))
        _deliver(sub, TaskEvent("c", {"i": 4}))
        names: list[str] = []
        while not sub.queue.empty():
            names.append(sub.queue.get_nowait().name)
        assert names == ["done", "b", "c"]
    finally:
        loop.close()


def test_deliver_clears_full_terminal_queue_for_latest_terminal() -> None:
    bus = EventBus(subscriber_queue_maxsize=2)
    loop = asyncio.new_event_loop()
    try:
        sub = bus.subscribe("t", loop)
        sub.queue.put_nowait(TaskEvent("error", {"i": 1}, terminal=True))
        sub.queue.put_nowait(TaskEvent("expired", {"i": 2}, terminal=True))
        _deliver(sub, TaskEvent("done", {"i": 3}, terminal=True))
        assert sub.queue.qsize() == 1
        event = sub.queue.get_nowait()
        assert event.name == "done"
        assert event.terminal is True
    finally:
        loop.close()


def test_deliver_preserves_terminal_events_under_pressure() -> None:
    bus = EventBus(subscriber_queue_maxsize=2)
    loop = asyncio.new_event_loop()
    try:
        sub = bus.subscribe("t", loop)
        sub.queue.put_nowait(TaskEvent("done", {"i": 1}, terminal=True))
        sub.queue.put_nowait(TaskEvent("warning", {"i": 2}))
        _deliver(sub, TaskEvent("progress", {"i": 3}))
        names: list[str] = []
        while not sub.queue.empty():
            names.append(sub.queue.get_nowait().name)
        assert "done" in names
    finally:
        loop.close()


def test_publish_snapshot_dispatches_to_all_subscribers(tmp_path: Path) -> None:
    bus = EventBus()
    store = TaskStore(tmp_path)
    record = store.create_task(pdf_dpi=200)
    snapshot = store.require_snapshot(record.task_id)

    async def runner() -> tuple[TaskEvent, TaskEvent]:
        loop = asyncio.get_running_loop()
        sub_a = bus.subscribe(record.task_id, loop)
        sub_b = bus.subscribe(record.task_id, loop)
        bus.publish_snapshot(record.task_id, snapshot, event_name="queued")
        await asyncio.sleep(0)
        ea = await asyncio.wait_for(sub_a.queue.get(), timeout=1.0)
        eb = await asyncio.wait_for(sub_b.queue.get(), timeout=1.0)
        return ea, eb

    a, b = asyncio.run(runner())
    assert a.name == "queued" and b.name == "queued"
    assert a.payload["task_id"] == record.task_id


def test_publish_snapshot_merges_warning_message(tmp_path: Path) -> None:
    bus = EventBus()
    store = TaskStore(tmp_path)
    record = store.create_task(pdf_dpi=200)
    store.set_running(record.task_id)
    store.append_warning(record.task_id, "切分回退")
    snapshot = store.require_snapshot(record.task_id)

    async def runner() -> TaskEvent:
        loop = asyncio.get_running_loop()
        sub = bus.subscribe(record.task_id, loop)
        bus.publish_snapshot(
            record.task_id,
            snapshot,
            event_name="warning",
            extra={"message": "切分回退"},
        )
        await asyncio.sleep(0)
        return await asyncio.wait_for(sub.queue.get(), timeout=1.0)

    event = asyncio.run(runner())
    assert event.name == "warning"
    assert event.payload["task_id"] == record.task_id
    assert event.payload["warnings"] == ["切分回退"]
    assert event.payload["message"] == "切分回退"


def test_stream_task_events_yields_seed_and_heartbeat(tmp_path: Path) -> None:
    bus = EventBus()
    store = TaskStore(tmp_path)
    record = store.create_task(pdf_dpi=200)

    async def runner() -> list[str]:
        chunks: list[str] = []
        agen = stream_task_events(
            record.task_id, store=store, bus=bus, heartbeat_seconds=0.05
        )
        chunks.append(await agen.__anext__())  # seed
        chunks.append(await agen.__anext__())  # heartbeat after timeout
        await agen.aclose()
        return chunks

    chunks = asyncio.run(runner())
    assert chunks[0].startswith("event: queued\n")
    assert chunks[1].startswith("event: heartbeat\n")


def test_stream_task_events_uses_progress_seed_for_running(tmp_path: Path) -> None:
    bus = EventBus()
    store = TaskStore(tmp_path)
    record = store.create_task(pdf_dpi=200)
    store.set_running(record.task_id)
    store.set_progress(record.task_id, current=1, total=3, key="a.png")

    async def runner() -> str:
        agen = stream_task_events(
            record.task_id, store=store, bus=bus, heartbeat_seconds=5.0
        )
        try:
            return await agen.__anext__()
        finally:
            await agen.aclose()

    chunk = asyncio.run(runner())
    assert chunk.startswith("event: progress\n")
    payload = _sse_payload(chunk)
    assert payload["state"] == "running"
    assert payload["progress"] == {"current": 1, "total": 3, "key": "a.png"}


def test_stream_task_events_closes_after_terminal(tmp_path: Path) -> None:
    bus = EventBus()
    store = TaskStore(tmp_path)
    record = store.create_task(pdf_dpi=200)

    async def runner() -> list[str]:
        chunks: list[str] = []
        agen = stream_task_events(
            record.task_id, store=store, bus=bus, heartbeat_seconds=5.0
        )
        chunks.append(await agen.__anext__())  # seed
        loop = asyncio.get_running_loop()

        def emit() -> None:
            store.set_running(record.task_id)
            store.set_done(
                record.task_id,
                SummarySnapshot(0, 0, 0, 0),
                retain_minutes=60,
            )
            bus.publish_snapshot(
                record.task_id,
                store.require_snapshot(record.task_id),
                event_name="done",
                terminal=True,
            )

        loop.call_soon(emit)
        chunks.append(await agen.__anext__())  # done
        with pytest.raises(StopAsyncIteration):
            await agen.__anext__()
        return chunks

    chunks = asyncio.run(runner())
    assert chunks[0].startswith("event: queued\n")
    assert chunks[1].startswith("event: done\n")


def test_stream_task_events_late_done_yields_once_without_subscribing(
    tmp_path: Path,
) -> None:
    bus = EventBus()
    store = TaskStore(tmp_path)
    record = store.create_task(pdf_dpi=200)
    store.set_running(record.task_id)
    store.set_done(record.task_id, SummarySnapshot(0, 0, 0, 0), retain_minutes=60)

    async def runner() -> tuple[str, int]:
        agen = stream_task_events(
            record.task_id, store=store, bus=bus, heartbeat_seconds=5.0
        )
        chunk = await agen.__anext__()
        with pytest.raises(StopAsyncIteration):
            await agen.__anext__()
        return chunk, bus.subscriber_count(record.task_id)

    chunk, subscriber_count = asyncio.run(runner())
    assert chunk.startswith("event: done\n")
    assert _sse_payload(chunk)["state"] == "done"
    assert subscriber_count == 0


def test_stream_task_events_late_failure_uses_error_event(tmp_path: Path) -> None:
    bus = EventBus()
    store = TaskStore(tmp_path)
    record = store.create_task(pdf_dpi=200)
    store.set_running(record.task_id)
    store.set_failed(
        record.task_id,
        TaskState.FAILED_INTERNAL,
        retain_minutes=60,
    )

    async def runner() -> str:
        agen = stream_task_events(
            record.task_id, store=store, bus=bus, heartbeat_seconds=5.0
        )
        chunk = await agen.__anext__()
        with pytest.raises(StopAsyncIteration):
            await agen.__anext__()
        return chunk

    chunk = asyncio.run(runner())
    assert chunk.startswith("event: error\n")
    assert _sse_payload(chunk)["state"] == "failed-internal"


def test_subscribe_after_unsubscribe_does_not_leak(tmp_path: Path) -> None:
    bus = EventBus()
    loop = asyncio.new_event_loop()
    try:
        sub = bus.subscribe("t", loop)
        bus.unsubscribe("t", sub.subscriber_id)
        # 内部 dict 应清空
        assert "t" not in bus._subs
    finally:
        loop.close()
