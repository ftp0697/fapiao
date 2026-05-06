"""排序规则。"""

from datetime import date

from fapiao_pdf.models import ProcessedDocument

_TYPE_PRIORITY: dict[str, int] = {"invoice": 0, "order": 1}
_DATE_FAR_FUTURE: date = date.max


def _sort_key(doc: ProcessedDocument) -> tuple[int, int, date, str]:
    type_rank: int = _TYPE_PRIORITY[doc.doc_type]
    has_date_rank: int = 0 if doc.date is not None else 1
    sort_date: date = doc.date if doc.date is not None else _DATE_FAR_FUTURE
    return type_rank, has_date_rank, sort_date, doc.original.display_key


def sort_documents(docs: list[ProcessedDocument]) -> list[ProcessedDocument]:
    """先发票后订单；组内有日期升序在前，无日期在尾；同序按 display_key。"""

    return sorted(docs, key=_sort_key)
