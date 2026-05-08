# Tasks — `add-web-ui`

> 实施 `/ccg:spec-impl` 时按序执行；每条 ≤ 2 小时工作量；每条都可独立测试。
> 引用文件均使用相对路径；`design.md` §X 指本变更同目录的 `design.md`。

## 1. 项目骨架与依赖

- [x] 1.1 在 `pyproject.toml` 增加 `[project.optional-dependencies].web = ["fastapi>=0.115,<1", "uvicorn>=0.30,<1", "python-multipart>=0.0.9,<1"]`
- [x] 1.2 在 `pyproject.toml` 增加 `[project.scripts]` 条目：`fapiao = "fapiao_pdf.cli:app"` 已存在；新增 console-script 不需要，复用 `fapiao serve` 子命令
- [x] 1.3 在 `pyproject.toml` 的 `[project.optional-dependencies].dev` 增加 `httpx>=0.27,<1`（FastAPI TestClient 异步路径）
- [x] 1.4 创建 `src/fapiao_pdf/web/__init__.py`（空文件，仅声明子包）
- [x] 1.5 创建 `src/fapiao_pdf/web/static/` 目录并放入空 `.gitkeep`

## 2. 配置层（`web/config.py`）

- [x] 2.1 实现 `WebConfig` dataclass，字段：`host`, `port`, `retain_minutes`, `max_upload_mb`, `max_files`, `max_single_file_mb`, `heartbeat_seconds`, `cleanup_interval_seconds`, `download_grace_seconds`, `expired_placeholder_hours`, `expired_placeholder_max`, `upload_chunk_size`, `task_root`, `max_sse_subscribers_per_task`, `subscriber_queue_maxsize`
- [x] 2.2 默认值常量：`DEFAULT_RETAIN_MINUTES=60`, `DEFAULT_MAX_UPLOAD_MB=200`, `DEFAULT_MAX_FILES=200`, `DEFAULT_MAX_SINGLE_FILE_MB=50`, `DEFAULT_HEARTBEAT_SECONDS=15`, `DEFAULT_CLEANUP_INTERVAL_SECONDS=300`, `DEFAULT_DOWNLOAD_GRACE_SECONDS=60`, `DEFAULT_EXPIRED_PLACEHOLDER_HOURS=24`, `DEFAULT_EXPIRED_PLACEHOLDER_MAX=1024`, `DEFAULT_UPLOAD_CHUNK_SIZE=1<<20`, `DEFAULT_MAX_SSE_PER_TASK=16`, `DEFAULT_SUBSCRIBER_QUEUE_MAXSIZE=32`
- [x] 2.3 实现 `resolve_task_root() -> Path`，返回 `Path(tempfile.gettempdir()) / "fapiao-tasks"`，若不存在则 `mkdir(parents=True, exist_ok=True)`
- [x] 2.4 实现 `validate_web_config(config) -> WebConfig`，校验数值范围（端口 1..65535、retain ≥ 1、配额 ≥ 1）；越界 `raise ValueError`
- [x] 2.5 单元测试 `tests/test_web_config.py`：默认值、边界、校验失败

## 3. 错误与状态枚举（`web/errors.py`）

- [x] 3.1 定义 `class TaskState(StrEnum)`：值集见 design.md §2 状态机
- [x] 3.2 定义 `class ApiErrorCode(StrEnum)`：值集见 design.md §5.2
- [x] 3.3 定义异常层级：`WebError` 基类 + `UploadTooLargeError`, `TooManyFilesError`, `SingleFileTooLargeError`, `InvalidPdfDpiError`, `NoUploadedFilesError`, `TaskNotFoundError`, `TaskExpiredError`, `TaskNotReadyError`, `TaskRunningError`, `OcrBrokenError`, `TooManyStreamsError`, `InsufficientStorageError`
- [x] 3.4 实现 `map_pipeline_exception(exc) -> tuple[TaskState, str | None]`，按顺序匹配 `NoProcessableInputError`, `OcrModelMissingError`, `FatalRunError`, `Exception`
- [x] 3.5 实现 `to_http_exception(error: WebError) -> HTTPException`，字典映射 ApiErrorCode → status_code
- [x] 3.6 单元测试 `tests/test_web_errors.py`：异常映射穷举、状态码正确性

