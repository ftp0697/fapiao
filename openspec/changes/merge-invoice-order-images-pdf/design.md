## Context

当前仓库为规格优先的绿地项目：已有 `proposal.md`，但没有应用源码、测试、打包配置或既有 OpenSpec 能力。目标是实现一个离线运行的 Python CLI，将本地发票与订单图片/PDF 识别、分组、排序并合并为 A4 可打印 PDF。

已确认约束：

- Python 基线：Python 3.11+，使用 `pyproject.toml` 与 `src/` layout。
- 输出顺序：先发票组，再订单组；每组内部按票面日期升序排序。
- 无日期票据：排在所属类型组尾，并按相对路径与页/切片索引排序。
- 离线语义：`merge` 处理图片时不上传图片、不依赖远程 API；OCR 模型可通过安装或 `fapiao init` 预先准备。
- 技术栈：Typer、PaddleOCR、PyMuPDF、Pillow、ReportLab、OpenCV headless、pytest、Hypothesis。
- PDF 拆页 DPI：`--pdf-dpi` 可配置，默认 200，允许范围 100-300。
- 部分文件失败：只要最终 PDF 成功写出，进程退出码为 0，失败明细进入 stderr 与统计摘要。
- CLI 范围：MVP 包含 `fapiao init` 与 `fapiao merge`。
- 图片方向：先应用 EXIF Orientation，再在 OCR 支持且结果可信时做方向校正；失败则保留原图并警告。
- 多票据页：使用 OpenCV 做轻量自动切分；低置信或检测失败时回退为整页处理并警告。
- 特殊文件：跳过符号链接、加密 PDF、非普通文件，并输出中文警告。
- OCR 失败计数：OCR 抛异常或识别结果为空文本均计入 OCR 失败。

外部依据：PaddleOCR 文档显示 Python `PaddleOCR(...).predict()` 支持本地 OCR、中文模型与方向相关开关；PyMuPDF 文档支持 `Page.get_pixmap(dpi=...)` 渲染 PDF 页；Pillow 文档支持 `Image.open(Path)`、上下文关闭和 `ImageOps.exif_transpose`；ReportLab 文档支持 A4 canvas 与坐标绘制；OpenCV 文档支持 threshold、findContours、boundingRect/ROI crop；Hypothesis 文档支持 pytest 下基于策略生成反例。

## Goals / Non-Goals

**Goals:**

- 提供可安装的 `fapiao` Python CLI。
- 支持 `fapiao init` 预下载/验证 OCR 模型。
- 支持 `fapiao merge [input_dir] -o output.pdf [--force] [--pdf-dpi 200] [--workers 1]`。
- 支持无参数 `fapiao merge` 的中文交互模式。
- 递归扫描 `.jpg` / `.jpeg` / `.png` / `.pdf`，大小写不敏感。
- 将 PDF 每页渲染为逻辑页面，再对逻辑页面进行可选多票据切分。
- 对原始图片和 PDF 页面切片执行 OCR、类型识别、日期解析与方向校正。
- 发票组先输出；发票每页最多两张，上下排列；订单每页一张。
- 使用 A4 竖版、10mm 页边距、发票上下间隙 5mm。
- 对单文件错误做降级或跳过，不中断批处理。
- 用 pytest + Hypothesis 覆盖核心不变量。

**Non-Goals:**

- 不提供 GUI、Web 服务或云 OCR。
- 不导出 CSV/Excel，不做发票去重。
- 不在本轮实现用户自定义关键词配置文件。
- 不保证复杂重叠、多列粘连、遮挡票据的完美自动切分；低置信时回退整页。
- 不做像素级 golden PDF 对比测试。
- 不处理需要密码的加密 PDF。

## Decisions

### D1. 项目结构

采用 Python 3.11+、`pyproject.toml`、`src/fapiao_pdf/`、`tests/`。核心模块按职责拆分：`cli`、`models`、`scanner`、`pdf_pages`、`image_io`、`splitter`、`ocr`、`classifier`、`date_parser`、`ordering`、`layout`、`renderer`、`stats`。

替代方案：单文件脚本。拒绝原因：OCR、PDF、切分、排版和测试边界复杂，单文件会破坏 SRP 与可测试性。

### D2. CLI

使用 Typer 暴露两个命令：

- `fapiao init`：初始化/验证 PaddleOCR 模型缓存，可联网。
- `fapiao merge [input_dir] -o output.pdf --force --pdf-dpi 200 --workers 1`：执行处理。

`merge` 无 `input_dir` 或 `output` 时进入中文交互提示。已存在输出文件：参数模式必须显式 `--force`；交互模式确认覆盖，接受 `y/yes/是/确认`，默认拒绝。

替代方案：argparse。拒绝原因：Typer 对子命令、提示、帮助与测试更直接，符合可维护优先。

### D3. 输入扫描与逻辑文档模型

扫描使用 `pathlib`，跳过符号链接和非普通文件。每个输入转为 `LogicalInput`：图片为 `relative_path`，PDF 页为 `relative_path#page=0001`，切片为 `relative_path#page=0001#crop=0001`。该 display key 用于警告、排序兜底和测试。

加密 PDF 直接跳过并输出「加密PDF不支持，已跳过：<路径>」。无法打开的 PDF 跳过整个文件；单页渲染失败时跳过该页。

### D4. PDF 拆页与图片加载

