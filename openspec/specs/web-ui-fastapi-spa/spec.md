## ADDED Requirements

### Requirement: Web service entry point
The system SHALL provide a `fapiao serve` subcommand that starts a single-process FastAPI + Uvicorn web service serving both the HTTP API and the single-page frontend.

#### Scenario: Default local-only bind
- **WHEN** the user runs `fapiao serve` without overrides
- **THEN** the system SHALL bind to `127.0.0.1:8000` with exactly one Uvicorn worker

#### Scenario: Custom host and port
- **WHEN** the user provides `--host` and `--port`
- **THEN** the system SHALL bind to the requested address using one Uvicorn worker

#### Scenario: OCR model missing at startup
- **WHEN** `fapiao serve` cannot satisfy `pipeline.ensure_ocr_ready(allow_download=False)`
- **THEN** the system SHALL print a Chinese actionable message and exit with code `2`

#### Scenario: Lazy model loading
- **WHEN** the service starts successfully
- **THEN** the system SHALL NOT load the PaddleOCR model into memory until the first task begins processing

#### Scenario: Public host warning
- **WHEN** the user passes `--host 0.0.0.0` or any non-loopback address
- **THEN** the system SHALL print a Chinese security advisory before listening

### Requirement: Task upload endpoint
The system SHALL accept multipart task submissions and persist them to per-task isolated work directories before queuing.

#### Scenario: Successful upload
- **WHEN** a client `POST /api/tasks` with one or more `.jpg/.jpeg/.png/.pdf` files (case insensitive)
- **THEN** the system SHALL respond `202` with a JSON body `{"task_id": "<uuid4>", "queue_position": <int>}`

#### Scenario: Unsupported extensions
- **WHEN** the upload contains files outside the supported extension list
- **THEN** the system SHALL silently skip those files and proceed if at least one supported file remains

#### Scenario: All-empty upload
- **WHEN** every uploaded file is unsupported or the request has no files
- **THEN** the system SHALL respond `400` with `{"error": "NoUploadedFiles"}`

#### Scenario: Total size over limit
- **WHEN** the total upload size exceeds `--max-upload-mb`
- **THEN** the system SHALL respond `413` with `{"error": "UploadTooLarge"}`

#### Scenario: File count over limit
- **WHEN** the upload contains more than 200 files
- **THEN** the system SHALL respond `413` with `{"error": "TooManyFiles"}`

#### Scenario: Custom pdf_dpi
- **WHEN** the client provides `pdf_dpi` form field within `100..300`
- **THEN** the system SHALL forward it to `pipeline.run_merge` for that task

#### Scenario: Invalid pdf_dpi
- **WHEN** `pdf_dpi` is outside `100..300`
- **THEN** the system SHALL respond `422` with `{"error": "InvalidPdfDpi"}`

#### Scenario: Single file size over limit
- **WHEN** any individual uploaded file exceeds `--max-single-file-mb` (default 50)
- **THEN** the system SHALL respond `413` with `{"error": "SingleFileTooLarge"}`

#### Scenario: Disk full during upload
- **WHEN** writing any uploaded part raises `OSError` with `errno == ENOSPC`
- **THEN** the system SHALL delete the partial task directory and respond `507` with `{"error": "InsufficientStorage"}`

#### Scenario: Upload while OCR broken
- **WHEN** a previous task has set the executor `ocr_broken` flag
- **THEN** the system SHALL respond `503` with `{"error": "OcrModelMissing"}` without creating a new task record

#### Scenario: Path isolation
- **WHEN** a task is created with id `T`
- **THEN** the system SHALL place inputs under `<tmpdir>/fapiao-tasks/T/input/` with collision-safe rename, never exposing `.part` filenames to the scanner

### Requirement: Task status endpoint
The system SHALL expose a state snapshot for any active or recently completed task.

#### Scenario: Existing task lookup
- **WHEN** a client `GET /api/tasks/{task_id}` for a known task
- **THEN** the system SHALL respond `200` with `state`, `queue_position`, `progress`, `warnings`, `summary`, `created_at`, `expires_at`, and `result_available`

