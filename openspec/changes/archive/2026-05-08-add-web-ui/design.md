# Design — `add-web-ui`

> 在不破坏 CLI 的前提下，新增 `fapiao_pdf.web` 子包：FastAPI 服务 + 单页前端 + 串行任务编排，复用 `pipeline.run_merge()` 完成端到端处理。
>
> 多模型协作输出综合：codex 提供后端架构与 PBT 属性骨架；前端视觉与状态机由本文件直接定稿（gemini 因 API key 不可用，已重试 2 次后跳过）。

---

## 1. 架构总览

### 1.1 双层并发模型

```
┌─────────────────────────────────────────────────────────────────┐
│                  asyncio event loop  (main thread)              │
│                                                                  │
│   FastAPI routes ──▶ TaskStore (RLock) ──▶ EventBus ─┐          │
│         │                                             │          │
│         │ POST /tasks 落盘 + enqueue                  │          │
│         │ GET  /events  subscribe → asyncio.Queue ◀──┘          │
│         │ GET  /result  FileResponse + active_downloads          │
│         ▼                                                        │
│   queue.Queue[WorkItem]    (thread-safe)                        │
└──────────────┬───────────────────────────────────────────────────┘
               │ blocking get()
               ▼
┌─────────────────────────────────────────────────────────────────┐
│            SerialMergeExecutor worker  (single thread)          │
│                                                                  │
│   1. dequeue WorkItem                                            │
│   2. lazy build PaddleOcrEngine (lock-protected, once)          │
│   3. PipelineTextCapture(stdout/stderr) → handlers              │
│   4. handlers update TaskStore + EventBus.publish_snapshot      │
│      via loop.call_soon_threadsafe                              │
│   5. pipeline.run_merge(input, output, engine=…, stdout=…, …)   │
│   6. set_done / set_failed-* + persist task.json                │
└─────────────────────────────────────────────────────────────────┘
```

- **同步层**：`queue.Queue` + 单 worker 线程；唯一调用 `pipeline.run_merge()` 与 OCR engine 的位置。
- **异步层**：FastAPI 路由 + 每订阅者一个 `asyncio.Queue`；事件由 worker 通过 `loop.call_soon_threadsafe` 投递。
- **桥接原语**：`loop.call_soon_threadsafe(deliver, subscriber, event)`，永不在 worker 线程直接 `put_nowait` 到 asyncio 队列。

### 1.2 模块清单（src/fapiao_pdf/web/）

| 模块 | 职责 | 关键导出 |
|---|---|---|
| `config.py` | 配置数据类 + 常量 + 路径解析 | `WebConfig`, `resolve_task_root()` |
| `errors.py` | `TaskState` / `ApiErrorCode` 枚举 + pipeline 异常映射 | `map_pipeline_exception()` |
| `tasks.py` | `TaskRecord` / `TaskStore` / `task.json` 持久化 + 过期占位 | `TaskStore`, `TaskSnapshot` |
| `progress.py` | `PipelineTextCapture` + `EventBus` + SSE 格式化 | `PipelineTextCapture`, `EventBus`, `stream_task_events()` |
| `queue.py` | `SerialMergeExecutor` 单 worker 线程 + OCR 单例 | `SerialMergeExecutor` |
| `cleanup.py` | 启动清扫 + 周期 sweep + 占位驱逐 | `CleanupManager` |
| `app.py` | FastAPI 组装 + lifespan + 路由 + StaticFiles | `create_app()` |
| `static/index.html`, `static/app.js`, `static/style.css` | 零构建 SPA | — |

**禁止反向依赖**：`web/` 不得修改 `pipeline.py` / `ocr.py` / `cli.py` 的签名；只通过既有注入位（`engine=`, `stdout=`, `stderr=`）通信。

---

## 2. 任务状态机

