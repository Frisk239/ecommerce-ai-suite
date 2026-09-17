"""小红书封面文字卡流水线（第 115 刀 W15b，ADR 0055 分层内的确定性分支）。

**为什么是流水线不是大模型**（Owner 裁决，2026-09-17）：小红书主流封面形态
之一是「大字报文字卡」——色块背景 + 白色圆角卡 + 大号粗体标题（关键词高亮）
+ 小字装饰，**可以完全不出现商品**。这类图的全部信息都是我们已有的确定性
输入（文案标题/商品名/类目），排版规则明确（3:4、留白、字号层级）——交给
生成模型等于把已知答案交给掷骰子（中文渲染错字/排版失控），程序化渲染
可测、零幻觉、逐字节可复现。AI 的位置在另一分支：商品图美化走图像**编辑**
模型（imggen.edit_image，商品主体来自真实原图）。

版式锚定 Owner 提供的真实小红书截图：暖饱和底色、居中白色圆角卡、三行内
大标题、单关键词高亮色条、底部小字行。尺寸 1080×1440（3:4 竖版，平台封面
主流比例）。字体走 services.cjk_font（容器 fonts-noto-cjk，98b 刀先例）。
"""

from __future__ import annotations

import io
import re
import unicodedata
import zlib
from dataclasses import dataclass
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

from suite_api.services.cjk_font import find_cjk_font

# 画布与版式常量（3:4 竖版；改这里就是改产品语义，测试钉死）
WIDTH = 1080
HEIGHT = 1440
CARD_MARGIN = 72  # 白卡四边留白
CARD_RADIUS = 56
CARD_PADDING = 84  # 卡内文字到卡边的内边距
TITLE_SIZES = [148, 124, 104, 88, 76]  # 逐档降字号直到 ≤3 行
TITLE_MAX_LINES = 3
TITLE_LINE_GAP = 28
HIGHLIGHT_PAD_X = 10
HIGHLIGHT_PAD_Y = 14
FOOTER_SIZE = 44
CHIP_SIZE = 40
INK = (26, 26, 30)  # 近黑墨色（视觉锚定：文字主色近黑，不纯黑）
MUTED = (150, 148, 144)
WHITE = (255, 255, 255)


class CoverCardError(Exception):
    """封面卡渲染失败（当前唯一成因：找不到 CJK 字体——诚实失败不产豆腐块图）。"""


# 色板（bg, accent）：主色+强调色两色封顶（研究口径：封面配色 ≤3 色）。
# 按 task.id 取模选格——同任务重试配色稳定，不同任务有变化。
PALETTES: list[tuple[tuple[int, int, int], tuple[int, int, int]]] = [
    ((244, 179, 66), (94, 152, 214)),  # 暖黄 × 雾蓝（截图原色调）
    ((233, 116, 81), (250, 233, 153)),  # 珊瑚橙 × 奶油黄
    ((96, 141, 198), (250, 220, 120)),  # 湖蓝 × 杏黄
    ((106, 173, 120), (247, 236, 178)),  # 抹茶绿 × 米黄
    ((196, 122, 161), (246, 234, 211)),  # 藕粉 × 暖白
    ((70, 84, 118), (240, 200, 132)),  # 黛蓝 × 灯金
]


@dataclass(frozen=True)
class CardSpec:
    """一张封面卡的全部确定性输入（渲染纯函数化：同 spec 同字节）。"""

    title: str
    product_name: str
    category: str
    palette_index: int = 0


# emoji 与装饰符号剔除（Pillow + 系统字体渲染彩色 emoji 不可靠；标题要的是字）
_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF\uFE0F\u200D]"
)


def sanitize_title(title: str) -> str:
    """标题进卡前的清洗（纯函数）：去 emoji/变体选择符、合并空白、截 24 字。"""
    text = _EMOJI_RE.sub("", unicodedata.normalize("NFC", title))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:24]


def palette_for(index: int) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    return PALETTES[index % len(PALETTES)]


@lru_cache(maxsize=32)
def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def _wrap_line(
    text: str, font: ImageFont.FreeTypeFont, max_width: int, measure: ImageDraw.ImageDraw
) -> list[str]:
    """按像素宽度硬折（CJK 无空格可依）；返回行列表。"""
    lines: list[str] = []
    current = ""
    for ch in text:
        candidate = current + ch
        if measure.textlength(candidate, font=font) > max_width and current:
            lines.append(current)
            current = ch
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def _wrap_title(
    title: str, font_path: str, max_width: int, measure: ImageDraw.ImageDraw
) -> tuple[int, list[str]]:
    """逐档降字号折行，取第一个 ≤3 行的档；都放不下用最小档放 3 行（截断保形）。"""
    for size in TITLE_SIZES:
        font = _font(font_path, size)
        lines = _wrap_line(title, font, max_width, measure)
        if len(lines) <= TITLE_MAX_LINES:
            return size, lines
    font = _font(font_path, TITLE_SIZES[-1])
    lines = _wrap_line(title, font, max_width, measure)[:TITLE_MAX_LINES]
    return TITLE_SIZES[-1], lines