## 4. 任务模型与持久化（`web/tasks.py`）

- [x] 4.1 定义 `ProgressSnapshot` / `SummarySnapshot` / `TaskRecord` / `TaskSnapshot` / `ExpiredPlaceholder` 数据类
- [x] 4.2 实现 `TaskRecord.to_json() -> dict` / `from_json(data) -> TaskRecord`，包含 `schema_version=1` 字段
- [x] 4.3 实现 `TaskStore` 类，内部用 `threading.RLock` + `dict[str, TaskRecord]` + `OrderedDict[str, ExpiredPlaceholder]`
- [x] 4.4 `TaskStore.create_task(...)` 分配 `task_id=uuid4().hex`，返回 `TaskRecord(state=queued, queue_seq=next_seq)`
- [x] 4.5 `TaskStore` 状态变换方法：`set_running`, `set_progress`, `append_warning`, `set_done`, `set_failed`，每次都更新 `updated_at` 并触发 `_persist(task_id)`
- [x] 4.6 `set_done` 与 `set_failed-*`：设置 `completed_at` 与 `expires_at = completed_at + retain_minutes`
- [x] 4.7 `mark_expired(task_id, reason)`：从 active dict 移除 → 加入 placeholder OrderedDict（FIFO 驱逐到 max=1024）
- [x] 4.8 `note_download_started/finished`：原子递增/递减 `active_downloads`，更新 `last_download_at`
- [x] 4.9 `queue_position(task_id)`：扫描 active dict 中 `state == queued` 且 `queue_seq < self.queue_seq` 的数量；running=0；terminal=null
- [x] 4.10 `_persist(task_id)`：写 `<task_dir>/task.json.tmp` → `os.replace` 到 `task.json`
- [x] 4.11 `load_from_disk()`：扫描 `task_root/*/task.json`；按 design.md §2 重启恢复规则处理
- [x] 4.12 `delete_task(task_id)`：从 active dict 移除 + `shutil.rmtree(task_dir, ignore_errors=False)`
- [x] 4.13 `to_snapshot(task_id)` 与 `require_snapshot(task_id)`，后者未找到 raise `TaskNotFoundError`
- [x] 4.14 单元测试 `tests/test_web_tasks.py`：状态机遍历、JSON round-trip、placeholder 驱逐

## 5. 进度捕获与事件总线（`web/progress.py`）

- [x] 5.1 定义 `PROGRESS_RE = re.compile(r"^处理中 (?P<current>\d+)/(?P<total>\d+) - (?P<key>.+)$")`
- [x] 5.2 实现 `class PipelineTextCapture(io.TextIOBase)`：`__init__(on_line)`, `writable=True`, `isatty=False`, `write` 维护 `_buffer` 按 `\r|\n` 切行回调，`flush` 冲残留
- [x] 5.3 实现 `parse_progress_line(line) -> tuple[int,int,str] | None`
- [x] 5.4 定义 `TaskEvent` dataclass：`name`, `payload: dict`, `terminal: bool`
- [x] 5.5 定义 `Subscriber` dataclass：`subscriber_id`, `loop`, `queue: asyncio.Queue[TaskEvent]`
- [x] 5.6 实现 `class EventBus`，内部 `dict[task_id, dict[subscriber_id, Subscriber]]` + `threading.RLock`
- [x] 5.7 `EventBus.subscribe(task_id, loop) -> Subscriber`：拒绝超过 `max_sse_subscribers_per_task` → raise `TooManyStreamsError`；分配 `subscriber_id=uuid4().hex`
- [x] 5.8 `EventBus.unsubscribe(task_id, subscriber_id)` 幂等
- [x] 5.9 `EventBus.publish_snapshot(task_id, snapshot, event_name, terminal=False, extra=None)`：先在锁内复制订阅者列表 → 锁外逐个 `loop.call_soon_threadsafe(_deliver, sub, event)`
- [x] 5.10 `_deliver(subscriber, event)`：try `put_nowait` → 满则丢最旧非-terminal → 仍满（terminal）则清空再 put
- [x] 5.11 实现 `format_sse(event) -> str`：`event: <name>\ndata: <json>\n\n`
- [x] 5.12 实现 `async def stream_task_events(task_id, store, bus, heartbeat_seconds) -> AsyncIterator[str]`：种子事件 → `asyncio.wait_for(queue.get(), heartbeat_seconds)` 循环 → finally unsubscribe
- [x] 5.13 单元测试 `tests/test_web_progress.py`：行解析、buffer 边界、SSE 格式、订阅/退订、慢消费者背压