```
        POST /api/tasks
             │
             ▼
       (落盘成功)
             │
             ▼
        ┌─queued─┐
        │   │   │
   DELETE   │  worker dequeue
        │   ▼   │
        │  running ◀───── (任意时刻 ≤ 1)
        │   │
        │   ├── RunStats ──▶ done
        │   ├── NoProcessableInputError ──▶ failed-no-input
        │   ├── OcrModelMissingError ──▶ failed-ocr-missing (latch=ocr_broken)
        │   ├── FatalRunError ──▶ failed-fatal
        │   └── 其他 Exception ──▶ failed-internal
        ▼
   sweep │  (completed_at + retain_minutes < now AND active_downloads == 0)
         ▼
       expired (placeholder TTL 24h, 内存 OrderedDict, 上限 1024)
             │
             ▼
       (TTL 到 / LRU 驱逐 / DELETE) ──▶ 完全消失（GET /tasks → 404）
```

**重启恢复**（lifespan startup）：
1. 扫描 `<tmpdir>/fapiao-tasks/*/task.json`。
2. 任意 `state ∈ {queued, running}` → 改为 `failed-restart`，删除目录。
3. 任意损坏 JSON → 删除目录，记入 placeholder（`reason=corrupt-startup`）。
4. 任意 terminal 状态且 `created_at + retain_minutes >= now` → 加载入 store。
5. 其余目录 → 删除。

---

## 3. task.json schema (v1)

```json
{
  "schema_version": 1,
  "task_id": "a3f29e1c-7b14-4f88-9c66-6d2e8a8c5e91",
  "state": "running",
  "queue_seq": 17,
  "created_at": "2026-05-07T03:14:22.501+00:00",
  "updated_at": "2026-05-07T03:14:25.122+00:00",
  "completed_at": null,
  "expires_at": null,
  "pdf_dpi": 200,
  "input_dir": "<absolute path under task root>",
  "output_path": "<absolute path under task root>",
  "progress": {"current": 12, "total": 47, "key": "<display_key>"},
  "warnings": ["切分警告：…", "OCR 失败：…"],
  "summary": null,
  "error": null,
  "result_available": false,
  "last_download_at": null
}
```

**字段语义**：
- `expires_at`：仅 `state ∈ terminal` 时被设置为 `completed_at + retain_minutes`；其余为 `null`（永不过期）。
- `result_available`：仅 `state == "done"` 且 `output_path` 存在时为 `true`。
- `active_downloads`：进程内字段，**不持久化**；仅供 cleanup 判断。

**原子写**：先写 `task.json.tmp`，`os.replace()` 到 `task.json`。每次状态变更后立即持久化。

---

## 4. SSE 事件契约

### 4.1 事件名词汇

| event | terminal | 触发 |
|---|---|---|
| `queued` | false | task 创建后首次推送（含订阅时种子事件） |
| `progress` | false | `处理中 N/M - <key>` 行命中 |
| `warning` | false | stderr 行（非空） |
| `heartbeat` | false | 订阅者 queue 等待 ≥ 15s 无事件 |
| `done` | true | `set_done` |
| `error` | true | 任意 `failed-*` |
| `expired` | true | `mark_expired` |

### 4.2 Payload 结构

**所有非-heartbeat 事件**统一携带完整 `TaskSnapshot`（snake_case）：
```json
{
  "task_id": "…",
  "state": "running",
  "queue_position": 0,
  "progress": {"current": 12, "total": 47, "key": "…"},
  "warnings": ["…", "…"],
  "summary": null,
  "error": null,
  "created_at": "…",
  "expires_at": null,
  "result_available": false
}
```

**`warning` 事件**额外字段：`{"message": "<本次新增的中文警告>"}`，避免前端重复扫描整个 warnings 数组。

**`heartbeat` 事件**：`{"task_id": "…", "server_time": "<ISO8601>"}`。

**SSE 格式**（每事件）：
```
event: progress
data: {"task_id":"…","state":"running",…}

```

### 4.3 慢订阅者背压

- 每订阅者 `asyncio.Queue(maxsize=32)`。
- 队列满时：丢弃最旧的非-terminal 事件，永不丢 terminal。
- 单 task 最大并发订阅 16；超限 SSE 立即返回 429。

### 4.4 晚到订阅者

- task 已终态：不注册订阅者，直接 yield 单个 terminal 事件后关闭。
- task 在 expired placeholder：返回 410 `TaskExpired`，**不发 SSE 事件**。
- task 不存在：返回 404 `TaskNotFound`。

---

## 5. HTTP 接口契约

### 5.1 端点矩阵

