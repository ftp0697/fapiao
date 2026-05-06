from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from PIL import Image, ImageDraw

from fapiao_pdf.splitter import _iou, split_page


def _draw_receipts(
    size: tuple[int, int],
    boxes: list[tuple[int, int, int, int]],
) -> Image.Image:
    img = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(img)
    for box in boxes:
        draw.rectangle(box, fill="black")
    return img


def test_split_page_returns_two_crops_for_two_receipts(tmp_path: Path) -> None:
    img = _draw_receipts(
        (1000, 1400),
        [(80, 80, 920, 560), (100, 760, 900, 1280)],
    )

    crops, warn = split_page(img, tmp_path / "page.png")

    assert warn is None
    assert crops is not None
    assert len(crops) == 2
    assert crops[0].display_key.endswith("#crop=0001")
    assert crops[1].display_key.endswith("#crop=0002")


def test_split_page_sorts_left_to_right_then_top_to_bottom(tmp_path: Path) -> None:
    img = _draw_receipts(
        (1200, 1200),
        [
            (650, 120, 1120, 520),
            (80, 120, 540, 520),
            (120, 700, 1080, 1080),
        ],
    )

    crops, warn = split_page(img, tmp_path / "page.png")

    assert warn is None
    assert crops is not None
    assert len(crops) == 3
    assert crops[0].bbox[0] < crops[1].bbox[0]
    assert crops[0].bbox[1] <= crops[1].bbox[1]
    assert crops[2].bbox[1] > crops[0].bbox[1]


def test_split_page_falls_back_for_single_receipt(tmp_path: Path) -> None:
    img = _draw_receipts((1000, 1400), [(100, 120, 900, 1280)])

    crops, warn = split_page(img, tmp_path / "single.png")

    assert crops is None
    assert warn == "未检测到多票据区域，回退整页"


def test_split_page_applies_padding_and_display_key(tmp_path: Path) -> None:
    src = tmp_path / "sample.png"
    img = _draw_receipts(
        (1000, 1400),
        [(120, 150, 460, 850), (560, 180, 880, 900)],
    )

    crops, warn = split_page(img, src)

    assert warn is None
    assert crops is not None
    assert crops[0].display_key == f"{src}#crop=0001"
    assert crops[1].display_key == f"{src}#crop=0002"
    assert crops[0].bbox[0] < 120
    assert crops[0].bbox[1] < 150
    assert crops[0].bbox[2] > 460
    assert crops[0].bbox[3] > 850


@settings(suppress_health_check=[HealthCheck.function_scoped_fixture], max_examples=15)
@given(
    x1=st.integers(min_value=40, max_value=80),
    y1=st.integers(min_value=40, max_value=80),
    w1=st.integers(min_value=320, max_value=380),
    h1=st.integers(min_value=420, max_value=480),
    y2=st.integers(min_value=720, max_value=820),
    w2=st.integers(min_value=320, max_value=380),
    h2=st.integers(min_value=420, max_value=480),
)
def test_split_page_generated_boxes_do_not_overlap(
    tmp_path: Path,
    x1: int,
    y1: int,
    w1: int,
    h1: int,
    y2: int,
    w2: int,
    h2: int,
) -> None:
    box1 = (x1, y1, x1 + w1, y1 + h1)
    box2 = (x1, y2, x1 + w2, y2 + h2)
    img = _draw_receipts((1000, 1400), [box1, box2])

    crops, warn = split_page(img, tmp_path / "gen.png")

    if crops is None:
        # 边缘策略允许整页回退；不变量仅约束成功路径
        assert warn is not None
        return
    assert len(crops) >= 2
    assert _iou(crops[0].bbox, crops[1].bbox) <= 0.2
