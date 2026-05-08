## Why

当前 fapiao 仅为 CLI 工具，使用门槛集中在终端、虚拟环境和 Python 安装。多人共用一台具备 OCR 模型的工作机时，无法通过浏览器即时上传票据、查看进度并下载结果。

本变更在不破坏现有 CLI 的前提下，复用 `pipeline.run_merge()` 增加一个 FastAPI Web 服务与单页前端，提供拖拽上传、实时进度推送、合并 PDF 下载，以满足多用户、免登录、离线运行的内网/本机部署场景。

## What Changes

- 新增 `fapiao_pdf.web` 子包（FastAPI 应用 + 任务编排 + 静态资源），与现有 CLI 模块解耦。
- 新增 `fapiao serve` 子命令，启动 Uvicorn 服务，单进程运行以满足单 OCR 引擎全局共享假设。
- 新增 HTTP 端点：`POST /api/tasks` 上传创建任务、`GET /api/tasks/{id}` 轮询状态、`GET /api/tasks/{id}/events` SSE 进度流、`GET /api/tasks/{id}/result` 下载 PDF、`DELETE /api/tasks/{id}` 主动清理。
- 新增前端单页应用：暗色科技风、原生 HTML+JS+CSS、零构建步骤、由 FastAPI `StaticFiles` 直接托管。
- 新增任务编排层：全局串行队列（`queue.Queue` + 单工作线程）、进程内 `OcrEngine` 单例延迟加载、按 UUID 隔离任务工作目录、定时清理线程（保留 1 小时）。
- 新增依赖：`fastapi`、`uvicorn`、`python-multipart`（标准库 `asyncio` / `threading` / `tempfile` 已能覆盖队列与文件清理）。
- 现有 CLI、OCR 引擎、pipeline 主流程零改动；仅在 `pipeline.run_merge()` 边界使用既有 `engine` 注入位、既有 `stdout` 注入位捕获进度。
- 文档更新：`README.md` 增加「Web 模式」章节；`pyproject.toml` 增加 `web` 可选依赖组与 `fapiao serve` 入口。

## Capabilities

### New Capabilities
- `web-ui-fastapi-spa`：以 FastAPI + 单页前端形式提供浏览器版 fapiao；支持多用户匿名并发提交、串行 OCR 处理、SSE 进度推送、按 task_id 下载结果、超时自动清理。

### Modified Capabilities
- 无；本变更不修改既有 `invoice-order-pdf-merge` 行为。

## Impact

- 代码：新增 `src/fapiao_pdf/web/` 子包（app、queue、tasks、progress capture、static），新增 `cli.py::serve_command`；新增 `tests/test_web_*` 测试套件。
- CLI：新增 `fapiao serve [--host 127.0.0.1] [--port 8000] [--retain-minutes 60] [--max-upload-mb 200]`；不改变 `merge`、`init`、`--version`。
- 依赖：`pyproject.toml` 新增 `[project.optional-dependencies].web = ["fastapi>=0.115,<1", "uvicorn>=0.30,<1", "python-multipart>=0.0.9,<1"]`；保持核心 CLI 安装精简。
- 数据安全：与 CLI 一致，所有处理离线；上传文件落到 `<sysconfig.tmpdir>/fapiao-tasks/<task_id>/`；任务完成 1 小时后自动删除输入与输出；进程崩溃后启动时清扫遗留任务目录；日志保持现有「不输出 OCR 文本/金额/税号」隐私约束。
- 平台：FastAPI/Uvicorn/python-multipart 均为纯 Python wheel，跨平台与现有依赖一致。
- 性能：单工作线程串行执行；并发上传被排队；首个任务触发 OCR 模型加载 (~600MB 内存)，后续任务复用；前端通过 SSE 接收 `处理中 N/M - <key>` 行进度，平均延迟 < 1 秒。
- 兼容性：单进程运行（禁止 `--workers > 1`）；多 worker 部署需引入外部队列，超出本变更范围。
- 安全边界：仅监听 `127.0.0.1` 默认；`--host 0.0.0.0` 需用户显式指定；上传大小、文件数、`pdf_dpi` 在服务层强校验；不实现登录、不存储任何标识用户身份的字段。

## Research Summary for OPSX

### Discovered Constraints (Hard)

- **必须复用 `pipeline.run_merge()`**：禁止 fork 出独立处理路径，避免双套维护。
- **必须复用 `engine` 注入参数**：Web 层创建一次 `PaddleOcrEngine` 单例，注入到每次 `run_merge` 调用；禁止每请求新建。
- **不得在导入时加载 OCR 模型**：保持 CLI 与测试的快速冷启动；模型应在第一次任务触发或显式 lifespan 钩子内懒加载。
- **必须保持离线**：服务启动时调用 `ensure_ocr_ready(allow_download=False)`；模型缺失返回机器可读错误，不联网下载。
- **必须串行执行**：单工作线程消费全局队列；同一时刻最多一个 `run_merge` 在跑（PaddleOCR 线程安全未知，且单引擎重入风险高）。
- **`run_merge` 接受目录**：不能直接喂内存字节；上传必须先落到每任务工作目录，处理完按 retention 删除。
- **进度通过 stream 注入**：将自定义 `io.TextIOBase` 注入 `stdout=`，在内存中解析 `处理中 N/M - <key>` 与 stderr 警告，转 SSE 事件；不解析 stdout 字符串以外的渠道。
- **错误分类对齐 CLI**：`NoProcessableInputError` → 任务 `failed-no-input`；`OcrModelMissingError` → `failed-ocr-missing`；`FatalRunError` → `failed-fatal`；其他异常 → `failed-internal`。
- **必须保留 CLI 不变**：现有 `fapiao merge` 行为、参数、退出码、stdout/stderr 文本完全不动。