| 方法 | 路径 | 状态码 | 响应 |
|---|---|---|---|
| `POST` | `/api/tasks` | 202 | `{"task_id":"…","queue_position":N}` |
| `POST` | `/api/tasks` | 400 / 413 / 422 / 503 / 507 | `{"error": ApiErrorCode}` |
| `GET` | `/api/tasks/{id}` | 200 | `TaskSnapshot` |
| `GET` | `/api/tasks/{id}` | 404 | `{"error":"TaskNotFound"}` |
| `GET` | `/api/tasks/{id}/events` | 200 | `text/event-stream` |
| `GET` | `/api/tasks/{id}/events` | 404 / 410 / 429 | `{"error": …}` |
| `GET` | `/api/tasks/{id}/result` | 200 | `application/pdf` |
| `GET` | `/api/tasks/{id}/result` | 404 / 409 / 410 | `{"error": …}` |
| `DELETE` | `/api/tasks/{id}` | 204 | （空） |
| `DELETE` | `/api/tasks/{id}` | 404 / 409 | `{"error": …}` |
| `GET` | `/api/health` | 200 | `{"ok":true,"version":"…","ocr_cache_present":bool,"engine_loaded":bool,"queue_depth":int,"ocr_broken":bool}` |

### 5.2 ApiErrorCode 完整集合

```
NoUploadedFiles  UploadTooLarge  TooManyFiles  SingleFileTooLarge
InvalidPdfDpi    InsufficientStorage  TaskNotFound  TaskExpired
TaskNotReady     TaskRunning      OcrModelMissing  TooManyStreams
```

### 5.3 上传配额（用户决策 Q2）

- `--max-upload-mb` 默认 200；总字节超限 → 413 `UploadTooLarge`。
- `--max-files` 默认 200；按 multipart 所有 part 计数（含被扩展名过滤的） → 413 `TooManyFiles`。
- `--max-single-file-mb` 默认 50；任意单文件超限 → 413 `SingleFileTooLarge`。
- 磁盘满（`OSError.errno == ENOSPC`）→ 删除任务目录 → 507 `InsufficientStorage`。
- `pdf_dpi` 必须 ∈ [100, 300]；否则 422 `InvalidPdfDpi`。

### 5.4 删除语义

- `state ∈ {queued, done, failed-*}` → 204；worker 取出 queued 时若已 deleted 则 skip。
- `state == running` → 409 `TaskRunning`。
- `state == expired` → 404 `TaskNotFound`（与未知 task 同语义）。

### 5.5 下载文件名（用户决策 Q4）

```
Content-Disposition: attachment; filename="fapiao-<short8>.pdf"; filename*=UTF-8''fapiao-<short8>.pdf
```

`<short8>` = `task_id[:8]`（hex）。同一时间窗口内冲突概率 ≈ 0。

---

## 6. 保留与清理（用户决策 Q1）

- 默认 `--retain-minutes 60`；仅作用于 `state ∈ terminal`。
- `queued` / `running` 任务**永不**参与过期删除。
- `cleanup` 周期：每 5 分钟执行 `sweep_once(now)`。
- 删除条件（同时满足）：
  1. `state ∈ {done, failed-*, failed-restart}`
  2. `completed_at + retain_minutes < now`
  3. `active_downloads == 0`
  4. `last_download_at` 为 `None` 或 `now - last_download_at >= 60s`
- 删除流程：
  1. `EventBus.publish_snapshot(task_id, snapshot, "expired", terminal=True)`（已订阅者收尾）
  2. `mark_expired(task_id)` → 加入 placeholder（OrderedDict，TTL=24h，max 1024 条；超限 LRU 驱逐）
  3. `shutil.rmtree(task_dir, ignore_errors=False)`，失败仅记日志，下轮重试
- placeholder 命中：`GET /tasks/{id}` → 404；`GET /tasks/{id}/result` → 410；`GET /tasks/{id}/events` → 410；`DELETE` → 404。

---

## 7. 关闭与崩溃恢复（用户决策 Q3）