#### Scenario: Unknown task
- **WHEN** the requested `task_id` was never created
- **THEN** the system SHALL respond `404` with `{"error": "TaskNotFound"}`

#### Scenario: Cleaned-up task
- **WHEN** the requested `task_id` existed but was deleted by retention sweep
- **THEN** the system SHALL respond `404` with `{"error": "TaskNotFound"}` while still distinguishing internally via the expired placeholder list

### Requirement: Server-sent events progress stream
The system SHALL push live progress events for a task as a `text/event-stream` response.

#### Scenario: Subscribe to running task
- **WHEN** a client `GET /api/tasks/{task_id}/events` for a running or queued task
- **THEN** the system SHALL emit a seed `queued` or `progress` event reflecting the current snapshot, then live `progress` / `warning` events as the pipeline produces them

#### Scenario: Event payload structure
- **WHEN** the system emits any non-heartbeat event
- **THEN** the JSON payload SHALL contain the full task snapshot (`task_id`, `state`, `queue_position`, `progress`, `warnings`, `summary`, `error`, `created_at`, `expires_at`, `result_available`); `warning` events SHALL additionally include `message` (the latest warning text)

#### Scenario: Heartbeat
- **WHEN** a stream is open without recent events
- **THEN** the system SHALL emit a `heartbeat` event with `{"task_id": "...", "server_time": "<ISO8601>"}` at most every 15 seconds

#### Scenario: Terminal event closes stream
- **WHEN** a task reaches `done`, `failed-*`, or `expired`
- **THEN** the system SHALL emit a final `done`, `error`, or `expired` event and close the stream

#### Scenario: Late subscriber
- **WHEN** a client subscribes after the task already finished
- **THEN** the system SHALL emit one terminal event reflecting the final state and close immediately, without registering a subscriber

#### Scenario: Subscriber concurrency limit
- **WHEN** a task already has 16 active SSE subscribers
- **THEN** the system SHALL respond `429` with `{"error": "TooManyStreams"}` to additional subscriptions

#### Scenario: Slow subscriber backpressure
- **WHEN** a subscriber's queue (maxsize 32) is full and a new non-terminal event arrives
- **THEN** the system SHALL drop the oldest non-terminal event in that queue; terminal events SHALL never be dropped

### Requirement: Result download endpoint
The system SHALL deliver the merged PDF for completed tasks until retention expires.

#### Scenario: Download after success
- **WHEN** a client `GET /api/tasks/{task_id}/result` for a `done` task
- **THEN** the system SHALL respond `200` with `Content-Type: application/pdf` and `Content-Disposition: attachment; filename="fapiao-<task_id[:8]>.pdf"; filename*=UTF-8''fapiao-<task_id[:8]>.pdf`

#### Scenario: Download before completion
- **WHEN** the task is `queued` or `running`
- **THEN** the system SHALL respond `409` with `{"error": "TaskNotReady"}`

#### Scenario: Download after expiration
- **WHEN** the task expired and was cleaned up but the placeholder is still active
- **THEN** the system SHALL respond `410` with `{"error": "TaskExpired"}`

#### Scenario: Download activity tracking
- **WHEN** a download starts
- **THEN** the system SHALL increment `active_downloads` and update `last_download_at` before streaming, and decrement `active_downloads` via a background task after the response completes

### Requirement: Task deletion endpoint
The system SHALL allow clients to release task resources before retention expires.

#### Scenario: Delete completed task
- **WHEN** a client `DELETE /api/tasks/{task_id}` for a `done` or `failed-*` task
- **THEN** the system SHALL remove the task directory and metadata, then respond `204`

#### Scenario: Delete queued task
- **WHEN** a client `DELETE /api/tasks/{task_id}` for a `queued` task
- **THEN** the system SHALL mark the record `deleted` so the worker skips it on dequeue, remove the task directory, and respond `204`

#### Scenario: Delete running task
- **WHEN** a client `DELETE /api/tasks/{task_id}` while the task is `running`
- **THEN** the system SHALL respond `409` with `{"error": "TaskRunning"}`

