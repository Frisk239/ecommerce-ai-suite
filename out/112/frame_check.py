"""Slice 112B: VLM sanity check that extracted frames show a product (monitor)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(r"E:\code\ecommerce-ai-suite\apps\api\src")))

from suite_api.services import vlm  # noqa: E402


def main() -> None:
    if not vlm.is_configured():
        print("VLM 未配置")
        return
    for p in sys.argv[1:]:
        img = Path(p).read_bytes()
        text = vlm.chat_with_image(
            "You are a frame-QA assistant. Answer in one short sentence.",
            "What product and what scene is shown in this video frame? Name the visible object(s).",
            img,
        )
        print(f"{Path(p).name}: {text}")


if __name__ == "__main__":
    main()
