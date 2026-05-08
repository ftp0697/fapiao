import asyncio
import json
import time
from pathlib import Path
from typing import TextIO

from fastapi.testclient import TestClient

from fapiao_pdf.pipeline import RunStats
from fapiao_pdf.web.app import _safe_filename, create_app
from fapiao_pdf.web.config import WebConfig
from fapiao_pdf.web.errors import ApiErrorCode, TaskState
from fapiao_pdf.web.tasks import SummarySnapshot


class FakeOcrEngine:
    pass


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
    stdout.write("处理中 1/1 - a.png\n")
    output.write_bytes(b"%PDF-1.4\n%fake\n")
    return RunStats(processed=1, invoices=1, orders=0, ocr_failures=0)


def _client(tmp_path: Path, **overrides) -> TestClient:
    values = {
        "task_root": tmp_path,
        "retain_minutes": 1,
        "max_upload_mb": 1,
        "max_files": 3,
        "max_single_file_mb": 1,
        "cleanup_interval_seconds": 1,
        "upload_chunk_size": 4,
    }
    values.update(overrides)
    config = WebConfig(**values)
    app = create_app(
        config,
        run_merge=fake_run_merge,
        engine_factory=lambda: FakeOcrEngine(),
    )
    return TestClient(app)


def _wait_for_state(client: TestClient, task_id: str, state: str) -> dict[str, object]:
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        data = client.get(f"/api/tasks/{task_id}").json()
        if data["state"] == state:
            return data
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for {state}")


def _post_without_content_length(app, body: bytes, boundary: str) -> tuple[int, bytes]:
    chunks = [body[i : i + 65536] for i in range(0, len(body), 65536)]
    messages = [
        {"type": "http.request", "body": chunk, "more_body": index < len(chunks) - 1}
        for index, chunk in enumerate(chunks)
    ]
    sent: list[dict[str, object]] = []

    async def receive() -> dict[str, object]:
        if messages:
            return messages.pop(0)
        return {"type": "http.disconnect"}

    async def send(message: dict[str, object]) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/tasks",
        "raw_path": b"/api/tasks",
        "query_string": b"",
        "headers": [(b"content-type", f"multipart/form-data; boundary={boundary}".encode())],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }

    async def call() -> None:
        await app(scope, receive, send)

    asyncio.run(call())
    start = next(message for message in sent if message["type"] == "http.response.start")
    content = b"".join(
        message.get("body", b"")
        for message in sent
        if message["type"] == "http.response.body"
    )
    return int(start["status"]), content


