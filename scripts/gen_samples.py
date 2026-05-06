"""生成中文合成票据样本，用于真机 OCR 验收。"""

from __future__ import annotations

import sys
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw, ImageFont


def _font(size: int) -> ImageFont.ImageFont:
    candidates = [
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\simsun.ttc",
        r"C:\Windows\Fonts\Deng.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _draw(path: Path, lines: list[str], *, size: tuple[int, int] = (800, 1100)) -> None:
    img = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(img)
    title_font = _font(36)
    body_font = _font(24)
    y = 60
    for idx, line in enumerate(lines):
        font = title_font if idx == 0 else body_font
        draw.text((60, y), line, fill="black", font=font)
        y += 60 if idx == 0 else 40
    img.save(path, "PNG")


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: gen_samples.py <output_dir>", file=sys.stderr)
        return 2
    out_dir = Path(sys.argv[1])
    out_dir.mkdir(parents=True, exist_ok=True)

    _draw(
        out_dir / "01_invoice_2024_03_15.png",
        [
            "北京市增值税电子普通发票",
            "发票代码: 011002400111",
            "发票号码: 88990011",
            "开票日期: 2024-03-15",
            "购买方: 张三科技有限公司",
            "价税合计: 1,234.56 元",
            "税额: 96.04 元",
        ],
    )
    _draw(
        out_dir / "02_invoice_2024_01_08.png",
        [
            "上海市增值税专用发票",
            "发票代码: 011002400222",
            "发票号码: 11220033",
            "开票日期: 2024年1月8日",
            "销售方: 李四餐饮服务",
            "价税合计: 388.00 元",
        ],
    )
    _draw(
        out_dir / "03_order_2024_05_20.png",
        [
            "京东商城订单详情",
            "订单编号: JD20240520123456",
            "下单日期: 2024/5/20",
            "收货地址: 广州市天河区珠江新城",
            "商品清单: 笔记本电脑 1 台",
            "应付总额: 6,499.00 元",
        ],
    )
    _draw(
        out_dir / "04_order_no_date.png",
        [
            "淘宝订单确认单",
            "订单号: TB202404XXXXXX",
            "商品清单: 蓝牙耳机 一副",
            "收货地址: 深圳市南山区科技园",
            "应付金额: 299.00 元",
        ],
    )

    # PDF：将订单图作为单页 PDF
    pdf_path = out_dir / "05_order_pdf_2023_12_01.pdf"
    img = Image.new("RGB", (800, 1100), "white")
    draw = ImageDraw.Draw(img)
    body_font = _font(24)
    title_font = _font(36)
    draw.text((60, 60), "苏宁易购订单凭证", fill="black", font=title_font)
    body = [
        "订单编号: SN20231201998877",
        "下单日期: 2023.12.01",
        "收货地址: 杭州市西湖区文三路",
        "商品清单: 智能手机 1 部",
        "应付金额: 4,299.00 元",
    ]
    y = 140
    for line in body:
        draw.text((60, y), line, fill="black", font=body_font)
        y += 40
    tmp_png = out_dir / "_tmp_pdf.png"
    img.save(tmp_png)

    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    rect = pymupdf.Rect(40, 40, 555, 802)
    page.insert_image(rect, filename=str(tmp_png))
    doc.save(pdf_path)
    doc.close()
    tmp_png.unlink()

    print(f"生成完成：{out_dir}")
    for p in sorted(out_dir.iterdir()):
        print(f"  - {p.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
