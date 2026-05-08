# CLAUDE.md — src/fapiao_pdf/

> fapiao 主实现包；15 个模块按职责拆分（SRP），CLI/编排/IO 与纯函数分层。
>
> 父目录：[../../CLAUDE.md](../../CLAUDE.md)

## 1. 模块清单

| 模块 | 行数 | 职责 | 对应测试 |
|---|---|---|---|
| `__init__.py` | 1 | `__version__` 单一来源 | `test_version.py` |
| `cli.py` | 222 | Typer 入口、参数校验、覆盖确认、退出码映射 | `test_cli.py` |
| `pipeline.py` | 242 | 端到端编排，注入式 OCR 引擎 | `test_pipeline_e2e.py` |
| `models.py` | 71 | 数据类（`LogicalInput` / `ProcessedDocument` / `LayoutPage` 等） | （间接） |
| `scanner.py` | 41 | 递归扫描，跳过符号链接、特殊文件，确定性排序 | `test_scanner.py` |
| `pdf_pages.py` | 79 | PyMuPDF 拆页，加密检测，DPI 校验 | （e2e） |
| `image_io.py` | 32 | Pillow 加载 + EXIF 转置 + JPEG 编码 | （e2e） |
| `splitter.py` | 149 | OpenCV 多票据切分，几何阈值约束，IoU 去重 | `test_splitter.py` |
| `ocr.py` | 218 | `OcrEngine` Protocol、PaddleOCR 适配、Fake 引擎 | `test_ocr.py` |
| `classifier.py` | 34 | 关键词分类（发票优先），未知降级 | `test_classify_date_order.py` |
| `date_parser.py` | 20 | 4 种日期格式正则，最早有效日历日期 | `test_classify_date_order.py` |
| `ordering.py` | 21 | 类型→有日期→日期→display_key 排序 | `test_classify_date_order.py` |
| `layout.py` | 132 | A4 几何 + 槽位规划 + 等比缩放居中 | `test_layout.py` |
| `renderer.py` | 91 | ReportLab canvas，临时文件 + `os.replace` 原子写入 | （e2e） |
| `stats.py` | 73 | TTY/非 TTY 进度、stderr 警告、摘要模板 | `test_stats.py` |

## 2. 入口与导出符号

### `cli.py::app`

- Typer 应用，注册 `init` / `merge` 子命令
- 全局选项：`--version` / `-V`
- `merge` 选项：`--output/-o`、`--force`、`--pdf-dpi`、`--workers`
- 关键私有函数：`_resolve_force` / `_validate_pdf_dpi` / `_validate_workers` / `_resolve_merge_paths`

### `pipeline.py`

| 公开符号 | 说明 |
|---|---|
| `RunStats` | 摘要数据类（processed/invoices/orders/ocr_failures/output_path） |
| `NoProcessableInputError` | 无可处理文件 → CLI 退出 1 |
| `OcrModelMissingError` | OCR 模型缺失 → CLI 退出 2 |
| `FatalRunError` | 渲染失败/参数错误 → CLI 退出 2 |
| `run_merge(input_dir, output, *, force, pdf_dpi, workers, engine=None, stdout=None, stderr=None)` | 主编排函数；`engine` 参数允许测试注入 `FakeOcrEngine` |
| `ensure_ocr_ready(*, allow_download)` | 转发到 `ocr.ensure_ocr_ready`；模型缺失抛 `OcrModelMissingError` |

### `ocr.py`

| 公开符号 | 说明 |
|---|---|
| `OcrEngine`（Protocol） | `recognize(image: Image.Image) -> OcrResult` |
| `PaddleOcrEngine` | 生产实现，懒加载 `paddleocr` |
| `FakeOcrEngine` | 测试桩；接受 `responder: Callable[[Image], str]` |
| `OcrModelMissingError` | 模型不可用 |
| `ensure_ocr_ready(*, allow_download)` | 校验缓存；`merge` 时禁联网 |
| `build_default_engine()` | `ensure_ocr_ready(False) → PaddleOcrEngine` |
| `_extract_text(prediction)` | 适配 PaddleOCR 3.x 嵌套结构 `result[0].json['res']['rec_texts']` |
| `_build_paddleocr_kwargs(cfg)` | 默认 mobile 模型 + 关闭辅助子模型 + `run_mode=paddle` |

模块顶层强制设置 `FLAGS_use_onednn=0`、`FLAGS_enable_pir_api=0`，必须在 `import paddle` 前生效。

### `models.py`

- `LogicalInput(path, display_key, doc_type)`：扫描产物
- `SplitCrop(image, bbox, display_key)`、`SplitDocument`：切分产物
- `OcrResult(text, orientation_corrected, success, error)`：OCR 输出
- `ProcessedDocument(original, image, doc_type, date, ocr_failure, warnings)`：业务文档
- `LayoutSlot(document, slot_index, page_type)`、`LayoutPage(page_num, slots)`：版面
- `WarningEntry`：保留供未来结构化警告