- `uvicorn.run()` 接管 SIGINT / SIGTERM；应用层不注册自定义 handler。
- lifespan shutdown 流程：
  1. 停止接受新任务（`SerialMergeExecutor.stop(grace_seconds=30)`）
  2. 当前 in-flight 任务允许继续最多 30 秒
  3. 超时 → `threading.Event` 通知 worker 完成后退出（无法强中断 native OCR 调用，但保证不再处理新任务）
  4. 关闭 `CleanupManager`（最多 5 秒等待）
  5. 进程退出
- 下次启动：
  - 任意 `state ∈ {queued, running}` 的 `task.json` → 改为 `failed-restart`，加入内存 store（成为终态）；删除其工作目录
  - 加入 placeholder 24h，使前端 `GET /result` 收到 410 而非 404

---

## 8. 进度捕获契约

### 8.1 PipelineTextCapture（io.TextIOBase 子类）

- `isatty() → False`：强制 `ProgressReporter` 走非 TTY 分支，每次 `\n` 结尾。
- `write(s)`：累积到 `_buffer`，按 `\r` 或 `\n` 切行，逐行回调 `_on_line(line)`。
- `flush()`：把残留半行也作为一条事件冲出。
- `_on_line` 回调在 worker 线程执行；handler 内部调用 `EventBus.publish_snapshot(...)` 时，跨线程投递由 EventBus 内部完成。

### 8.2 行解析器

```python
PROGRESS_RE = re.compile(r"^处理中 (?P<current>\d+)/(?P<total>\d+) - (?P<key>.+)$")
```

- stdout 命中 → `set_progress` + 推 `progress` 事件。
- stdout 未命中 → 丢弃（不混入 warning）。
- stderr 非空行 → `append_warning` + 推 `warning` 事件（payload.message = 本行）。
- stderr 空行 → 忽略。

---

## 9. 前端设计

### 9.1 文件分解

```
src/fapiao_pdf/web/static/
  index.html        # 单文件，所有 DOM 与 inline data-* 配置
  app.js            # ES module，<script type="module" src="/static/app.js">
  style.css         # @layer tokens, components, utilities
```

- **JS 范式**：纯函数控制器 + 单一 `state` 对象 + 显式 transition 表；不使用 class、不使用 custom element、不引入任何 CDN。
- **DOM 操作**：限定 `requestAnimationFrame` 包裹的 batch update，避免 SSE 高频事件抖动。
- **事件源**：`EventSource` 唯一渠道；不做轮询 fallback（Chrome/Edge/Firefox/Safari evergreen 全支持）。

### 9.2 UI 状态机

| state | 可见区域 | fetch 活动 | 按钮 |
|---|---|---|---|
| `idle` | upload-card（拖拽区 + 文件列表 + DPI 输入） | 无 | 开始合并 |
| `uploading` | upload-card（禁用） + spinner | `POST /api/tasks` (multipart) | 取消上传（abort） |
| `queued` | task-card（队列位置 + 等待动画） | `GET /events` (SSE) | 取消任务（DELETE） |
| `running` | task-card（进度条 + 当前 key + warnings 列表） | `GET /events` (SSE) | （无） |
| `done` | result-card（摘要 + 下载按钮） | （SSE 已关闭） | 下载 PDF / 重新提交 |
| `failed-*` | error-card（中文消息 + retry 按钮） | （SSE 已关闭） | 重新提交 |

转换：所有终态 → `idle`（重新提交）；`uploading` → `idle`（取消）；`queued/running` → `failed-internal`（DELETE）。

### 9.3 视觉 tokens