#### Scenario: Delete unknown or expired task
- **WHEN** the requested task does not exist or has been completely purged from the placeholder
- **THEN** the system SHALL respond `404` with `{"error": "TaskNotFound"}`

### Requirement: Health and version endpoint
The system SHALL expose a health probe reporting service readiness.

#### Scenario: Health snapshot
- **WHEN** a client `GET /api/health`
- **THEN** the system SHALL respond `200` with `{"ok": true, "version": "<__version__>", "ocr_cache_present": <bool>, "engine_loaded": <bool>, "queue_depth": <int>, "ocr_broken": <bool>}`

### Requirement: Single-instance global task queue
The system SHALL serialize task execution to honor the single shared OCR engine assumption.

#### Scenario: Concurrent submissions
- **WHEN** two `POST /api/tasks` arrive simultaneously
- **THEN** the system SHALL process them strictly in submission order

#### Scenario: Single active task
- **WHEN** any moment in time
- **THEN** the system SHALL have at most one task in `running` state

#### Scenario: Engine reuse
- **WHEN** a task starts running
- **THEN** the system SHALL inject the singleton `OcrEngine` into `pipeline.run_merge` instead of creating a new instance

#### Scenario: Graceful shutdown grace period
- **WHEN** the service receives SIGINT or SIGTERM
- **THEN** the system SHALL stop accepting new tasks, wait at most 30 seconds for the current `running` task to complete, and then exit; in-flight tasks not finished within the grace period SHALL be reconciled to `failed-restart` on the next startup

#### Scenario: OCR broken latch
- **WHEN** the worker observes `OcrModelMissingError` during task execution
- **THEN** the system SHALL set an `ocr_broken` flag and reject all subsequent `POST /api/tasks` with `503 OcrModelMissing` until the process restarts

### Requirement: Progress capture from pipeline streams
The system SHALL capture progress and warnings from `pipeline.run_merge` without modifying its public API.

#### Scenario: Progress line parsing
- **WHEN** the pipeline writes `处理中 N/M - <key>` to its injected stdout
- **THEN** the system SHALL update the task `progress` snapshot to `{"current": N, "total": M, "key": "<key>"}`

#### Scenario: Warning capture
- **WHEN** the pipeline writes a Chinese warning line to its injected stderr
- **THEN** the system SHALL append the line to the task `warnings` array

#### Scenario: Summary capture
- **WHEN** `pipeline.run_merge` returns `RunStats`
- **THEN** the system SHALL store `processed`, `invoices`, `orders`, `ocr_failures` into the task `summary` field

### Requirement: Pipeline error mapping
The system SHALL translate pipeline domain exceptions into deterministic task states.

#### Scenario: No processable input
- **WHEN** `pipeline.run_merge` raises `NoProcessableInputError`
- **THEN** the system SHALL set task state to `failed-no-input`

#### Scenario: OCR model missing
- **WHEN** the pipeline raises `OcrModelMissingError`
- **THEN** the system SHALL set task state to `failed-ocr-missing` and respond `503` to subsequent uploads until restart

#### Scenario: Fatal run error
- **WHEN** the pipeline raises `FatalRunError`
- **THEN** the system SHALL set task state to `failed-fatal` and store the message in the task error field

#### Scenario: Unexpected exception
- **WHEN** the pipeline raises any other exception
- **THEN** the system SHALL set task state to `failed-internal` and store a generic message without stack trace

### Requirement: Retention and cleanup
The system SHALL delete task artifacts after a configurable retention window measured from task completion.

#### Scenario: Default retention
- **WHEN** the user does not pass `--retain-minutes`
- **THEN** the system SHALL retain task artifacts for `60` minutes after the task reaches a terminal state

#### Scenario: Active tasks never expire
- **WHEN** a task is in `queued` or `running` state
- **THEN** the system SHALL NOT delete its artifacts regardless of `--retain-minutes`