### `layout.py`

| 符号 | 说明 |
|---|---|
| 常量 | `A4_WIDTH_MM=210`、`A4_HEIGHT_MM=297`、`PAGE_MARGIN_MM=10`、`INVOICE_GAP_MM=5`、`INVOICE_PER_PAGE=2`、`ORDER_PER_PAGE=1` |
| `mm_to_pt(value_mm)` | 单位转换；ReportLab 用 pt |
| `SlotRect` / `PlacedImage` | 槽位与最终放置矩形 |
| `plan_pages(docs)` | 纯函数；按类型分组分页 |
| `slot_rects_for_page(page)` | 计算页面槽位几何 |
| `fit_into_slot(w, h, slot)` | 等比缩放居中 |

### `renderer.py`

- `render_pdf(pages, output)`：原子写入；ReportLab 原点位于左下，做 Y 轴翻转
- `RenderError`：异常时清理临时文件，不替换最终文件

### `stats.py`

- `aggregate(docs) -> StatsSnapshot`、`format_summary(snapshot, output)`
- `ProgressReporter(total, stream=None)`：TTY 用 `\r`，非 TTY 用换行
- `emit_warning(message, stream=None)`：默认 stderr

## 3. 内部依赖关系

```
cli.py
 └── pipeline.py
      ├── scanner.py        (Path → List[Path])
      ├── pdf_pages.py      (Path → Iterator[RenderedPdfPage])
      ├── image_io.py       (Path → PIL.Image)
      ├── splitter.py       (Image, Path → List[SplitCrop] | None)
      ├── ocr.py            (Image → OcrResult)
      ├── classifier.py     (str → DocType + warning)
      ├── date_parser.py    (str → date | None)
      ├── ordering.py       (List[ProcessedDocument] → List[...])
      ├── layout.py         (List[ProcessedDocument] → List[LayoutPage])
      ├── renderer.py       (List[LayoutPage], Path → None)
      └── stats.py          (List[ProcessedDocument] → StatsSnapshot)

models.py 被几乎所有模块导入；layout/ordering/stats 直接消费 ProcessedDocument
```

`models.py` 是唯一对所有模块开放的"共享类型层"；其他模块互相之间通过 pipeline 编排，避免横向依赖。

## 4. 外部依赖与版本要求

| 库 | 用途 | 适配位置 |
|---|---|---|
| `typer` | CLI | `cli.py` |
| `paddleocr` ≥3.5 | OCR 推理 | `ocr.py`（懒加载） |
| `paddlepaddle` ≥3.3 (CPU) | OCR 后端 | `ocr.py`（强制 `run_mode=paddle`） |
| `pymupdf` | PDF 拆页 | `pdf_pages.py` |
| `pillow` | 图像加载 | `image_io.py`、`splitter.py`、`renderer.py` |
| `opencv-python-headless` | 多票据切分 | `splitter.py` |
| `reportlab` | PDF 输出 | `renderer.py` |
| `numpy` | OCR/Splitter 数组 | `ocr.py`、`splitter.py` |

## 5. 关键算法说明

### 5.1 splitter 接受标准（5 个阈值）

| 常量 | 值 | 含义 |
|---|---|---|
| `_MIN_REGION_COUNT` | 2 | 至少 2 个候选才不回退 |
| `_MIN_AREA_RATIO` / `_MAX_AREA_RATIO` | 8% / 95% | 单候选面积占整页 |
| `_MIN_DIMENSION_RATIO` | 15% | 宽高均需 > 整页 15% |
| `_MIN_ASPECT_RATIO` / `_MAX_ASPECT_RATIO` | 0.25 / 4.0 | 长宽比 |
| `_MAX_IOU` | 0.2 | 候选两两 IoU 阈值 |

### 5.2 排序键

```
(type_priority, has_date, sort_date, display_key)
   0=invoice 1=order   0=有 1=无   有日期或 date.max     字典序兜底
```

### 5.3 OCR 文本提取（PaddleOCR 3.x 适配）

按层级降级查找：
```
result[0].json['res']['rec_texts']    # PaddleOCR 3.x 主路径
result[0].json['rec_texts']           # 旧版顶层
result[0].rec_texts                   # 对象属性
```

## 6. 修改注意事项

1. **改 `models.py` 字段** → 影响层多模块；用 `grep -r "ProcessedDocument" tests/` 全量回归
2. **改 OCR 引擎签名** → 同步 `FakeOcrEngine` 与 `test_pipeline_e2e.py::_fake_engine_with_corpus`
3. **改 layout 常量** → 同步 `test_layout.py` 期望与 `README.md::5.2`
4. **改 cli 参数** → 同步 `pyproject.toml::project.scripts`、`tests/test_cli.py`、`README.md::4`
5. **新增外部依赖** → 同步 `pyproject.toml`、`README.md::2`、本文件 §4
