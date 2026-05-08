# CLAUDE.md — src/fapiao_pdf/web/

> Web UI 子包：FastAPI 服务 + 单页前端 + 串行任务编排。复用 `pipeline.run_merge()`，零反向依赖。
>
> 父目录：[../../../CLAUDE.md](../../../CLAUDE.md)

## 1. 模块清单

| 模块 | 行数 | 职责 | 对应测试 |
|---|---|---|---|
| `__init__.py` | 0 | 子包占位 | — |
| `config.py` | 80 | `WebConfig` dataclass + 默认常量 + `validate_web_config()` | `test_web_config.py` |
| `errors.py` | 148 | `TaskState` / `ApiErrorCode` 枚举 + `WebError` 层级 + `map_pipeline_exception()` | `test_web_errors.py` |
| `tasks.py` | 518 | `TaskRecord` / `TaskStore` + `task.json` 持久化 + 过期占位 OrderedDict | `test_web_tasks.py`, `test_web_restart.py`, `test_web_properties.py` |
| `progress.py` | 276 | `PipelineTextCapture` + `EventBus` + SSE 格式化 | `test_web_progress.py` |
| `queue.py` | 299 | `SerialMergeExecutor` 单 worker 线程 + lazy OCR 引擎 | `test_web_queue.py` |
| `cleanup.py` | 150 | 启动清扫 + 周期 sweep + placeholder TTL 驱逐 | `test_web_cleanup.py` |
| `app.py` | 473 | FastAPI 组装 + lifespan + 6 个路由 + StaticFiles | `test_web_app.py` |
| `static/index.html` | 76 | SPA 骨架（5 张卡片 + a11y） | （手测） |
| `static/style.css` | 296 | OKLCH tokens + 玻璃拟态 + 霓虹辉光 + reduced-motion | （手测） |
| `static/app.js` | 480 | 状态机 + 拖拽 + SSE + 错误映射 + URL 恢复 | （手测） |

## 2. 关键架构

### 双层并发模型

```
asyncio event loop (main)        ←→        worker thread (single)
  FastAPI routes                              SerialMergeExecutor._run
  TaskStore (RLock)                           pipeline.run_merge()
  EventBus → asyncio.Queue per sub          PipelineTextCapture
  GET /events streaming                      loop.call_soon_threadsafe(deliver)
```

- 同步桥接：`loop.call_soon_threadsafe`（不直接 put_nowait 跨线程）
- 单 OCR engine 实例（PaddleOCR 非线程安全）

### 状态机

`queued → running → done | failed-{no-input,ocr-missing,fatal,internal,restart}`

终态后 `completed_at + retain_minutes` 过期，进入 placeholder（OrderedDict, TTL=24h, max=1024 LRU）。

### task.json schema v1（`tasks.py::TaskRecord.to_json`）

详见 [openspec/changes/add-web-ui/design.md §3](../../../openspec/changes/add-web-ui/design.md)。

## 3. HTTP 端点

| 方法 | 路径 | 主要状态码 |
|---|---|---|
| `POST` | `/api/tasks` | 202 / 400 / 413 / 422 / 503 / 507 |
| `GET` | `/api/tasks/{id}` | 200 / 404 |
| `GET` | `/api/tasks/{id}/events` | 200 SSE / 404 / 410 / 429 |
| `GET` | `/api/tasks/{id}/result` | 200 PDF / 404 / 409 / 410 |
| `DELETE` | `/api/tasks/{id}` | 204 / 404 / 409 |
| `GET` | `/api/health` | 200 |

## 4. 启动序列

```python
create_app(config) → lifespan startup:
  1. cleanup.run_startup_sweep(now)
  2. store.load_from_disk(retain_minutes)
  3. executor.start()
  4. cleanup.start()
```

## 5. 关键约束

- **非反向依赖**：不修改 `pipeline.py` / `ocr.py` / `cli.py` 签名；仅通过 `engine=` / `stdout=` / `stderr=` 注入位通信。
- **强制单 worker**：`uvicorn.run(workers=1)`；忽略 `WEB_CONCURRENCY`。
- **重启恢复**：`queued`/`running` 在重启时标 `failed-restart` + 加入 placeholder 24h。
- **配额**：`--max-upload-mb=200` / `--max-files=200` / `--max-single-file-mb=50`，可通过 CLI 参数覆盖。

## 6. 详细文档

- [../../../openspec/changes/add-web-ui/design.md](../../../openspec/changes/add-web-ui/design.md) — 完整设计决策与替代方案
- [../../../openspec/changes/add-web-ui/proposal.md](../../../openspec/changes/add-web-ui/proposal.md) — 变更提案