def test_root_serves_static_index(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "fapiao" in response.text


def test_health_reports_queue_and_ocr_flags(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "ok": True,
        "version": "0.1.0",
        "ocr_cache_present": body["ocr_cache_present"],
        "engine_loaded": False,
        "queue_depth": 0,
        "ocr_broken": False,
    }
    assert isinstance(body["ocr_cache_present"], bool)


def test_upload_task_runs_to_done_and_downloads_result(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        response = client.post(
            "/api/tasks",
            data={"pdf_dpi": "200"},
            files=[("files", ("a.png", b"image", "image/png"))],
        )
        assert response.status_code == 202
        body = response.json()
        task_id = body["task_id"]
        assert body["queue_position"] == 0

        snapshot = _wait_for_state(client, task_id, TaskState.DONE.value)
        assert snapshot["summary"] == {
            "processed": 1,
            "invoices": 1,
            "orders": 0,
            "ocr_failures": 0,
        }
        result = client.get(f"/api/tasks/{task_id}/result")
        assert result.status_code == 200
        assert result.content.startswith(b"%PDF-1.4")
        disposition = result.headers["content-disposition"]
        assert "fapiao-" in disposition
        assert "filename*=" in disposition


def test_upload_rejects_invalid_requests(tmp_path: Path) -> None:
    with _client(tmp_path, max_files=1, max_upload_mb=1, max_single_file_mb=1) as client:
        assert client.post("/api/tasks", data={"pdf_dpi": "200"}).status_code == 400
        invalid_dpi = client.post(
            "/api/tasks",
            data={"pdf_dpi": "99"},
            files=[("files", ("a.png", b"x", "image/png"))],
        )
        assert invalid_dpi.status_code == 422
        assert invalid_dpi.json() == {"error": ApiErrorCode.INVALID_PDF_DPI.value}

        too_many = client.post(
            "/api/tasks",
            data={"pdf_dpi": "200"},
            files=[
                ("files", ("a.png", b"x", "image/png")),
                ("files", ("b.png", b"x", "image/png")),
            ],
        )
        assert too_many.status_code == 413
        assert too_many.json() == {"error": ApiErrorCode.TOO_MANY_FILES.value}

        unsupported = client.post(
            "/api/tasks",
            data={"pdf_dpi": "200"},
            files=[("files", ("a.txt", b"x", "text/plain"))],
        )
        assert unsupported.status_code == 400
        assert unsupported.json() == {"error": ApiErrorCode.NO_UPLOADED_FILES.value}


def test_upload_enforces_single_and_total_size_limits(tmp_path: Path) -> None:
    with _client(tmp_path, max_upload_mb=2, max_single_file_mb=1) as client:
        too_large = b"x" * (1024 * 1024 + 1)
        single = client.post(
            "/api/tasks",
            data={"pdf_dpi": "200"},
            files=[("files", ("a.png", too_large, "image/png"))],
        )
        assert single.status_code == 413
        assert single.json() == {
            "error": ApiErrorCode.SINGLE_FILE_TOO_LARGE.value
        }

    with _client(tmp_path / "total", max_upload_mb=1, max_single_file_mb=2) as client:
        half = b"x" * (600 * 1024)
        total = client.post(
            "/api/tasks",
            data={"pdf_dpi": "200"},
            files=[
                ("files", ("a.png", half, "image/png")),
                ("files", ("b.png", half, "image/png")),
            ],
        )
        assert total.status_code == 413
        assert total.json() == {"error": ApiErrorCode.UPLOAD_TOO_LARGE.value}


def test_upload_stream_limit_without_content_length_returns_413(tmp_path: Path) -> None:
    config = WebConfig(
        task_root=tmp_path,
        retain_minutes=1,
        max_upload_mb=1,
        max_files=1,
        max_single_file_mb=2,
    )
    app = create_app(
        config,
        run_merge=fake_run_merge,
        engine_factory=lambda: FakeOcrEngine(),
    )
    boundary = "boundary"
    body = b"".join(
        [
            b"--boundary\r\n",
            b'Content-Disposition: form-data; name="files"; filename="a.png"\r\n',
            b"Content-Type: image/png\r\n\r\n",
            b"x" * (1024 * 1024 + 5000),
            b"\r\n--boundary--\r\n",
        ]
    )

    status, content = _post_without_content_length(app, body, boundary)

    assert status == 413
    assert content == b'{"error":"UploadTooLarge"}'


def test_upload_without_content_length_replays_valid_body(tmp_path: Path) -> None:
    boundary = "boundary"
    body = b"".join(
        [
            b"--boundary\r\n",
            b'Content-Disposition: form-data; name="files"; filename="a.png"\r\n',
            b"Content-Type: image/png\r\n\r\n",
            b"image",
            b"\r\n--boundary--\r\n",
        ]
    )

    with _client(tmp_path) as client:
        status, content = _post_without_content_length(client.app, body, boundary)
        task_id = json.loads(content)["task_id"]
        snapshot = _wait_for_state(client, task_id, TaskState.DONE.value)

    assert status == 202
    assert snapshot["state"] == TaskState.DONE.value


def test_upload_skips_unsupported_files_when_supported_remain(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        response = client.post(
            "/api/tasks",
            data={"pdf_dpi": "200"},
            files=[
                ("files", ("a.txt", b"ignored", "text/plain")),
                ("files", ("b.PNG", b"image", "image/png")),
            ],
        )

        assert response.status_code == 202
        task_id = response.json()["task_id"]
        snapshot = _wait_for_state(client, task_id, TaskState.DONE.value)
        assert snapshot["state"] == TaskState.DONE.value


def test_status_result_delete_and_events_state_branches(tmp_path: Path) -> None:
    with _client(tmp_path, max_sse_subscribers_per_task=1) as client:
        services = client.app.state.web_services
        queued = services.store.create_task(pdf_dpi=200)
        expired = services.store.create_task(pdf_dpi=200)
        running = services.store.create_task(pdf_dpi=200)
        done = services.store.create_task(pdf_dpi=200)
        services.store.set_running(running.task_id)
        done.output_path.write_bytes(b"%PDF-1.4\n")
        services.store.set_done(
            done.task_id,
            SummarySnapshot(processed=1, invoices=1, orders=0, ocr_failures=0),
            retain_minutes=1,
        )
        services.store.mark_expired(expired.task_id, reason="test")

        missing = client.get(f"/api/tasks/{'0' * 32}")
        assert missing.status_code == 404
        expired_events = client.get(f"/api/tasks/{expired.task_id}/events")
        assert expired_events.status_code == 410
        assert expired_events.json() == {"error": ApiErrorCode.TASK_EXPIRED.value}
        not_ready = client.get(f"/api/tasks/{running.task_id}/result")
        assert not_ready.status_code == 409
        delete_running = client.delete(f"/api/tasks/{running.task_id}")
        assert delete_running.status_code == 409

        with client.stream("GET", f"/api/tasks/{done.task_id}/events") as stream:
            text = "".join(stream.iter_text())
        assert "event: done" in text
        assert f'"task_id": "{done.task_id}"' in text

        subscriber = services.bus.subscribe(running.task_id, services.loop)
        try:
            too_many_streams = client.get(f"/api/tasks/{running.task_id}/events")
        finally:
            services.bus.unsubscribe(running.task_id, subscriber.subscriber_id)
        assert too_many_streams.status_code == 429
        assert too_many_streams.json() == {"error": ApiErrorCode.TOO_MANY_STREAMS.value}

        delete_queued = client.delete(f"/api/tasks/{queued.task_id}")
        assert delete_queued.status_code == 204
        assert client.get(f"/api/tasks/{queued.task_id}").status_code == 404

        delete_done = client.delete(f"/api/tasks/{done.task_id}")
        assert delete_done.status_code == 204
        assert client.get(f"/api/tasks/{done.task_id}").status_code == 404


def test_upload_rejects_when_ocr_broken_latch_is_set(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        client.app.state.web_services.executor._ocr_broken_flag.set()
        response = client.post(
            "/api/tasks",
            data={"pdf_dpi": "200"},
            files=[("files", ("a.png", b"image", "image/png"))],
        )

    assert response.status_code == 503
    assert response.json() == {"error": ApiErrorCode.OCR_MODEL_MISSING.value}


def test_safe_filename_strips_paths_and_renames_collisions() -> None:
    used = {"a.png"}

    assert _safe_filename("../a.png", used) == "a (1).png"
    used.add("a (1).png")
    assert _safe_filename("..\\a.png", used) == "a (2).png"
    assert _safe_filename("", used) == "upload"
