"""票据分类。"""

from typing import Literal

DocType = Literal["invoice", "order"]

_INVOICE_KEYWORDS: tuple[str, ...] = (
    "发票",
    "税额",
    "价税合计",
    "发票号码",
    "发票代码",
)

_ORDER_KEYWORDS: tuple[str, ...] = (
    "订单",
    "订单号",
    "订单编号",
    "商品清单",
    "收货地址",
)


def classify(text: str) -> tuple[DocType, str | None]:
    """根据 OCR 文本返回类型与可选警告。

    发票关键词优先；命中订单关键词为订单；均未命中降级为订单并给出警告。
    """

    if any(kw in text for kw in _INVOICE_KEYWORDS):
        return "invoice", None
    if any(kw in text for kw in _ORDER_KEYWORDS):
        return "order", None
    return "order", "类型识别失败，按订单处理"
