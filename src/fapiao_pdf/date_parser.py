"""日期解析。"""

import re
from datetime import date

_DATE_PATTERN: re.Pattern[str] = re.compile(
    r"(\d{4})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})\s*日?"
)


def parse_first_valid_date(text: str) -> date | None:
    """按 OCR 文本流顺序返回首个有效日历日期。"""

    for match in _DATE_PATTERN.finditer(text):
        year, month, day = (int(g) for g in match.groups())
        try:
            return date(year, month, day)
        except ValueError:
            continue
    return None
