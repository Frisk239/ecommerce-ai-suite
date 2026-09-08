"""素材中心集成测试（真 PG；complete_chat 替身不发外网；第 17 刀/ADR 0038）。

契约：
- 建任务同步就地执行：返回即稳定态 pending_qc（生成+规则质检都过）；
- approve -> registered + 登记出 kind=material / source_kind=material_generated
  资产（机洗按所挂商品规格字段跑正则，登记内已推进待人洗）-> 人洗确认 ->
  发布 -> 顾客问「保温杯有什么卖点」命中素材切块并引用；
- 打回 -> failed（人工打回）-> retry 新一次生成 -> 再 approve 登记；
- 空 key（无替身）-> failed「生成不可用」不降级：无任何素材资产；
- 非法转移 409（registered 再 retry / failed 直接 approve）；
- 打码步（ADR 0038 P1#4）：转写含手机号 -> QA 抽取 prompt 输入已打码、
  落库 qa_pairs 值不留裸号（断言见文件末，与回流链路同路）。
"""

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sse_helpers import parse_sse_events

from suite_api.services import llm as llm_module

ApiFixture = tuple[TestClient, Path]

GOOD_TITLE = "钛钢保温杯：一杯守住温度"
GOOD_CONTENT = (
    "卖点一：钛钢保温杯双层真空，持久保温12小时。\n"
    "卖点二：钛钢保温杯轻量杯身，通勤车载两相宜。\n"
    "材质：钛钢\n"
    "净含量：480ml"
)


def _login(client: TestClient) -> None:
    assert client.post(
        "/api/auth/login", json={"username": "operator", "password": "operator123"}
    ).status_code == 200


def _material_json(title: str = GOOD_TITLE, content: str = GOOD_CONTENT) -> str:
    return json.dumps({"title": title, "content": content}, ensure_ascii=False)


def _cup_id(client: TestClient) -> int:
    """按名取种子商品（id 分配随种子顺序，不硬编码：净含量+材质 required 的保温杯）。"""
    products = client.get("/api/products").json()
    return next(p["id"] for p in products if p["name"] == "钛钢保温杯")


def _patch_complete_chat(
    monkeypatch: pytest.MonkeyPatch,
    result: str | None = None,
    error: Exception | None = None,
) -> list[dict[str, str]]:
    """替换 llm.complete_chat；返回 prompt 捕获记录（同回流测试先例）。"""
    calls: list[dict[str, str]] = []

    async def fake(system_prompt: str, user_prompt: str) -> Any:
        calls.append({"system": system_prompt, "user": user_prompt})
        if error is not None:
            raise error
        assert result is not None
        return result

    monkeypatch.setattr(llm_module, "complete_chat", fake)
    return calls


def _ask(client: TestClient, session_id: int, question: str) -> list[tuple[str, dict]]:
    with client.stream(
        "POST", f"/api/service/sessions/{session_id}/messages", json={"content": question}
    ) as resp:
        assert resp.status_code == 200
        raw = "".join(resp.iter_text())
    return parse_sse_events(raw)


# ---------- 全闭环：生成 -> 抽检 -> 登记 -> 人洗发布 -> 检索引用 ----------


