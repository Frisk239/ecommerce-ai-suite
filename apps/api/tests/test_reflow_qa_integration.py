"""回流 QA 增强集成测试（真 PG，complete_chat 全 mock 不调外网；第 12 刀/ADR 0035）。

契约：
- 回流登记（配置 LLM 时）同步抽 qa_pairs 机洗草稿 -> 待人洗；
- 人洗确认（改文案/删对/增对，source=human）-> 发布 -> confirmed QA 对成块
  「问：…/答：…」入索引，同问法命中并引用 A-{id}·v1；机洗草稿未确认不进索引；
- LLM 失败/坏输出 -> 停已接入 + last_error「LLM QA 抽取失败」-> 重试端点重跑
  LLM 抽取成功后推进待人洗；
- 版本正文端点（操作者面）返回转写全文。

空 key 降级（弃权照常待人洗）由既有 test_service_integration 的零改动用例钉死。
"""

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sse_helpers import parse_sse_events

from suite_api.services import llm as llm_module

ApiFixture = tuple[TestClient, Path]


def _login(client: TestClient) -> None:
    assert client.post(
        "/api/auth/login", json={"username": "operator", "password": "operator123"}
    ).status_code == 200


def _ask(client: TestClient, session_id: int, question: str) -> list[tuple[str, dict]]:
    with client.stream(
        "POST", f"/api/service/sessions/{session_id}/messages", json={"content": question}
    ) as resp:
        assert resp.status_code == 200
        raw = "".join(resp.iter_text())
    return parse_sse_events(raw)


def _session_with_turns(client: TestClient, questions: list[str]) -> int:
    sid = client.post("/api/service/sessions").json()["id"]
    for q in questions:
        _ask(client, sid, q)  # 无发布证据 -> refusal，但两端消息都落库（转写有料）
    return sid


def _patch_complete_chat(
    monkeypatch: pytest.MonkeyPatch,
    result: str | None = None,
    error: Exception | None = None,
) -> list[dict[str, str]]:
    """替换 llm.complete_chat 为 fake（回流机洗调用它）；返回 prompt 捕获记录。"""
    calls: list[dict[str, str]] = []

    async def fake(system_prompt: str, user_prompt: str) -> Any:
        calls.append({"system": system_prompt, "user": user_prompt})
        if error is not None:
            raise error
        assert result is not None
        return result

    monkeypatch.setattr(llm_module, "complete_chat", fake)
    return calls


# ---------- 全闭环：机洗草稿 -> 人洗 -> 发布 -> 检索命中 QA 块 ----------