```css
@layer tokens {
  :root {
    /* 背景与表面（OKLCH 暗色基底） */
    --bg-base:        oklch(0.16 0.02 240);
    --bg-card:        oklch(0.20 0.025 240 / 0.72);    /* glassmorphism */
    --bg-overlay:     oklch(0.12 0.015 240 / 0.85);

    /* 文本 */
    --text-primary:   oklch(0.96 0.01 240);
    --text-secondary: oklch(0.68 0.02 240);
    --text-muted:     oklch(0.50 0.02 240);

    /* 霓虹与状态 */
    --neon-accent:    oklch(0.85 0.18 195);   /* 青蓝 */
    --neon-secondary: oklch(0.78 0.18 295);   /* 紫 */
    --status-success: oklch(0.78 0.18 145);
    --status-warning: oklch(0.82 0.18 75);
    --status-error:   oklch(0.65 0.22 25);

    /* 字体 */
    --font-mono: ui-monospace, "JetBrains Mono", "Cascadia Code",
                 Consolas, "Liberation Mono", "Microsoft YaHei Mono", monospace;
    --fs-xs: 12px; --fs-sm: 13px; --fs-md: 15px;
    --fs-lg: 18px; --fs-xl: 24px; --fs-2xl: 32px;

    /* 间距（4px 基） */
    --sp-1: 4px;  --sp-2: 8px;  --sp-3: 12px; --sp-4: 16px;
    --sp-5: 24px; --sp-6: 32px; --sp-7: 48px; --sp-8: 64px;

    /* 圆角与阴影 */
    --r-sm: 4px; --r-md: 8px; --r-lg: 16px; --r-xl: 24px;
    --shadow-card: 0 8px 32px oklch(0% 0 0 / 0.40);
    --shadow-glow: 0 0 24px oklch(0.85 0.18 195 / 0.35);

    /* 玻璃拟态 */
    --blur-card: 14px;
    --border-card: 1px solid oklch(1 0 0 / 0.08);

    /* 动画 */
    --dur-fast: 150ms; --dur-std: 250ms; --dur-slow: 400ms;
    --ease-out: cubic-bezier(0.2, 0.8, 0.2, 1);
    --ease-in:  cubic-bezier(0.4, 0, 0.6, 1);
  }
}
```

**几何网格背景**（CSS 渐变，不引图片）：
```css
body {
  background:
    radial-gradient(ellipse at 20% 0%, oklch(0.30 0.10 295 / 0.30), transparent 60%),
    radial-gradient(ellipse at 80% 100%, oklch(0.28 0.10 195 / 0.28), transparent 60%),
    linear-gradient(to right,  oklch(1 0 0 / 0.04) 1px, transparent 1px) 0 0 / 48px 48px,
    linear-gradient(to bottom, oklch(1 0 0 / 0.04) 1px, transparent 1px) 0 0 / 48px 48px,
    var(--bg-base);
}
```

**允许的动画属性**：仅 `transform`、`opacity`、`backdrop-filter`、`box-shadow`（GPU 友好）。

### 9.4 a11y

- 进度区域：`<div role="status" aria-live="polite">`；error 区域 `aria-live="assertive"`。
- 拖拽区可 Tab focus；`Enter` / `Space` 触发文件选择。
- 对比度：所有文本 / 背景组合 ≥ 4.5:1（OKLCH lightness 差 ≥ 0.50）。
- 焦点环：`outline: 2px solid var(--neon-accent); outline-offset: 2px;`。

### 9.5 Warning 列表 DOM 上限

- 内存最多保留最新 200 条；DOM 渲染最新 50 条；超出显示「…(N 条已隐藏)」。
- 自动滚动到最新；用户向上滚后停止自动滚（`scrollTop` 检测）。

### 9.6 失败状态文案

| state | 中文消息（前端固定） |
|---|---|
| `failed-no-input` | 未发现可处理的输入。请检查目录中是否包含 `.jpg / .jpeg / .png / .pdf` 文件。 |
| `failed-ocr-missing` | OCR 模型未就绪，请联系部署管理员运行 `fapiao init`。 |
| `failed-fatal` | 处理失败：{error}（致命错误，请联系管理员） |
| `failed-internal` | 内部错误，请稍后重试。若反复出现请联系管理员。 |
| `failed-restart` | 服务在处理过程中重启，任务未能完成。请重新提交。 |

### 9.7 字段命名约定

- 全部 JSON 字段 **snake_case**（与 backend 一致）；前端不做 camelCase 转换。
- SSE event 名小写英文；与 §4.1 表一致。
- multipart 字段名：`files`（多文件）+ `pdf_dpi`（与 CLI `--pdf-dpi` 对齐）。

---

## 10. CLI 集成

```python
@app.command("serve")
def serve_command(
    host: Annotated[str, typer.Option("--host")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port")] = 8000,
    retain_minutes: Annotated[int, typer.Option("--retain-minutes")] = 60,
    max_upload_mb: Annotated[int, typer.Option("--max-upload-mb")] = 200,
    max_files: Annotated[int, typer.Option("--max-files")] = 200,
    max_single_file_mb: Annotated[int, typer.Option("--max-single-file-mb")] = 50,
) -> None:
    ...
```