def test_full_loop_generate_approve_publish_and_retrieval(
    api: ApiFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = api
    _login(client)
    cup_id = _cup_id(client)
    calls = _patch_complete_chat(monkeypatch, result=_material_json())

    created = client.post("/api/material/tasks", json={"product_id": cup_id})
    assert created.status_code == 201
    task = created.json()
    task_id = task["id"]
    assert task["status"] == "pending_qc"
    assert task["title"] == GOOD_TITLE
    assert task["product_name"] == "钛钢保温杯"
    assert task["asset_id"] is None
    assert task["last_error"] is None
    assert len(calls) == 1  # 建任务请求内同步生成一次
    assert "钛钢保温杯" in calls[0]["user"]  # 商品名+规格事实进 prompt
    assert len(calls) == 1  # 质检是代码规则，不再额外调 LLM

    # 列表与详情视图一致
    listed = client.get("/api/material/tasks").json()
    assert [t["id"] for t in listed][0] == task_id  # id 倒序
    assert client.get(f"/api/material/tasks/{task_id}").json() == task

    approved = client.post(f"/api/material/tasks/{task_id}/approve")
    assert approved.status_code == 200
    registered = approved.json()
    assert registered["status"] == "registered"
    asset_id = registered["asset_id"]
    assert asset_id is not None

    # 登记出的素材资产：kind=material、来源=素材生成、挂商品、机洗已弃权/推进
    asset = client.get(f"/api/assets/{asset_id}").json()
    assert asset["kind"] == "material"
    assert asset["source_kind"] == "material_generated"
    assert asset["status"] == "pending_review"
    assert asset["product"]["id"] == cup_id
    extracted = asset["versions"][0]["extracted_fields"]
    assert extracted["材质"] == {"value": "钛钢", "source": "machine"}
    assert extracted["净含量"] == {"value": "480ml", "source": "machine"}

    # 必填闸门仅种类=文档且挂商品（CONTEXT「必填字段」词条）：素材没有规格
    # 必填——机洗抽到值未确认也直接可发布（第 17 刀修复的潜伏偏离，对照
    # 文档挂商品未确认仍 422 的既有测试 test_publish_gate）
    assert client.post(f"/api/assets/{asset_id}/publish").status_code == 200

    # 顾客问卖点 -> 命中素材正文切块并引用 v1
    sid = client.post("/api/service/sessions").json()["id"]
    events = _ask(client, sid, "保温杯有什么卖点")
    complete = events[-1][1]
    assert complete["kind"] == "answer"
    assert {"asset_id": asset_id, "version_no": 1} in complete["citations"]
    answer = "".join(d["text"] for e, d in events if e == "delta")
    assert "钛钢保温杯" in answer

    # registered 是终态：再重试/打回都 409
    assert client.post(f"/api/material/tasks/{task_id}/retry").status_code == 409
    assert client.post(f"/api/material/tasks/{task_id}/reject").status_code == 409


# ---------- 打回 -> 失败 -> 重试 -> 再抽检通过 ----------


def test_reject_retry_then_approve(api: ApiFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = api
    _login(client)
    cup_id = _cup_id(client)
    _patch_complete_chat(monkeypatch, result=_material_json())
    task_id = client.post("/api/material/tasks", json={"product_id": cup_id}).json()["id"]

    rejected = client.post(f"/api/material/tasks/{task_id}/reject").json()
    assert rejected["status"] == "failed"
    assert rejected["last_error"] == "人工打回"
    assert rejected["asset_id"] is None
    # failed 不能直接抽检通过（生成侧与抽检侧闸门互斥）
    assert client.post(f"/api/material/tasks/{task_id}/approve").status_code == 409

    retried = client.post(f"/api/material/tasks/{task_id}/retry")
    assert retried.status_code == 200
    body = retried.json()
    assert body["status"] == "pending_qc"
    assert body["last_error"] is None  # 复位：打回原因清空

    again = client.post(f"/api/material/tasks/{task_id}/approve").json()
    assert again["status"] == "registered"
    assert again["asset_id"] is not None


# ---------- 空 key：failed 不降级、不进中台 ----------


def test_no_llm_key_fails_without_fallback(api: ApiFixture) -> None:
    client, _ = api
    _login(client)
    # 前面用例登记的素材资产数（本用例只验「失败不新增」，不验库为空）
    material_before = len(
        [a for a in client.get("/api/assets").json() if a["kind"] == "material"]
    )
    # 不 patch：conftest 空凭证进程里 complete_chat 抛 LLMNotConfigured
    task = client.post("/api/material/tasks", json={"product_id": _cup_id(client)}).json()
    assert task["status"] == "failed"
    assert "生成不可用" in task["last_error"]
    assert task["content"] is None
    assert task["asset_id"] is None
    # 失败不进中台（0029）：素材通道名下一个字节都没多
    material_after = len(
        [a for a in client.get("/api/assets").json() if a["kind"] == "material"]
    )
    assert material_after == material_before


# ---------- 入参与闸门 404/422/401 ----------


def test_gate_and_error_contract(api: ApiFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = api
    _login(client)
    assert client.post("/api/material/tasks", json={"product_id": 9999}).status_code == 404
    assert client.post("/api/material/tasks", json={}).status_code == 422
    assert client.get("/api/material/tasks/999999").status_code == 404
    assert client.post("/api/material/tasks/999999/approve").status_code == 404

    _patch_complete_chat(
        monkeypatch, result=_material_json(content="卖点：这款杯子很好用。")  # 缺商品名
    )
    bad = client.post("/api/material/tasks", json={"product_id": _cup_id(client)}).json()
    assert bad["status"] == "failed"
    assert "商品名" in bad["last_error"]
    assert client.post(f"/api/material/tasks/{bad['id']}/approve").status_code == 409

    # 未登录 401（素材是操作者动作）
    client.cookies.clear()
    assert client.get("/api/material/tasks").status_code == 401
    assert client.post("/api/material/tasks", json={"product_id": 1}).status_code == 401
    _login(client)


# ---------- 打码步集成（ADR 0038 P1#4，回流 QA 链路） ----------


def test_redact_in_reflow_qa_pipeline(api: ApiFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = api
    _login(client)
    calls = _patch_complete_chat(
        monkeypatch,
        result=(
            '[{"q": "上门安装要留电话吗",'
            ' "a": "留 13812345678 或邮箱 zhangsan@example.com，师傅回电"}]'
        ),
    )
    sid = client.post("/api/service/sessions").json()["id"]
    _ask(client, sid, "上门安装要留电话 13812345678 吗")

    register = client.post(f"/api/service/sessions/{sid}/register")
    assert register.status_code == 201
    asset = register.json()
    assert asset["status"] == "pending_review"

    # 接入点 a：转写送厂商前已打码——prompt 里无裸号
    assert len(calls) == 1
    assert "13812345678" not in calls[0]["user"]
    assert "1********78" in calls[0]["user"]

    # 接入点 b：模型把裸号带进答案也留不住——落库 qa_pairs 值已打码
    pairs = asset["versions"][0]["extracted_fields"]["qa_pairs"]["value"]
    flat = "".join(f"{p['q']}{p['a']}" for p in pairs)
    assert "13812345678" not in flat and "zhangsan@example.com" not in flat
    assert "1********78" in flat and "****@example.com" in flat