## 6. 串行执行器（`web/queue.py`）

- [x] 6.1 定义 `WorkItem` dataclass：`task_id`
- [x] 6.2 实现 `class SerialMergeExecutor`：`__init__(store, bus, config, run_merge_fn, engine_factory_fn, loop)` 注入式
- [x] 6.3 `start()`：创建 `threading.Thread(target=_run, daemon=True)`，设置 `_stop_event = threading.Event()`
- [x] 6.4 `enqueue(task_id) -> int`：`store.set_queued` + `queue.Queue.put(WorkItem)` + 返回 `queue_position`
- [x] 6.5 `_run` 循环：`queue.get(timeout=1.0)` → 检查 `_stop_event` → 检查 `task.state == queued`（可能已被 DELETE）→ `_process_one(task_id)`
- [x] 6.6 `_process_one(task_id)`：lazy 构建 OCR engine（首次 + 锁保护）→ `set_running` + 推 progress 种子事件 → 构造 stdout/stderr capture → `try: run_merge(...)` → 异常映射 → `set_done` / `set_failed-*` → 终态事件
- [x] 6.7 OCR 模型缺失捕获：`set_failed-ocr-missing` + `_ocr_broken_flag.set()`；后续 enqueue 拒绝（路由层查询）
- [x] 6.8 `stop(grace_seconds)`：`_stop_event.set()` → `Thread.join(timeout=grace_seconds)` → 仍存活则记录日志（无法强中断 native 调用）
- [x] 6.9 `current_task_id() / queue_depth() / ocr_ready_flag() / ocr_broken_flag()` 只读访问器
- [x] 6.10 单元测试 `tests/test_web_queue.py`：注入 FakeOcrEngine + Fake run_merge；验证串行性、状态转换、stop 语义

## 7. 清理管理器（`web/cleanup.py`）

- [x] 7.1 实现 `class CleanupManager`：`__init__(store, bus, config)`
- [x] 7.2 `start()`：`Thread(target=_loop, daemon=True)` + `Event` 控制退出
- [x] 7.3 `_loop`：`_stop_event.wait(cleanup_interval_seconds)` 循环；每轮 `sweep_once(now)`
- [x] 7.4 `sweep_once(now)`：枚举 active dict → `should_delete(task, now)` → 推 `expired` 终态事件 → `mark_expired` → `delete_task`
- [x] 7.5 `should_delete(task, now)`：design.md §6 四个条件
- [x] 7.6 `run_startup_sweep(now)`：调用 `TaskStore.load_from_disk` 后，对剩余非 task.json 的孤立目录 `shutil.rmtree`
- [x] 7.7 placeholder TTL 驱逐：每次 sweep 末尾遍历 `placeholders`，超 24h 移除
- [x] 7.8 `stop(timeout)`：`_stop_event.set()` + `join(timeout)`
- [x] 7.9 单元测试 `tests/test_web_cleanup.py`：注入 fake `now()`、active_downloads 阻止删除、placeholder 驱逐

## 8. FastAPI 应用（`web/app.py`）