- 启动时调用 `pipeline.ensure_ocr_ready(allow_download=False)`；缺失 → 中文 actionable 消息 + exit 2。
- 非 loopback host → 启动前打印安全提示。
- 强制 `uvicorn.run(..., workers=1)`；忽略环境变量 `WEB_CONCURRENCY`。

---

## 11. PBT 属性清单（实施时落入 `tests/test_web_properties.py`）

| 属性 | 不变量 | 证伪策略 |
|---|---|---|
| `strict_fifo_serialization` | 任意时刻 `running` 任务数 ≤ 1；`running` 起始时间严格按提交序单调 | 并发 N 个 POST，事件日志查找 running 区间重叠或顺序反转 |
| `no_task_before_full_upload` | multipart 中断时不创建 task 记录、不留最终命名文件 | 在拷贝循环随机 raise CancelledError 注入 |
| `progress_monotonicity` | 单 task 内 `progress.current` 严格递增；`total` 不变；`1 ≤ current ≤ total` | 随机注入 partial write、flush；扫描事件序列 |
| `terminal_uniqueness` | 每 task 至多一个 terminal 事件；之后无 progress/warning | 对 worker/cleanup/delete/restart 做随机交错 |
| `late_subscriber_gets_terminal` | 终态后订阅 → 收到 1 个 terminal 事件后 close | 在 done/failed/expired 后建立 SSE，统计事件数 |
| `multi_subscriber_snapshot_equivalence` | 同 task 多订阅者最终非-heartbeat snapshot 相等 | 多订阅者 + 慢消费者 + 队列合并 |
| `warning_append_only` | `warnings` 仅追加；任意 snapshot 是后续 snapshot 的前缀 | 注入 stderr + 并发查询 |
| `result_visibility_matches_state` | `result_available == True ⇔ state == done ∧ output 文件存在` | 切换路径检查 |
| `cleanup_never_deletes_active` | `queued/running/active_downloads>0` 不被 sweep 删除 | 推进时间 + 触发下载 + sweep |
| `expired_placeholder_semantics` | placeholder 内 `GET /result` → 410；TTL 后 → 404 | 推进时间至 24h 边界 |
| `metadata_roundtrip` | `TaskRecord` JSON 写读后逻辑等价（除 `active_downloads`） | 随机生成 record 后 round-trip |
| `atomic_upload_names` | scanner 永远看不到 `.part` 文件 | 并发上传 + 目录遍历 |
| `delete_non_running_is_effective` | `queued/done/failed-*` 删除后目录消失；worker 跳过被删 queued | DELETE 与 worker 竞态 |
| `upload_limit_enforcement` | 超限请求绝不返回 202、不创建 task | 边界值生成（limit-1, limit, limit+1） |
| `ocr_broken_latch` | `ocr_broken=True` 后所有 POST → 503，重启前不恢复 | 模拟首次 lazy init 失败 |
| `no_sensitive_text_in_logs_or_events` | 事件/日志不含 OCR 原文/金额/税号 | FakeOcrEngine 输出敏感文本，扫描事件流 |
| `queue_position_consistency` | `running=0`, queued 严格递增, terminal=null | 并发提交/删除 + 频繁轮询 |
| `restart_reconciliation_is_safe` | 崩溃后重启不会重复执行旧任务 | 上传/运行/写元数据中途强制终止 |
| `sse_event_order_total` | 同订阅者收到的事件全序与 EventBus.publish 顺序一致 | 多发布者随机交错 |
| `download_filename_format` | filename 始终匹配 `^fapiao-[0-9a-f]{8}\.pdf$` | 抽样多任务 |

---

## 12. 已解决歧义审计

