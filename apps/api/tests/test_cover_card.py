"""封面文字卡流水线单测（第 115 刀 W15b）：确定性渲染的形状与可复现性。

渲染用例依赖 CJK 字体（容器/CI fonts-noto-cjk，Windows/macOS 系统字体）——
缺字体时 skip（CoverCardError 的「诚实失败」分支由服务层冒泡，在素材集成
面另有覆盖）；纯函数（sanitize/palette）不受字体影响恒跑。
"""

import io

import pytest
from PIL import Image

from suite_api.services import cover_card
from suite_api.services.cjk_font import find_cjk_font

_HAS_FONT = find_cjk_font() is not None

SPEC = cover_card.CardSpec(
    title="钛钢保温杯也太会装了吧！",
    product_name="钛钢保温杯",
    category="器皿",
    palette_index=0,
)


@pytest.mark.skipif(not _HAS_FONT, reason="渲染用例需要 CJK 字体（fonts-noto-cjk）")
def test_render_shape_is_3_4_png() -> None:
    data = cover_card.render_cover_card(SPEC)
    assert data[:8] == b"\x89PNG\r\n\x1a\n"  # PNG 魔数（暂存键后缀/预览 mime 的前提）
    with Image.open(io.BytesIO(data)) as card:
        assert card.size == (cover_card.WIDTH, cover_card.HEIGHT)  # 3:4 竖版


@pytest.mark.skipif(not _HAS_FONT, reason="渲染用例需要 CJK 字体（fonts-noto-cjk）")
def test_render_is_deterministic() -> None:
    """流水线的本质卖点：同 spec 逐字节可复现（AI 路径做不到）。"""
    assert cover_card.render_cover_card(SPEC) == cover_card.render_cover_card(SPEC)
    assert cover_card.card_digest(SPEC) == cover_card.card_digest(SPEC)


@pytest.mark.skipif(not _HAS_FONT, reason="渲染用例需要 CJK 字体（fonts-noto-cjk）")
def test_palette_index_changes_colors_not_layout() -> None:
    """配色随任务 id 取模变化（防千篇一律），版式（白卡/尺寸）不变。"""
    other = cover_card.render_cover_card(
        cover_card.CardSpec(
            title=SPEC.title, product_name=SPEC.product_name, category=SPEC.category, palette_index=1
        )
    )
    assert other != cover_card.render_cover_card(SPEC)  # 背景色不同即字节不同
    with Image.open(io.BytesIO(other)) as card:
        assert card.size == (cover_card.WIDTH, cover_card.HEIGHT)


def test_palette_for_wraps_around() -> None:
    assert cover_card.palette_for(0) == cover_card.palette_for(len(cover_card.PALETTES))
    assert cover_card.palette_for(-1) == cover_card.palette_for(len(cover_card.PALETTES) - 1)