#### Scenario: Periodic sweep
- **WHEN** the cleanup thread runs every five minutes
- **THEN** the system SHALL delete tasks whose `state ∈ terminal` AND `completed_at + retain_minutes < now` AND `active_downloads == 0` AND (`last_download_at is None` OR `now - last_download_at >= 60s`)

#### Scenario: Startup sweep
- **WHEN** the service starts
- **THEN** the system SHALL first run `TaskStore.load_from_disk` to recover terminal records, then remove every orphan directory under `<tmpdir>/fapiao-tasks/` not covered by a known record

#### Scenario: Restart-mid-run reconciliation
- **WHEN** a task metadata file shows state `queued` or `running` at startup
- **THEN** the system SHALL set the task state to `failed-restart`, register a 24-hour expired placeholder, and remove its work directory

#### Scenario: Corrupt metadata
- **WHEN** a task `task.json` cannot be parsed at startup
- **THEN** the system SHALL register an expired placeholder with `reason="corrupt-startup"`, remove the directory, and continue startup

#### Scenario: Expired placeholder
- **WHEN** a task has just been deleted by retention sweep
- **THEN** the system SHALL keep an in-memory placeholder for 24 hours (max 1024 entries, LRU eviction) so subsequent `GET /result` and `GET /events` return `410 TaskExpired`, while `GET /tasks/{id}` returns `404 TaskNotFound`

### Requirement: Single-page frontend
The system SHALL serve a self-contained dark-themed single-page frontend that drives the entire workflow.

#### Scenario: Index page
- **WHEN** a browser navigates to `/`
- **THEN** the system SHALL respond `200` with `index.html` containing dark tech-aesthetic styling, drag-drop upload, progress UI, and a download button placeholder

#### Scenario: Static assets
- **WHEN** the browser requests assets under `/static/`
- **THEN** the system SHALL serve them via `StaticFiles` from `src/fapiao_pdf/web/static`

#### Scenario: Upload interaction
- **WHEN** the user drops or picks supported files in the page
- **THEN** the page SHALL display a list with name, size, extension, total count, and total size before submitting

#### Scenario: Live progress
- **WHEN** an upload is accepted
- **THEN** the page SHALL open the SSE stream and display queue position, current progress bar, current file key, and accumulated warnings in Chinese

#### Scenario: Final summary and download
- **WHEN** the task reaches `done`
- **THEN** the page SHALL show `共处理 N 张，发票 X，订单 Y，OCR 失败 Z` and reveal a download button pointing at `/api/tasks/{id}/result`

#### Scenario: Resumable session
- **WHEN** the page is loaded with `?task=<id>` in the URL
- **THEN** the page SHALL skip the upload widget and resume polling/SSE for that task

#### Scenario: Failure rendering
- **WHEN** the task ends in any `failed-*` state
- **THEN** the page SHALL show the corresponding Chinese message and offer a retry button that returns to the upload screen

#### Scenario: Offline-friendly assets
- **WHEN** the deployment is air-gapped
- **THEN** the page SHALL load successfully without contacting any external CDN or font service

### Requirement: Privacy parity with CLI
The system SHALL preserve the existing privacy guarantees of the CLI tool.

#### Scenario: No OCR text in logs
- **WHEN** the service writes any log line for a task
- **THEN** the system SHALL NOT include OCR text content, monetary amounts, tax numbers, or personal identifiers

#### Scenario: No external network during merge
- **WHEN** a task is processing
- **THEN** the system SHALL NOT issue any outbound network call beyond what `pipeline.run_merge` already performs (which is none)

### Requirement: CLI non-regression
The system SHALL preserve the existing CLI behavior and signatures.

#### Scenario: Existing merge command unchanged
- **WHEN** the user runs `fapiao merge` after this change
- **THEN** the system SHALL produce byte-equivalent results to the prior release for the same input set, ignoring PDF metadata timestamps

#### Scenario: Existing exit codes unchanged
- **WHEN** any prior `fapiao merge` / `fapiao init` exit code path is exercised
- **THEN** the system SHALL return the same exit code as before
