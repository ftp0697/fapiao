# fapiao

离线命令行工具：递归扫描本地票据图片与 PDF，识别发票/订单类型与日期，合并为可打印的 A4 竖版 PDF。

- 全程离线（merge 阶段）：不上传图片、不调用云 OCR
- 类型分组：先发票后订单，不同类型不混排
- 自动版面：发票每页最多两张上下排列，订单每页一张
- 健壮降级：损坏图片、加密 PDF、单页渲染失败不会中断整体批处理
- 双形态：CLI 一次性合并 + 可选本地 Web UI（FastAPI + 拖拽 + SSE 进度，详见 §13）

## 1. 系统要求

| 项 | 要求 |
|---|---|
| 操作系统 | Windows 10+ / macOS / Linux |
| Python | **3.11 – 3.13**（PaddlePaddle 3.x 暂无 cp314 wheel） |
| 磁盘 | 模型缓存约 600 MB |
| CPU | x86-64 推荐；ARM64 性能受限 |
| 网络 | 仅 `fapiao init` 阶段需要联网 |

## 2. 安装

### 2.1 创建虚拟环境

```bash
py -3.13 -m venv .venv
.venv/Scripts/activate                  # Windows
# source .venv/bin/activate             # macOS / Linux
```

### 2.2 安装 fapiao

```bash
pip install -e .
```

### 2.3 安装 PaddlePaddle 与 PaddleOCR

PaddlePaddle CPU 版需使用官方镜像：

```bash
pip install paddlepaddle -i https://www.paddlepaddle.org.cn/packages/stable/cpu/
pip install paddleocr --prefer-binary
```

> Windows 下若安装 `python-bidi` 失败，加 `--prefer-binary` 强制使用预编译 wheel，避免本地编译 Rust。

## 3. 首次初始化

```bash
fapiao init
```

首次执行会下载 PP-OCRv5 中文检测/识别 + 方向分类模型（约 250 MB）到：

- Windows / macOS / Linux：`~/.paddlex/official_models/`

成功输出：

```
OCR 模型已就绪。
```

## 4. 使用

### 4.1 参数模式

```bash
fapiao merge <input_dir> -o <output.pdf> [--force] [--pdf-dpi 200] [--workers 1]
```

| 参数 | 说明 | 默认 | 取值 |
|---|---|---|---|
| `input_dir` | 输入目录，递归扫描 | 必填 | 任意可读目录 |
| `-o, --output` | 输出 PDF 路径 | 必填 | 任意可写路径 |
| `--force` | 覆盖已存在输出 | 关闭 | 标志位 |
| `--pdf-dpi` | PDF 输入页渲染 DPI | `200` | `100..300` |
| `--workers` | 并行工作线程数 | `1` | `1..4` |

示例：

```bash
fapiao merge ./receipts -o ./out.pdf --force --pdf-dpi 220
```

### 4.2 交互模式

不带任何参数运行：

```bash
fapiao merge
请输入输入目录路径: ./receipts
请输入输出 PDF 路径: ./out.pdf
```

输出文件已存在时提示覆盖；接受 `y` / `yes` / `是` / `确认`，其余视为拒绝。

### 4.3 全局命令

```bash
fapiao --version       # 显示版本
fapiao --help          # 显示帮助
fapiao init            # 初始化或校验 OCR 模型
```

## 5. 输入与输出

### 5.1 支持的输入

- 图片：`.jpg` / `.jpeg` / `.png`（大小写不敏感）
- PDF：每页作为独立票据参与处理
- 多票据图：自动 OpenCV 切分，低置信度回退整页

跳过项（仅警告不中断）：

- 符号链接、特殊文件
- 加密 PDF
- 损坏图片或无法解码的 PDF 页

### 5.2 输出版面

- A4 竖版 210 × 297 mm，10 mm 页边距
- 发票页：1 张占满，或 2 张上下排列（中间 5 mm 间隔）
- 订单页：每页 1 张
- 等比缩放并居中，不超出可打印区域
- 不同类型不混排

### 5.3 排序规则

1. 类型组：发票 → 订单
2. 组内：有日期项按日期升序在前，无日期项在尾
3. 同日期或同为无日期：按文件相对路径字典序

## 6. 类型识别与日期解析

### 6.1 关键词

| 类型 | 命中关键词（任一即可） |
|---|---|
| 发票（优先） | `发票`、`税额`、`价税合计`、`发票号码`、`发票代码` |
| 订单 | `订单`、`订单号`、`订单编号`、`商品清单`、`收货地址` |
| 未识别 | 降级为订单并输出警告 |

发票关键词优先级最高，与订单关键词同时命中时按发票处理。

### 6.2 日期格式

以下四种格式均支持，月/日允许 1-2 位：

| 示例 | 说明 |
|---|---|
| `2024-03-15` | 短横线 |
| `2024/3/15` | 斜杠 |
| `2024.03.15` | 点号 |
| `2024年3月15日` | 中文 |

仅接受真实日历日期；多个候选取 OCR 文本流中**最早出现**的有效日期。

## 7. 退出码

| 码 | 含义 |
|---|---|
| `0` | PDF 成功生成（即使有部分文件失败） |
| `1` | 未发现支持文件，或所有文件均无法处理 |
| `2` | 参数错误 / 输出写入失败 / OCR 模型缺失 / 致命错误 |
| `130` | 用户 Ctrl+C 中断 |

## 8. 环境变量