### Discovered Constraints (Soft)

- 前端必须零构建：单 HTML + 单 JS + 单 CSS，由 FastAPI `StaticFiles(directory="src/fapiao_pdf/web/static")` 托管；禁止 npm/Vite。
- 文案与 CLI 一致：错误/警告文案直接复用 CLI 中的中文字符串，避免重复维护翻译。
- 视觉风格：暗色 + 霓虹高光 + 玻璃拟态卡片 + 几何网格背景；CSS 自写或 UnoCSS Runtime CDN 二选一（设计阶段定）。
- 字体：系统等宽栈优先（`ui-monospace, "JetBrains Mono", "Cascadia Code", Consolas, monospace`），避免 Google Fonts 让离线部署可用。

### Dependencies

- 新增 pip 依赖：`fastapi`、`uvicorn`、`python-multipart`；归入 `web` 可选依赖组以便核心 CLI 用户跳过。
- Python 标准库依赖：`asyncio`、`threading`、`queue`、`tempfile`、`uuid`、`shutil`、`json`、`io`、`pathlib`。
- 与既有模块的接口依赖：`pipeline.run_merge` / `pipeline.ensure_ocr_ready` / `ocr.build_default_engine` / `ocr.OcrEngine`；不修改任何既有签名。
- 工具链：`pytest` 复用既有测试基础设施；`httpx` 作为 dev 依赖用于 `TestClient` 异步路径（已随 `fastapi[standard]` 间接可用）。

### Risks & Mitigations

- **多 Uvicorn worker 破坏单引擎假设** → `fapiao serve` 启动器锁死 `workers=1`，文档明确禁止反向代理后启动多进程。
- **PaddleOCR 非线程安全** → 引擎仅在工作线程中使用；FastAPI 路由不直接调用 `engine.recognize`。
- **长任务阻塞事件循环** → `run_merge` 在专用线程执行，路由立即返回 `task_id`；事件循环只处理 SSE 推送与小请求。
- **大 multipart 上传 OOM** → 使用 `UploadFile` 流式 spooling 写入磁盘；启动时 `--max-upload-mb` 强制总大小上限；超限返回 413。
- **磁盘膨胀** → 启动时清扫 `<tmp>/fapiao-tasks/`；后台清理线程每 5 分钟扫描并删除 `created_at + retain_minutes < now()` 的任务。
- **进度行解析脆弱** → 在 `stats.py` 既有格式 `处理中 N/M - <key>` 上提供正则；CLI 文案变更需同步更新解析器（在 `tests/test_web_progress.py` 中绑定双向断言）。
- **进程崩溃失任务状态** → 任务元数据（state、progress、created_at）以 JSON 持久化到任务目录；服务重启后扫描目录，把仍 `running` 的任务标记 `failed-restart`。
- **下载与清理竞态** → 清理删除前检查最近 60 秒内是否有活动下载请求（通过任务对象上的 `last_download_at` 时间戳）；保守不删，等下个清扫周期。
- **公网部署滥用** → 默认 `--host 127.0.0.1`；显式开启公网时在终端打印安全提示与建议反代鉴权。

### Success Criteria (Verifiable)

- 启动：`fapiao serve` 在 3 秒内监听 `127.0.0.1:8000`，OCR 模型不在启动时加载。
- 健康检查：`GET /api/health` 返回 `{"ok": true, "version": <__version__>}`。
- 上传：`POST /api/tasks` 接受 multipart 混合 jpg/png/pdf，返回 `202 + {"task_id": "<uuid>"}`；超过 `--max-upload-mb` 返回 413。
- 状态：`GET /api/tasks/{id}` 返回 `{state: queued|running|done|failed-*, progress: {current, total, key}, summary?: {...}}`。
- 进度：`GET /api/tasks/{id}/events` SSE 流，平均推送延迟 < 1 秒，事件包含 `progress` / `warning` / `done` / `error`。
- 串行：连续两次提交，第二个任务在第一个 `done` 之前始终为 `queued` 或 `running` 后启；任意时刻 `running` 任务数 ≤ 1。
- 下载：`GET /api/tasks/{id}/result` 返回 `application/pdf`、`Content-Disposition: attachment; filename="fapiao-<id>.pdf"`，与 CLI `merge` 输出一致（同样输入字节级一致或仅时间戳差异）。
- 错误：缺失 OCR 模型时上传立即返回 503 + `{"error": "OcrModelMissing"}`；空目录上传返回任务 `failed-no-input`。
- 清理：手工修改任务 `created_at` 提前 65 分钟后，下一次清扫周期内任务目录被删除；`GET` 该任务返回 404。
- CLI 无回归：原 `tests/test_cli.py` / `tests/test_pipeline_e2e.py` 全部保持通过。
- 离线友好：断网情况下完成完整工作流（启动→上传→进度→下载）。
- 前端：浏览器打开 `/` 看到暗色科技风界面；可拖拽上传；可显示进度条；下载链接出现；刷新页面后通过 URL 参数 `?task=<id>` 恢复轮询。

### User Confirmations

- 后端框架：FastAPI。
- 前端形态：单页 HTML + 原生 JS（零构建）。
- 并发模型：全局任务队列 + 串行执行，单 OCR 引擎实例。
- 用户隔离：随机 task_id + 处理完保留 1 小时自动清理。
- 视觉风格：暗色 + 科技感（具体玻璃拟态/霓虹细节在 `/ccg:spec-plan` 阶段定）。
