# CLAUDE.md — tests/

> pytest + Hypothesis；230 passed, 1 skipped。覆盖纯函数单元 + 注入式 OCR 端到端 + Web 路由/SSE/串行/清理。
>
> 父目录：[../CLAUDE.md](../CLAUDE.md)

## 1. 测试组织

| 文件 | 用例数（粗估） | 类型 | 覆盖模块 |
|---|---|---|---|
| `test_version.py` | 2 | 元数据 | `__init__.py`、`pyproject.toml` |
| `test_cli.py` | 9 | CLI 行为 | `cli.py` 全部分支（参数/交互/--force/--help） |
| `test_scanner.py` | 5 | 单元 | `scanner.py` |
| `test_splitter.py` | 5（含 1 Hypothesis） | 单元 + PBT | `splitter.py` |
| `test_classify_date_order.py` | 13（含 2 Hypothesis） | 单元 + PBT | `classifier.py`、`date_parser.py`、`ordering.py` |
| `test_layout.py` | 7（含 1 Hypothesis） | 单元 + PBT | `layout.py` |
| `test_ocr.py` | 6 | 单元 | `ocr.py`（含 Fake、ensure_ocr_ready） |
| `test_stats.py` | 4 | 单元 | `stats.py` |
| `test_pipeline_e2e.py` | 6 | 集成 | `pipeline.py` 全链路（注入 FakeOcrEngine） |
| `test_web_config.py` | 8 | 单元 | `web/config.py`、`web/errors.py` |
| `test_web_errors.py` | 12 | 单元 | `web/errors.py` 异常映射 |
| `test_web_tasks.py` | 24 | 单元 | `web/tasks.py` TaskStore + 持久化 |
| `test_web_progress.py` | 26 | 单元 | `web/progress.py` 行解析 + EventBus + SSE |
| `test_web_queue.py` | 5 | 集成 | `web/queue.py` SerialMergeExecutor |
| `test_web_cleanup.py` | 10 | 单元 | `web/cleanup.py` 周期清扫 + placeholder TTL |
| `test_web_app.py` | 11 | 集成 | `web/app.py` 6 路由 + lifespan（TestClient） |
| `test_web_restart.py` | 5 | 集成 | 重启恢复 → `failed-restart` |
| `test_web_properties.py` | 6 | PBT | Hypothesis 跨模块不变量（详见 §5） |
| `test_cli_serve.py` | 10 | CLI | `cli.py::serve` 参数 + 安全提示 + 退出码 |

## 2. 运行命令

```bash
# 全量
.venv/Scripts/python.exe -m pytest tests/ -q

# 仅纯函数（最快）
.venv/Scripts/python.exe -m pytest tests/test_classify_date_order.py tests/test_layout.py tests/test_stats.py -q

# 仅 e2e（含真实 PIL/PyMuPDF）
.venv/Scripts/python.exe -m pytest tests/test_pipeline_e2e.py -q

# 单测
.venv/Scripts/python.exe -m pytest tests/test_cli.py::test_force_overwrites_existing_output -v
```

## 3. 关键测试夹具与桩

### 3.1 `FakeOcrEngine`（来自 `fapiao_pdf.ocr`）

```python
from fapiao_pdf.ocr import FakeOcrEngine

# 按图像尺寸映射文本，可控
engine = FakeOcrEngine(lambda img: f"发票号码 {img.width}\n2024-05-01")
```

### 3.2 e2e 工具

`test_pipeline_e2e.py::_make_image` / `_make_pdf` / `_fake_engine_with_corpus` 用 Pillow + PyMuPDF 即时生成测试输入；同尺寸图片可由 corpus 字典差异化映射文本。

### 3.3 CLI 测试 Runner

```python
try:
    runner = CliRunner(mix_stderr=False)        # click <8.2
except TypeError:
    runner = CliRunner()                          # click ≥8.2
```

## 4. 已知特殊情况

| 项 | 说明 |
|---|---|
| `test_scanner.py::test_skip_symlinks` | 平台限制 skipped（Windows 无 root 时） |
| `test_splitter.py::test_split_page_generated_boxes_do_not_overlap` | Hypothesis；用 `suppress_health_check=[function_scoped_fixture]` + `max_examples=15` 控制成本；允许低置信回退（`crops is None` + `warn` 非空） |
| `test_pipeline_e2e.py::test_end_to_end_render_failure_does_not_replace_existing_output` | 关键不变量：渲染失败时不替换已有文件 |

## 5. 测试性质（property-based）

| 测试 | 不变量 |
|---|---|
| `test_date_parser_total_on_well_formed_strings` | 任意 (year, month, day) → 解析与 `datetime.date(...)` 等价或同时为 `None` |
| `test_sort_is_deterministic_under_shuffle` | 任意输入顺序洗牌后排序结果一致 |
| `test_fit_preserves_aspect_and_stays_inside_slot` | 任意图片尺寸 fit 后必在槽内、保持长宽比 |
| `test_split_page_generated_boxes_do_not_overlap` | 切分成功路径下 IoU ≤ 0.2 |

## 6. 添加新测试的约定

1. **纯函数** → 直接 unit + 适当 PBT；不依赖 PIL/PaddleOCR
2. **涉及图像** → 用 `Image.new("RGB", size, "white")` 构造最小输入
3. **涉及 OCR** → 用 `FakeOcrEngine`，禁止真实 PaddleOCR
4. **涉及 PDF** → 用 `pymupdf.open()` + `doc.new_page()` 构造
5. **涉及文件路径** → 用 `tmp_path` fixture
6. **避免** 像素级 PDF 对比；只断言页数、A4 尺寸（595×842 pt 容差 ±5）、关键不变量