- [x] 8.1 定义 `class WebServices(dataclass)`：`config, store, bus, executor, cleanup, loop`
- [x] 8.2 实现 `create_app(config=None, *, run_merge=pipeline.run_merge, engine_factory=ocr.build_default_engine) -> FastAPI`
- [x] 8.3 lifespan：startup 阶段 → `cleanup.run_startup_sweep` → `store.load_from_disk` → `executor.start` → `cleanup.start`；shutdown 阶段 → `executor.stop(grace=30)` → `cleanup.stop(timeout=5)`
- [x] 8.4 lifespan：捕获 `OcrModelMissingError`（启动校验阶段）→ 打印中文消息 → `raise SystemExit(2)`
- [x] 8.5 挂载 `app.mount("/static", StaticFiles(directory=...))`
- [x] 8.6 `GET /` → 返回 `static/index.html`（FileResponse）
- [x] 8.7 `POST /api/tasks` 路由：检查 `executor.ocr_broken_flag` → 503；否则解析 multipart → 流式落盘 → 配额校验 → `store.create_task` → `executor.enqueue`
- [x] 8.8 上传落盘：每 part 写 `<task_dir>/input/.upload-<n>.part`，累计字节超限即 raise + 删任务目录；完成后 `os.replace` 到 `<task_dir>/input/<safe_filename>`
- [x] 8.9 `safe_filename`：去除路径分隔符 + 冲突重命名 `<stem> (n)<suffix>`
- [x] 8.10 `GET /api/tasks/{id}` → `store.require_snapshot` → 200 / 404
- [x] 8.11 `GET /api/tasks/{id}/events` → 检查 placeholder → 410 / 404 → 终态直接 yield 一次后关 → 否则 `stream_task_events`
- [x] 8.12 `GET /api/tasks/{id}/result` → 检查状态：done → FileResponse + BackgroundTask(note_download_finished)；queued/running → 409；placeholder → 410；其他 → 404
- [x] 8.13 `DELETE /api/tasks/{id}` → 状态分支：running → 409；其他 → `store.delete_task` → 204
- [x] 8.14 `GET /api/health` → `{"ok": true, "version": __version__, "ocr_cache_present": …, "engine_loaded": executor.ocr_ready_flag(), "queue_depth": executor.queue_depth(), "ocr_broken": executor.ocr_broken_flag()}`
- [x] 8.15 集成测试 `tests/test_web_app.py`（用 TestClient + FakeOcrEngine）：完整端到端、各路径状态码

## 9. CLI serve 子命令（`cli.py`）

- [x] 9.1 在 `cli.py` 顶部 `try: from fapiao_pdf.web import app as web_app_mod` → except ImportError 时延迟到 serve 调用时报错（可选 group 未装）
- [x] 9.2 实现 `@app.command("serve")` 函数，参数 `--host/--port/--retain-minutes/--max-upload-mb/--max-files/--max-single-file-mb`
- [x] 9.3 校验 host：非 loopback（≠ `127.0.0.1` `::1` `localhost`）→ 打印中文安全提示
- [x] 9.4 启动校验 `pipeline.ensure_ocr_ready(allow_download=False)`；缺失 → `typer.echo` + exit 2
- [x] 9.5 调用 `uvicorn.run(web_app_mod.create_app(config), host=..., port=..., workers=1, log_config=None)`
- [x] 9.6 集成测试 `tests/test_cli_serve.py`：参数解析、安全提示、OCR 缺失退出码

## 10. 前端：HTML 骨架（`web/static/index.html`）

- [x] 10.1 创建 `index.html`：`<!doctype html>` + `<html lang="zh-CN">` + `<meta name="viewport">` + `<title>fapiao 票据合并</title>`
- [x] 10.2 引入 `<link rel="stylesheet" href="/static/style.css">` + `<script type="module" src="/static/app.js"></script>`
- [x] 10.3 主结构：`<main class="layout">` 内部分 `upload-card`, `task-card`, `result-card`, `error-card`, `warnings-panel`，初始仅 `upload-card` 可见
- [x] 10.4 `upload-card`：拖拽区 `<div role="button" tabindex="0">` + 隐藏 `<input type="file" multiple accept=".jpg,.jpeg,.png,.pdf">` + 文件列表 `<ul>` + DPI 输入 `<input type="number" name="pdf_dpi" min="100" max="300" value="200">` + 提交按钮
- [x] 10.5 `task-card`：状态徽章 + 队列位置 + 进度条 `<progress>` + 当前 key + warnings 滚动列表
- [x] 10.6 `result-card`：摘要文本 + 下载按钮 `<a download>` + 重新提交按钮
- [x] 10.7 `error-card`：图标 + 中文消息（按 design.md §9.6 表填充）+ 重试按钮
- [x] 10.8 a11y：`role="status"` + `aria-live="polite"` 给 task-card；`role="alert"` 给 error-card；`aria-describedby` 关联 dropzone hint

