"""集成测试共享的 SSE 解析助手（service 流式接口的事件口径）。"""

import json


def parse_sse_events(raw: str) -> list[tuple[str, dict]]:
    """解析 SSE 文本为 [(event, data)]；空块跳过，data 行 JSON 解析。"""
    events: list[tuple[str, dict]] = []
    for block in raw.strip().split("\n\n"):
        if not block:
            continue
        lines = block.splitlines()
        event = next(line.removeprefix("event: ") for line in lines if line.startswith("event: "))
        data = json.loads(
            next(line.removeprefix("data: ") for line in lines if line.startswith("data: "))
        )
        events.append((event, data))
    return events
