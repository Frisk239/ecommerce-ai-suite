"""第 100 刀验收探针（临时，不提交）：M&M white 三问同答 + Erdbeeren 对照。"""

import json
import urllib.request

BASE = "http://localhost:8000"


def ask_once(question: str) -> dict:
    body = json.dumps({"content": question}).encode()
    req = urllib.request.Request(
        BASE + "/api/customer/sessions", data=b"{}", method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        created = json.load(resp)
    token, sid = created["token"], created["session_id"]
    req = urllib.request.Request(
        BASE + f"/api/customer/sessions/{sid}/messages", data=body, method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    events = []
    with urllib.request.urlopen(req, timeout=120) as resp:
        event, data = None, []
        for raw in resp:  # noqa: PLC0415 - 解析 SSE 行流
            line = raw.decode("utf-8").rstrip("\n")
            if line.startswith("event:"):
                event = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data.append(line.split(":", 1)[1].strip())
            elif not line and event:
                events.append((event, json.loads("".join(data))))
                event, data = None, []
        if event and data:
            events.append((event, json.loads("".join(data))))
    complete = next(payload for name, payload in events if name == "complete")
    deltas = "".join(
        payload.get("text", "") for name, payload in events if name == "delta"
    )
    return {
        "kind": complete["kind"],
        "citations": complete.get("citations"),
        "gap_id": complete.get("gap_id"),
        "text": deltas.strip()[:120],
    }


for i in range(1, 4):
    print(f"M&M white #{i}:", json.dumps(ask_once("M&M white的条码是多少"), ensure_ascii=False))
print("Erdbeeren :", json.dumps(ask_once("Erdbeeren的条码是多少"), ensure_ascii=False))
print("Fitpiggy  :", json.dumps(ask_once("Fitpiggy的条码是多少"), ensure_ascii=False))