## 11. 前端：CSS（`web/static/style.css`）

- [x] 11.1 `@layer tokens, components, utilities;`
- [x] 11.2 `@layer tokens` 写 `:root { --bg-base: ... }`，全部 25 个 token（design.md §9.3）
- [x] 11.3 `body` 几何网格背景（design.md §9.3 末尾 CSS 片段）
- [x] 11.4 `@layer components`：`.layout` grid 布局 + `.card` 玻璃拟态基础类（`background: var(--bg-card); backdrop-filter: blur(var(--blur-card)); border: var(--border-card); border-radius: var(--r-lg); box-shadow: var(--shadow-card);`）
- [x] 11.5 `@layer components`：`.btn-primary` 霓虹辉光 + hover 仅 `transform: scale(1.02)` + `box-shadow` 增强；禁用态降低不透明度
- [x] 11.6 `@layer components`：`.dropzone` drag-over 状态用 `transform` + `box-shadow` 表达
- [x] 11.7 `@layer components`：`.progress` 自定义样式（隐藏原生 `<progress>` 内部元素，叠加 ::-webkit-progress-value 与 -moz-progress-bar）
- [x] 11.8 `@layer components`：`.warning-row` 等宽字体 + 截断 + hover tooltip
- [x] 11.9 `@layer components`：状态徽章 `.badge--{queued,running,done,failed}`，颜色绑定 status tokens
- [x] 11.10 `@layer utilities`：`.hidden { display: none !important }` 唯一 utility
- [x] 11.11 焦点环：`:focus-visible { outline: 2px solid var(--neon-accent); outline-offset: 2px; }`
- [x] 11.12 媒体查询：`@media (prefers-reduced-motion: reduce)` 关闭 transform 与 backdrop-filter
- [x] 11.13 视觉对比检查：所有文本-背景组合 ≥ 4.5:1（手工 OKLCH lightness 差 ≥ 0.50 验证）

## 12. 前端：JS 控制器（`web/static/app.js`）

- [x] 12.1 模块顶部声明 `const STATE = { current: "idle", taskId: null, files: [], pdfDpi: 200, eventSource: null, warnings: [] }`
- [x] 12.2 `init()`：解析 `URLSearchParams.get("task")` → 若有则 `transition("queued", { taskId })`；否则 `transition("idle")`
- [x] 12.3 `transition(next, payload)`：依据状态机更新 `STATE.current`，调用 `render()` + 触发 side-effect（开/关 SSE，发请求）
- [x] 12.4 `render()`：单次 DOM 更新，按 `STATE.current` 切换 `.hidden` 类；用 `requestAnimationFrame` 包裹批量写
- [x] 12.5 拖拽处理：`dragenter/dragover/dragleave/drop`；`drop` 中过滤 `.jpg/.jpeg/.png/.pdf`（client-side 提示），保留全部 part 提交
- [x] 12.6 `submitUpload()`：构造 `FormData`，附带 `pdf_dpi`；`fetch("/api/tasks", { method: "POST", body: fd, signal })`；成功 → `transition("queued", { taskId })`，失败按 `error` 字段映射中文消息
- [x] 12.7 `subscribeEvents(taskId)`：`new EventSource(/api/tasks/${id}/events)`；监听 `queued/progress/warning/done/error/expired/heartbeat`
- [x] 12.8 事件处理：`progress` → 更新进度条 + key；`warning` → 追加到 `STATE.warnings`（保留最新 200，渲染最新 50，超出显示「…(N 条已隐藏)」）；`done` → `transition("done", payload.summary)`；`error/expired` → `transition("failed", payload.state)`
- [x] 12.9 `cancelTask(taskId)`：`fetch(DELETE)` → `transition("idle")`
- [x] 12.10 `triggerDownload(taskId)`：`location.href = /api/tasks/${id}/result`
- [x] 12.11 `resetSession()`：清空 STATE，去除 URL 参数（`history.replaceState`），`transition("idle")`
- [x] 12.12 自动滚动 warnings：检测用户向上滚后停止跟随
- [x] 12.13 `pageshow` / `beforeunload`：保存 `taskId` 到 URL（`history.replaceState`）
- [x] 12.14 错误消息映射表：`failed-no-input/...-ocr-missing/...-fatal/...-internal/...-restart` → design.md §9.6 文案