| 序号 | 原歧义 | 决策 | 来源 |
|---|---|---|---|
| 1 | retention 基准（创建 vs 完成） | `completed_at + retain_minutes`；queued/running 永不过期；24h placeholder | 用户 Q1 |
| 2 | `max_upload_mb` 默认值 | 200 | 用户 Q2 |
| 3 | `max_files` 计数语义 | multipart 全部 part 计数 | 用户 Q2 + codex G[10] |
| 4 | 单文件大小上限 | 新增 `--max-single-file-mb`，默认 50 | 用户 Q2 + codex G[20] |
| 5 | shutdown grace period | 30 秒；超时强退；下次启动标 failed-restart | 用户 Q3 |
| 6 | 信号处理归属 | uvicorn 接管，应用层不注册 | 用户 Q3 + codex G[8] |
| 7 | 下载文件名格式 | `fapiao-{task_id[:8]}.pdf` | 用户 Q4 |
| 8 | SSE event JSON schema | 所有事件含完整 snapshot；`warning` 加 `message`；`heartbeat` 加 `task_id`+`server_time` | codex G[1] 推荐 |
| 9 | task.json schema 版本 | 显式 `schema_version: 1` | codex G[2] |
| 10 | `queue_position` 语义 | running=0, queued≥1, terminal=null | codex G[3] |
| 11 | placeholder TTL/上限 | OrderedDict, TTL=24h, max=1024, LRU 驱逐 | codex G[4] |
| 12 | expired vs not-found 端点差异 | `GET /tasks` → 404；`GET /result` → 410；`GET /events` → 410；`DELETE` → 404 | codex G[5] |
| 13 | 单 task SSE 并发上限 | 16；超限 429 | codex G[6] |
| 14 | 慢订阅者背压 | per-subscriber asyncio.Queue(maxsize=32)，丢最旧非-terminal | codex G[7] |
| 15 | startup 顺序矛盾 | 先扫描 metadata → 标 failed-restart + placeholder → 删除目录 | codex G[12] |
| 16 | 磁盘满 HTTP 状态码 | 507 InsufficientStorage | codex G[13] |
| 17 | health endpoint 字段语义 | `ocr_cache_present`（缓存存在）+ `engine_loaded`（已构建）+ `ocr_broken` | codex G[14] |
| 18 | queued 删除允许性 | 允许；worker 取出时检查 deleted/expired 跳过 | codex G[15][16] |
| 19 | warning 事件语义 | snapshot 带全量 + 事件 payload 含本次新增 message | codex G[18] |
| 20 | task.json 损坏处理 | 删除目录 + placeholder reason="corrupt-startup"；GET /result 410 | codex G[19] |
| 21 | ocr_broken 自锁 | 一旦触发，POST 立即 503，重启前不重置 | codex G[21] |
| 22 | 前端字段命名 | snake_case，与后端一致 | 自定 |
| 23 | 前端 polling fallback | 不实现；要求 EventSource | 自定 |
| 24 | 前端 ES module 范式 | `<script type="module">`，纯函数控制器 | 自定 |
| 25 | 视觉 tokens 全集 | §9.3 OKLCH 表 | 自定 |

---

## 13. 风险登记

| 风险 | 缓解 | 残余 |
|---|---|---|
| PaddleOCR 非线程安全 | 单 worker + 单 engine 实例 | 长期运行内存泄漏 → 进程重启 |
| Windows SIGINT 怪行为 | uvicorn 接管 + lifespan 30s grace | 强杀仍 fail-restart |
| temp 目录被 OS 清理 | 文档明示「短期工作目录」 | 极端清理策略下不保 1h 可下载 |
| SSE 慢客户端 OOM | per-subscriber maxsize=32 + 丢旧 | 16 订阅 × 32 ≈ 512 事件上限 |
| multipart 中断半文件 | `.part` + os.replace 原子重命名；失败 → 删任务目录 | — |
| 公网部署滥用 | 默认 127.0.0.1 + 安全提示 + 文档建议反代鉴权 | 用户显式 0.0.0.0 时无鉴权 |
| 依赖体积 | `[project.optional-dependencies].web`，核心 CLI 用户无须装 fastapi | — |

---

## 14. 与 CLI 非回归保证

- 既有 `fapiao merge` / `fapiao init` 行为完全不变（spec.md 已显式断言）。
- 新增依赖归 `web` 可选组；`pip install -e ".[dev]"` 不强制装 fastapi。
- `pipeline.run_merge` 签名 0 改动；`web/` 仅消费 `engine=`、`stdout=`、`stderr=`。
- 既有 `tests/test_cli.py` / `tests/test_pipeline_e2e.py` 全部保持通过。