def _draw_highlight_run(
    draw: ImageDraw.ImageDraw,
    text: str,
    xy: tuple[float, float],
    font: ImageFont.FreeTypeFont,
    accent: tuple[int, int, int],
    ink: tuple[int, int, int],
) -> None:
    """高亮词：accent 圆角色条垫底 + 近黑字（截图形态：浅色块压字不反字）。"""
    x, y = xy
    width = draw.textlength(text, font=font)
    ascent, descent = font.getmetrics()
    draw.rounded_rectangle(
        (x - HIGHLIGHT_PAD_X, y - HIGHLIGHT_PAD_Y // 2, x + width + HIGHLIGHT_PAD_X, y + ascent + descent // 2),
        radius=12,
        fill=accent,
    )
    draw.text((x, y), text, font=font, fill=ink)


def render_cover_card(spec: CardSpec) -> bytes:
    """CardSpec → PNG 字节（1080×1440）。同 spec 逐字节可复现（纯渲染，无随机）。

    抛 ``CoverCardError``（找不到 CJK 字体）——不产出豆腐块假图。
    """
    font_path = find_cjk_font()
    if font_path is None:
        raise CoverCardError("找不到中文字体：封面文字卡渲染需要 CJK 字形（容器装 fonts-noto-cjk）")
    bg, accent = palette_for(spec.palette_index)
    image = Image.new("RGB", (WIDTH, HEIGHT), bg)
    draw = ImageDraw.Draw(image)

    # 白色圆角卡
    card_box = (CARD_MARGIN, CARD_MARGIN, WIDTH - CARD_MARGIN, HEIGHT - CARD_MARGIN)
    draw.rounded_rectangle(card_box, radius=CARD_RADIUS, fill=WHITE)

    inner_x = CARD_MARGIN + CARD_PADDING
    inner_w = WIDTH - 2 * (CARD_MARGIN + CARD_PADDING)

    # 顶部小行：类目 chip（左）+ 「好物分享」（右，muted）
    chip_font = _font(font_path, CHIP_SIZE)
    chip_text = spec.category[:6] or "好物"
    chip_w = draw.textlength(chip_text, font=chip_font) + 36
    chip_y = card_box[1] + CARD_PADDING
    draw.rounded_rectangle(
        (inner_x, chip_y, inner_x + chip_w, chip_y + CHIP_SIZE + 24), radius=14, fill=bg
    )
    draw.text((inner_x + 18, chip_y + 10), chip_text, font=chip_font, fill=WHITE)
    tag_font = _font(font_path, CHIP_SIZE)
    tag_text = "好物分享"
    tag_w = draw.textlength(tag_text, font=tag_font)
    draw.text((WIDTH - inner_x - tag_w, chip_y + 10), tag_text, font=tag_font, fill=MUTED)

    # 大标题：清洗 → 降字号折行 → 商品名子串高亮
    title = sanitize_title(spec.title) or spec.product_name or "好物推荐"
    size, lines = _wrap_title(title, font_path, inner_w, draw)
    title_font = _font(font_path, size)
    keyword = spec.product_name.strip()
    line_height = title_font.getmetrics()[0] + TITLE_LINE_GAP
    # 标题块垂直居中于卡片中部（顶部小行与底部小字之间）
    title_top = chip_y + CHIP_SIZE + 24 + 96
    block_bottom_limit = HEIGHT - CARD_MARGIN - CARD_PADDING - FOOTER_SIZE - 88
    block_height = line_height * len(lines) - TITLE_LINE_GAP
    y = title_top + max(0, (block_bottom_limit - title_top - block_height) // 2)
    for line in lines:
        draw.text((inner_x, y), line, font=title_font, fill=INK)
        if keyword and keyword in line:
            start = line.index(keyword)
            prefix = line[:start]
            x0 = inner_x + draw.textlength(prefix, font=title_font)
            _draw_highlight_run(draw, keyword, (x0, y), title_font, accent, INK)
        y += line_height

    # 底部小字行：分隔线 + 左商品名 / 右「3:4 · 文字卡」装饰
    footer_font = _font(font_path, FOOTER_SIZE)
    footer_y = HEIGHT - CARD_MARGIN - CARD_PADDING - FOOTER_SIZE - 40
    draw.line(
        (inner_x, footer_y - 32, WIDTH - inner_x, footer_y - 32), fill=(232, 230, 226), width=3
    )
    name = (spec.product_name.strip() or "好物")[:12]
    draw.text((inner_x, footer_y), name, font=footer_font, fill=INK)
    tail = "每日好物"
    tail_w = draw.textlength(tail, font=footer_font)
    draw.text((WIDTH - inner_x - tail_w, footer_y), tail, font=footer_font, fill=MUTED)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True, compress_level=6)
    return buffer.getvalue()


# zlib.crc32 只为让「逐字节可复现」可被一行断言钉住（PNG 无时间戳字段，
# Pillow 输出本就确定性；显式 crc 是给测试的稳定锚）
def card_digest(spec: CardSpec) -> int:
    return zlib.crc32(render_cover_card(spec))