| 变量 | 默认 | 用途 |
|---|---|---|
| `PADDLE_OCR_CACHE_DIR` | 内置二级回退路径 | 覆盖 PaddleOCR 模型缓存目录 |
| `FLAGS_use_onednn` | `0`（fapiao 强制） | 禁用 oneDNN，绕过 PaddlePaddle 3.3+ PIR 不兼容 |
| `FLAGS_enable_pir_api` | `0`（fapiao 强制） | 关闭 PIR API 加速路径 |
| `FAPIAO_OCR_MODEL` | `mobile`（默认） | 设为 `server` 切换 PP-OCRv5_server 大模型，精度更高但 CPU 推理慢约 7× |

仅当确知 PaddlePaddle 后续版本修复 oneDNN 兼容时，可显式设置 `FLAGS_use_onednn=1` 启用加速。

## 9. 进度与日志

- **stdout**：进度行 `处理中 N/M - <display-key>` 与最终摘要
- **stderr**：中文警告（OCR 失败、加密 PDF、切分回退、损坏图片等）
- **隐私**：日志不会输出 OCR 文本、金额、税号、身份证等敏感字段

最终摘要格式：

```
共处理 N 张，发票 X，订单 Y，OCR 失败 Z，输出至 <path>
```

## 10. 故障排查

### 10.1 `OCR 模型未就绪，请先运行 fapiao init`

模型缓存目录无 `*.pdiparams` 文件。检查：

```bash
ls ~/.paddlex/official_models/
```

应包含 `PP-OCRv5_server_det/`、`PP-OCRv5_server_rec/` 等子目录。若无，执行 `fapiao init` 重新下载。

### 10.2 `(Unimplemented) ConvertPirAttribute2RuntimeAttribute`

PaddlePaddle 3.3+ PIR 执行器与 oneDNN 不兼容。fapiao 已默认禁用，若仍出现：

1. 确认未在父进程设置 `FLAGS_use_onednn=1`
2. 降级 PaddlePaddle：`pip install paddlepaddle==3.2.0 -i https://www.paddlepaddle.org.cn/packages/stable/cpu/`

### 10.3 `python-bidi` 编译失败

Windows 缺 MSVC Build Tools。改用预编译 wheel：

```bash
pip install paddleocr --prefer-binary
```

### 10.4 加密 PDF 被跳过

输出警告 `加密PDF不支持，已跳过：<路径>`。当前版本不支持密码 PDF；先用其他工具解密后再处理。

### 10.5 多票据图被识别为单张

OpenCV 切分基于轮廓与几何约束，复杂背景或重叠票据可能回退整页。提高扫描分辨率、增加票据间空白可改善检出率。

## 11. 约束与不支持

- 不提供原生 GUI 客户端；不提供云服务（Web 模式仅作为本机使用，详见 §13）
- 不支持自定义关键词配置文件（关键词为内置常量）
- 不支持密码加密 PDF
- 不导出 CSV / Excel，不做发票去重
- 不保证复杂遮挡、粘连票据的完美自动切分

## 12. 开发与测试

```bash
.venv/Scripts/python.exe -m pytest tests/ -q
```

期望 230 通过、1 跳过（平台特性，含 Web 模式 ~150 项）。

样例生成：

```bash
.venv/Scripts/python.exe scripts/gen_samples.py samples
fapiao merge samples -o samples_out.pdf --force
```

## 13. Web 模式

启动本地 Web 服务（FastAPI + 单工作线程，复用 `pipeline.run_merge`）：

```bash
# 默认监听 127.0.0.1:8000
.venv/Scripts/fapiao.exe serve
# 自定义端口与配额
.venv/Scripts/fapiao.exe serve --host 127.0.0.1 --port 9000   --retain-minutes 60 --max-upload-mb 200 --max-files 200 --max-single-file-mb 50
```

打开浏览器访问 `http://127.0.0.1:8000`，拖拽文件 → 自动合并 → SSE 实时进度 → 下载 PDF。

### 13.1 端点矩阵

| 方法 | 路径 | 状态码 |
|---|---|---|
| `POST` | `/api/tasks` | 202 / 400 / 413 / 422 / 503 / 507 |
| `GET` | `/api/tasks/{id}` | 200 / 404 |
| `GET` | `/api/tasks/{id}/events` | 200 SSE / 404 / 410 / 429 |
| `GET` | `/api/tasks/{id}/result` | 200 PDF / 404 / 409 / 410 |
| `DELETE` | `/api/tasks/{id}` | 204 / 404 / 409 |
| `GET` | `/api/health` | 200 |

### 13.2 安全提示

- 默认仅监听 `127.0.0.1`，不暴露至局域网。
- 如使用 `--host 0.0.0.0` 或非回环地址，CLI 会打印安全警告：**Web 服务可能暴露给局域网或公网，请确认部署侧已加反向代理鉴权**。
- 强制 `uvicorn.run(workers=1)`，忽略 `WEB_CONCURRENCY` 环境变量；OCR 引擎进程内单例，避免 PaddleOCR 非线程安全问题。

### 13.3 离线运行

启动后不再联网；OCR 模型必须事先通过 `fapiao init` 下载至 `~/.paddlex/official_models/`。模型缺失时启动直接 exit 2 + 中文 actionable 提示。

### 13.4 可选依赖安装

```bash
.venv/Scripts/python.exe -m pip install -e ".[web]"
```

仅 CLI 用户无须安装 `[web]` extras。

## 14. 许可证

详见 `LICENSE`（如存在）。
