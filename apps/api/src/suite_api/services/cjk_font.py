"""CJK 字体定位（第 98b 刀起成片 drawtext 用；第 115 刀起封面文字卡共用）。

单一出处：视频合成的 ffmpeg drawtext 与素材封面的 Pillow 渲染都要一个带
CJK 字形的字体文件，两处各搜一份必然漂移（一个找到了另一个豆腐块）。
找不到返回 None——「缺字体怎么报」是各调用方的语义（RenderError /
CoverCardError），定位本身不做产品决定。

容器/CI：``fonts-noto-cjk``（Dockerfile 与 CI workflow 已装，98b 刀）；
Windows 开发机：微软雅黑/黑体/宋体；macOS：苹方/黑体。
"""

from __future__ import annotations

import functools
from glob import glob
from pathlib import Path

# 搜索序：粗字重在前（标题卡要的是 Bold 形态；Regular 也能用）
_CANDIDATES = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/arphic/uming.ttc",
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsun.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
]


@functools.lru_cache(maxsize=1)
def find_cjk_font() -> str | None:
    """返回第一个存在的 CJK 字体文件路径；找不到 None（调用方决定报错形态）。

    进程内缓存（字体不会在运行中长出来）；测试想覆盖路径请 monkeypatch 本函数。
    """
    candidates = list(_CANDIDATES)
    for pattern in ("/usr/share/fonts/**/NotoSansCJK*", "/usr/share/fonts/**/*wqy*"):
        candidates.extend(glob(pattern, recursive=True))
    for path in candidates:
        if Path(path).is_file():
            return path
    return None