PDF 使用 PyMuPDF 渲染，`--pdf-dpi` 默认 200，合法范围 100-300。图片使用 Pillow 打开，立即 `load()` 后关闭文件句柄，并应用 `ImageOps.exif_transpose`。

替代方案：pdf2image/Poppler。拒绝原因：Windows 外部二进制安装成本更高。

### D5. 多票据页自动切分

切分发生在 OCR 前，对原始图片和 PDF 渲染页统一处理。使用 OpenCV headless：灰度化、去噪、阈值/自适应阈值、轮廓检测、轴对齐 bounding box、2% padding crop。

接受切片需满足：

- 数量至少 2；否则回退整页。
- 每个候选区域面积占整页 8%-95%。
- 宽高均大于整页对应维度的 15%。
- 宽高比在 0.25-4.0 之间。
- 候选区域两两 IoU 不超过 0.2；超过则合并或回退整页。

切片排序为从上到下、同一行从左到右。若检测低置信、候选冲突或异常，回退整页并输出中文警告。MVP 不做透视校正和机器学习目标检测。

### D6. OCR 与方向校正

OCR 通过接口封装 PaddleOCR。默认中文/英文场景，使用本地模型。`merge` 不主动下载模型；模型缺失时失败并提示运行 `fapiao init`。OCR 返回文本流、可选方向信息和错误状态。

方向处理顺序：Pillow EXIF 校正 → OCR 方向可信时旋转 → OCR 方向不可用时保持现状并警告。OCR 异常或空文本计入 OCR 失败；该票据降级为 `order`，日期为空，继续参与排序。

### D7. 类型识别

分类为纯函数。发票关键词优先：`发票`、`税额`、`价税合计`、`发票号码`、`发票代码`。订单关键词：`订单`、`订单号`、`订单编号`、`商品清单`、`收货地址`。任何发票关键词命中即为 `invoice`；否则订单关键词命中为 `order`；否则降级为 `order` 并警告。

MVP 关键词以常量定义，不引入配置文件。

### D8. 日期解析与排序

支持日期格式：`YYYY-MM-DD`、`YYYY/MM/DD`、`YYYY.MM.DD`、`YYYY年MM月DD日`，月/日允许 1-2 位。解析时收集所有候选，校验真实日历日期，选择 OCR 文本流中最早出现的有效日期。无有效日期时日期为空。

排序规则：

1. 类型组顺序：`invoice` → `order`。
2. 组内有日期项先按日期升序。
3. 组内无日期项在组尾。
4. 同日期或同为无日期时按 display key 字典序。

### D9. A4 排版与 PDF 输出

布局先由纯函数生成 `LayoutPage`，再由 ReportLab 渲染。页面固定 A4 竖版 210mm × 297mm。边距 10mm。发票页最多 2 张，上下 cell，中间 5mm gap；订单页 1 张，占可打印区域。所有图片等比缩放并居中，不能超出 cell。

输出写入同目录临时文件，成功关闭 PDF 后再原子替换最终文件。Ctrl+C/SIGTERM 时清理临时文件，不替换最终文件。

替代方案：完全使用 PyMuPDF 输出。拒绝原因：ReportLab 的坐标与 A4 版式表达更直接，测试布局更清晰。

### D10. 进度、警告与退出码

stdout：进度与最终摘要。非 TTY 降级为简单文本进度。stderr：中文警告，不输出 OCR 文本、金额、税号、身份信息等敏感字段。

退出码：

- `0`：PDF 成功生成，即使有部分文件失败。
- `1`：未发现支持文件或所有文件均无法处理，没有生成 PDF。
- `2`：参数错误、输出写入失败、模型缺失等致命错误，没有生成 PDF。
- `130`：用户中断。

## Risks / Trade-offs

- PaddleOCR 依赖重、模型下载慢 → 用 `fapiao init` 显式初始化，文档说明运行期离线边界。
- OpenCV 自动切分存在误判 → 仅采用保守规则；低置信回退整页并警告。
- OCR 文本顺序不等同视觉顺序 → 日期规则固定为 OCR 文本流最早匹配，保持确定性。
- 大图或高 DPI PDF 占内存 → DPI 默认 200 且限制 100-300；处理后及时关闭/释放图像；`--workers` 默认 1，上限 4。
- PDF 输出中断可能损坏文件 → 同目录临时文件 + 成功后原子替换。
- 测试 PDF 像素差异跨平台不稳定 → 验证页数、页面尺寸、布局计划和关键行为，不做像素级断言。

## PBT Properties

- 扫描不变量：支持扩展名大小写不敏感；不支持扩展名永不进入处理队列。
- 身份不变量：PDF 页和切片 display key 唯一且稳定。
- 分类不变量：发票关键词优先；未知文本降级为订单。
- 日期不变量：只接受真实日历日期；多个日期选择 OCR 文本流中最早位置。
- 排序不变量：任意输入顺序洗牌后，输出顺序一致；发票组总在订单组之前；无日期项在组尾。
- 切分不变量：低于 2 个高置信区域时回退整页；高置信切片互不重叠且按上到下/左到右排序。
- 排版不变量：发票页 1-2 张；订单页恰好 1 张；无混合类型页；图片放置不越界且保持宽高比。
- 输出不变量：渲染失败或中断时最终输出文件不被替换。
- 统计不变量：成功保留票据数 = 发票数 + 订单数；OCR 失败数 = 异常数 + 空文本数。

## Open Questions

无。所有实现前决策已在本设计中固化。