## 13. 测试

- [x] 13.1 `tests/test_web_health.py`：health 端点字段 + 无 OCR 模型场景
- [x] 13.2 `tests/test_web_upload.py`：成功 202 / 空上传 400 / 超量 413（三种）/ 无效 dpi 422 / 磁盘满模拟 507
- [x] 13.3 `tests/test_web_serial.py`：注入慢 FakeOcrEngine（sleep）+ 提交 3 任务 → 验证 running 时刻不重叠 + queue_position 顺序
- [x] 13.4 `tests/test_web_progress_sse.py`：FakeOcrEngine 触发已知 N/M 序列；订阅 SSE 验证 event 顺序与 payload 字段
- [x] 13.5 `tests/test_web_progress_sse.py`：多订阅者 + 慢消费者 + terminal 永不丢
- [x] 13.6 `tests/test_web_download.py`：done 后下载 200 + filename 短 8 位；queued 时 409；deleted 后 410
- [x] 13.7 `tests/test_web_delete.py`：queued 删除 204；running 删除 409；queued 删除后 worker 跳过
- [x] 13.8 `tests/test_web_cleanup.py`：注入 fake clock 推进；active_downloads 阻止；placeholder TTL；启动清扫
- [x] 13.9 `tests/test_web_errors.py`：各 pipeline 异常 → 状态码与 ApiErrorCode 映射
- [x] 13.10 `tests/test_web_restart.py`：写入 running 状态的 task.json → 启动 store.load_from_disk → 验证标 failed-restart + placeholder
- [x] 13.11 `tests/test_web_properties.py`：用 Hypothesis 实现 design.md §11 中至少 6 个属性（fifo / progress_monotonicity / terminal_uniqueness / warning_append_only / cleanup_never_deletes_active / metadata_roundtrip）
- [x] 13.12 既有 `tests/test_cli.py` / `tests/test_pipeline_e2e.py` 全绿（无回归）

## 14. 文档

- [x] 14.1 `README.md` 新增「Web 模式」章节：`fapiao serve` 命令 + 端点表 + 安全提示 + 离线提示
- [x] 14.2 `src/fapiao_pdf/web/CLAUDE.md`：模块索引，参考 `src/fapiao_pdf/CLAUDE.md` 风格
- [x] 14.3 根 `CLAUDE.md` §5 模块导航增加 `src/fapiao_pdf/web/` 行
- [x] 14.4 根 `CLAUDE.md` §2 关键命令速查增加 `fapiao serve` 示例
- [x] 14.5 根 `CLAUDE.md` §6.1 OCR 行为增加「Web 模式：单 worker + lazy 单例 engine」一段
- [x] 14.6 根 `CLAUDE.md` §7 环境变量增加 `WEB_CONCURRENCY=1`（强制）说明

## 15. 验收

- [x] 15.1 `openspec status --change add-web-ui --json` 显示 `isComplete=true`
- [x] 15.2 `openspec validate add-web-ui` 通过
- [x] 15.3 `pytest tests/ -q` 全绿（71 既有 + 新增 ≥ 50）
- [x] 15.4 手测 `fapiao serve` 启动 < 3s 且 OCR 模型未加载（首个任务才加载）
- [x] 15.5 浏览器手测：拖拽 5 张样本 → SSE 进度可见 → 下载 PDF 与 `fapiao merge` 输出一致（除时间戳）
- [x] 15.6 断网手测完整流程（启动 → 上传 → 进度 → 下载）