def test_reflow_qa_draft_wash_publish_and_retrieval_hits_qa_block(
    api: ApiFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = api
    _login(client)
    calls = _patch_complete_chat(
        monkeypatch,
        result=(
            "```json\n"
            '[{"q": "盲盒可以指定款式吗", "a": "盲盒随机发货，不能指定"},'
            ' {"q": "赠品杯刷单独卖吗", "a": "杯刷不单独售卖"}]\n'
            "```"
        ),
    )
    sid = _session_with_turns(client, ["盲盒可以指定款式吗"])

    register_resp = client.post(f"/api/service/sessions/{sid}/register")
    assert register_resp.status_code == 201
    dialogue = register_resp.json()
    dialogue_id = dialogue["id"]
    assert dialogue["kind"] == "dialogue"
    assert dialogue["status"] == "pending_review"  # 机洗成功推进待人洗
    assert dialogue["last_error"] is None
    assert dialogue["versions"][0]["extracted_fields"]["qa_pairs"] == {
        "value": [
            {"q": "盲盒可以指定款式吗", "a": "盲盒随机发货，不能指定"},
            {"q": "赠品杯刷单独卖吗", "a": "杯刷不单独售卖"},
        ],
        "source": "machine",
    }
    assert len(calls) == 1  # 登记请求内同步抽一次
    assert "顾客：盲盒可以指定款式吗" in calls[0]["user"]  # 转写全文进 prompt
    assert "JSON" in calls[0]["system"]

    # 版本正文端点：转写全文可见（人洗必须看见正文，ADR 0035）
    text_resp = client.get(f"/api/assets/{dialogue_id}/versions/1/text")
    assert text_resp.status_code == 200
    assert text_resp.headers["content-type"].startswith("text/plain")
    assert "顾客：盲盒可以指定款式吗" in text_resp.text

    # 人洗：改第一对文案、删第二对、增一对全新 QA（PATCH 走既有确认流程）
    patched = client.patch(
        f"/api/assets/{dialogue_id}/versions/1/fields",
        json={
            "qa_pairs": [
                {"q": "盲盒能挑款吗", "a": "盲盒为随机发货，不支持指定款式"},
                {"q": "店铺支持海外配送吗", "a": "暂不支持海外配送地址"},
            ]
        },
    )
    assert patched.status_code == 200
    assert patched.json()["confirmed_fields"]["qa_pairs"] == {
        "value": [
            {"q": "盲盒能挑款吗", "a": "盲盒为随机发货，不支持指定款式"},
            {"q": "店铺支持海外配送吗", "a": "暂不支持海外配送地址"},
        ],
        "source": "human",
    }

    published = client.post(f"/api/assets/{dialogue_id}/publish")
    assert published.status_code == 200

    # 顾客问新增对的问法（转写里没有）-> 命中 QA 块（问：…/答：…）并引用 v1
    events = _ask(client, client.post("/api/service/sessions").json()["id"], "店铺支持海外配送吗")
    complete = events[-1][1]
    assert complete["kind"] == "answer"
    assert {"asset_id": dialogue_id, "version_no": 1} in complete["citations"]
    answer = "".join(d["text"] for e, d in events if e == "delta")
    assert "问：店铺支持海外配送吗" in answer  # QA 块本体即证据
    assert "暂不支持海外配送地址" in answer
    # 改后口径命中：删掉的「赠品杯刷」问法不再有任何证据（未确认草稿不出索引）
    gone = _ask(client, client.post("/api/service/sessions").json()["id"], "赠品杯刷单独卖吗")
    assert gone[-1][1]["kind"] == "refusal"


def test_unconfirmed_machine_draft_never_indexed(
    api: ApiFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """机洗草稿（extracted 未确认）直接发布：QA 对不成块、检索不到（0010）。"""
    client, _ = api
    _login(client)
    _patch_complete_chat(
        monkeypatch,
        result='[{"q": "会员积分可以抵现吗", "a": "积分暂不支持抵现"}]',
    )
    sid = _session_with_turns(client, ["运费险自动生效吗"])
    dialogue_id = client.post(f"/api/service/sessions/{sid}/register").json()["id"]
    assert client.post(f"/api/assets/{dialogue_id}/publish").status_code == 200  # 无必填闸门

    events = _ask(client, client.post("/api/service/sessions").json()["id"], "会员积分可以抵现吗")
    assert events[-1][1]["kind"] == "refusal"  # 草稿未进索引；转写轮次也不含此话题


# ---------- LLM 失败分级：停已接入 -> 重试端点重跑抽取 ----------


def test_reflow_llm_failure_stops_ingested_then_retry_succeeds(
    api: ApiFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = api
    _login(client)

    # 已配置但失败：停已接入 + last_error（会话照常 registered：字节已登记）
    _patch_complete_chat(monkeypatch, error=llm_module.LLMUnavailable("厂商模型暂时不可用"))
    sid = _session_with_turns(client, ["以旧换新补贴怎么领"])
    register_resp = client.post(f"/api/service/sessions/{sid}/register")
    assert register_resp.status_code == 201
    asset = register_resp.json()
    asset_id = asset["id"]
    assert asset["status"] == "ingested"
    assert "LLM QA 抽取失败" in asset["last_error"]
    assert client.get(f"/api/service/sessions/{sid}").json()["status"] == "registered"
    # 已接入态闸门：发布/确认字段都被 409
    assert client.post(f"/api/assets/{asset_id}/publish").status_code == 409
    assert (
        client.patch(
            f"/api/assets/{asset_id}/versions/1/fields", json={"qa_pairs": [{"q": "问", "a": "答"}]}
        ).status_code
        == 409
    )

    # 坏输出分支：重试仍失败（JSON 解析不了），last_error 更新为同一文案
    _patch_complete_chat(monkeypatch, result="抱歉，我不确定能抽什么。")
    retry_bad = client.post(f"/api/assets/{asset_id}/retry-machine-wash")
    assert retry_bad.status_code == 200
    assert retry_bad.json()["status"] == "ingested"
    assert "LLM QA 抽取失败" in retry_bad.json()["last_error"]

    # 修好后重试：重跑 LLM 抽取（不只正则）-> 待人洗 + 草稿 + last_error 清空
    _patch_complete_chat(
        monkeypatch, result='[{"q": "以旧换新补贴怎么领", "a": "回收旧机后在结算页勾选抵扣"}]'
    )
    retry_ok = client.post(f"/api/assets/{asset_id}/retry-machine-wash")
    assert retry_ok.status_code == 200
    body = retry_ok.json()
    assert body["status"] == "pending_review"
    assert body["last_error"] is None
    assert body["versions"][0]["extracted_fields"]["qa_pairs"] == {
        "value": [{"q": "以旧换新补贴怎么领", "a": "回收旧机后在结算页勾选抵扣"}],
        "source": "machine",
    }

    # 确认「没有 QA」（空数组）合法：可发布（dialogue 无必填闸门）
    confirmed = client.patch(f"/api/assets/{asset_id}/versions/1/fields", json={"qa_pairs": []})
    assert confirmed.status_code == 200
    assert confirmed.json()["confirmed_fields"]["qa_pairs"] == {"value": [], "source": "human"}
    assert client.post(f"/api/assets/{asset_id}/publish").status_code == 200


# ---------- PATCH fields 契约：dialogue 合法集=qa_pairs，数组值形状校验 ----------


def test_dialogue_field_validation_contract(api: ApiFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = api
    _login(client)
    _patch_complete_chat(monkeypatch, result="[]")  # 无可抽 -> 弃权草稿，照常待人洗
    sid = _session_with_turns(client, ["礼盒可以手写贺卡吗"])
    asset = client.post(f"/api/service/sessions/{sid}/register").json()
    asset_id = asset["id"]
    assert asset["status"] == "pending_review"
    assert asset["versions"][0]["extracted_fields"]["qa_pairs"] == {"abstained": True}

    # 未知字段 422（对话合法集只有 qa_pairs）
    assert (
        client.patch(f"/api/assets/{asset_id}/versions/1/fields", json={"净含量": "500ml"}).status_code
        == 422
    )
    # 值不是数组 / 项缺 a / q 空串 / 非数组项 -> 422
    assert (
        client.patch(
            f"/api/assets/{asset_id}/versions/1/fields", json={"qa_pairs": "不是数组"}
        ).status_code
        == 422
    )
    assert (
        client.patch(
            f"/api/assets/{asset_id}/versions/1/fields", json={"qa_pairs": [{"q": "只有问"}]}
        ).status_code
        == 422
    )
    assert (
        client.patch(
            f"/api/assets/{asset_id}/versions/1/fields",
            json={"qa_pairs": [{"q": "  ", "a": "答"}]},
        ).status_code
        == 422
    )
    assert (
        client.patch(f"/api/assets/{asset_id}/versions/1/fields", json={"qa_pairs": ["字符串项"]})
    ).status_code == 422

    # 补填一对 -> source=human；正文端点的 401/404 口径
    ok = client.patch(
        f"/api/assets/{asset_id}/versions/1/fields",
        json={"qa_pairs": [{"q": "礼盒带贺卡吗", "a": "可备注手写贺卡内容"}]},
    )
    assert ok.status_code == 200
    client.cookies.clear()
    assert client.get(f"/api/assets/{asset_id}/versions/1/text").status_code == 401
    client.post("/api/auth/login", json={"username": "operator", "password": "operator123"})
    assert client.get(f"/api/assets/{asset_id}/versions/99/text").status_code == 404
    assert client.get("/api/assets/999999/versions/1/text").status_code == 404
