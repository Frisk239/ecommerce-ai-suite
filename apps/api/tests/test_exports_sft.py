"""SFT 数据集导出集成测试（第 97 刀/ADR 0054；需真 PG，见 conftest）。

覆盖（Must 钉）：
- 鉴权：未登录 401（治理台动作，0016 口径）；
- 空数据集：200 + 只有 ``#`` 头的文件，asset_count/sample_count 如实 0，
  零 export_sft 留痕行（「没有数据离开就没有留痕」，0041 同口径）；
- 形状：alpaca 三键 + meta 血缘四键；附件名 ``sft-dataset-YYYYMMDD.jsonl``；
  头五字段（generated_at/exported_by/asset_count/sample_count/license_note）；
- 血缘钉：导出样本的 asset_id/version_no 与库内当前已发布指针一致、
  instruction/output 与 confirmed 值逐字一致；
- 闸门钉：未发布对话的 confirmed qa_pairs 不出现；已发布但只有机洗草稿
  （extracted 未确认）的问答对不出现——只用 confirmed；
- audit 留痕：action='export_sft'、operator=登录者本人（非 0041 系统行「mcp」）。

用例有顺序依赖（模块级共享库）：空数据集用例必须先跑——本文件按定义序执行。
"""

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sse_helpers import parse_sse_events

from suite_api.services import llm as llm_module

ApiFixture = tuple[TestClient, Path]

_CONFIRMED_QA = [
    {"q": "盲盒能挑款吗", "a": "盲盒为随机发货，不支持指定款式"},
    {"q": "店铺支持海外配送吗", "a": "暂不支持海外配送地址"},
]
_DRAFT_QA = [{"q": "会员积分可以抵现吗", "a": "积分暂不支持抵现"}]


def _login(client: TestClient) -> None:
    assert (
        client.post(
            "/api/auth/login", json={"username": "operator", "password": "operator123"}
        ).status_code
        == 200
    )


def _ask(client: TestClient, session_id: int, question: str) -> None:
    """会话发一问（无发布证据 -> 拒答，但两端消息都落库，转写有料）。"""
    with client.stream(
        "POST", f"/api/service/sessions/{session_id}/messages", json={"content": question}
    ) as resp:
        assert resp.status_code == 200
        raw = "".join(resp.iter_text())
    assert parse_sse_events(raw)[-1][1]["kind"] == "refusal"


def _register_dialogue(client: TestClient, question: str) -> int:
    """会话 -> 回流登记 kind=dialogue 资产（conftest 强制空 LLM key：草稿弃权、
    照常推进待人洗——正好是本刀要的「无草稿」基线）。"""
    sid = client.post("/api/service/sessions").json()["id"]
    _ask(client, sid, question)
    resp = client.post(f"/api/service/sessions/{sid}/register")
    assert resp.status_code == 201
    body = resp.json()
    assert body["kind"] == "dialogue"
    assert body["status"] == "pending_review"
    return body["id"]


def _confirm_qa(client: TestClient, asset_id: int, pairs: list[dict[str, str]]) -> None:
    patched = client.patch(f"/api/assets/{asset_id}/versions/1/fields", json={"qa_pairs": pairs})
    assert patched.status_code == 200
    assert patched.json()["confirmed_fields"]["qa_pairs"] == {
        "value": pairs,
        "source": "human",
    }


def _export(client: TestClient) -> Any:
    return client.post("/api/exports/sft")


def _parse_export(resp: Any) -> tuple[dict, list[dict]]:
    """响应体 -> (头, 样本行)。头=首行 ``#`` 后的 JSON；余行逐行 json.loads。"""
    lines = resp.text.splitlines()
    assert lines, "导出文件不能是空字节"
    assert lines[0].startswith("# "), "首行必须是 `# ` 起始的元数据头（ADR 0054）"
    header = json.loads(lines[0][2:])
    samples = [json.loads(line) for line in lines[1:] if line]
    return header, samples


def _audit_rows(client: TestClient, asset_id: int | None = None) -> list[dict]:
    url = "/api/audit" if asset_id is None else f"/api/audit?assetId={asset_id}"
    return client.get(url).json()


# ---------- 鉴权 ----------


def test_export_requires_login(api: ApiFixture) -> None:
    client, _ = api
    client.cookies.clear()
    assert _export(client).status_code == 401


# ---------- 空数据集（须先跑：此时库内尚无已发布对话） ----------


def test_empty_dataset_exports_header_only(api: ApiFixture) -> None:
    client, _ = api
    _login(client)
    resp = _export(client)
    assert resp.status_code == 200
    assert resp.headers["content-disposition"] == (
        f"attachment; filename=sft-dataset-{datetime.now(UTC).strftime('%Y%m%d')}.jsonl"
    )
    header, samples = _parse_export(resp)
    assert header["asset_count"] == 0
    assert header["sample_count"] == 0
    assert header["exported_by"] == "operator"
    assert "本产品不做训练" in header["license_note"]
    assert samples == []
    # 空导出零留痕（0041「if exported」同口径：没有资产离开系统）
    assert all(row["action"] != "export_sft" for row in _audit_rows(client))


# ---------- 形状与血缘钉 ----------


def test_published_confirmed_qa_exported_with_lineage(api: ApiFixture) -> None:
    client, _ = api
    _login(client)
    asset_id = _register_dialogue(client, "盲盒能挑款吗")
    _confirm_qa(client, asset_id, _CONFIRMED_QA)
    assert client.post(f"/api/assets/{asset_id}/publish").status_code == 200

    resp = _export(client)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/x-ndjson")
    assert re.fullmatch(
        r"attachment; filename=sft-dataset-\d{8}\.jsonl", resp.headers["content-disposition"]
    )
    header, samples = _parse_export(resp)
    # 本用例新发布 1 份对话、2 对 confirmed QA（模块库此前无已发布对话）
    assert header["asset_count"] == 1
    assert header["sample_count"] == 2
    datetime.fromisoformat(header["generated_at"])  # ISO 时刻可解析
    assert header["exported_by"] == "operator"

    # 血缘钉：与库内当前已发布指针逐项一致（asset_id/version_no），q/a 逐字一致
    detail = client.get(f"/api/assets/{asset_id}").json()
    assert detail["current_published_version_no"] == 1
    for sample in samples:
        assert set(sample) == {"instruction", "output", "meta"}
        assert set(sample["meta"]) == {"asset_id", "version_no", "source_kind", "title"}
        assert sample["meta"]["asset_id"] == asset_id
        assert sample["meta"]["version_no"] == detail["current_published_version_no"]
        assert sample["meta"]["source_kind"] == "session_backflow"
    by_q = {s["instruction"]: s["output"] for s in samples}
    assert by_q == {pair["q"]: pair["a"] for pair in _CONFIRMED_QA}


# ---------- 闸门钉：未发布不出现、机洗草稿不出现（只用 confirmed） ----------


def test_unpublished_and_draft_only_dialogues_absent(
    api: ApiFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = api
    _login(client)

    # 未发布：confirmed 了但不发布
    unpublished_id = _register_dialogue(client, "以旧换新补贴怎么领")
    _confirm_qa(client, unpublished_id, [{"q": "以旧换新去哪领", "a": "结算页勾选抵扣"}])
    assert client.get(f"/api/assets/{unpublished_id}").json()["status"] == "pending_review"

    # 已发布但只有机洗草稿（extracted 未确认，conftest 空 key 下用替身产草稿）
    async def fake_draft(system_prompt: str, user_prompt: str) -> str:
        return json.dumps(_DRAFT_QA, ensure_ascii=False)

    monkeypatch.setattr(llm_module, "complete_chat", fake_draft)
    draft_only_id = _register_dialogue(client, "会员积分可以抵现吗")
    extracted = client.get(f"/api/assets/{draft_only_id}").json()["versions"][0]["extracted_fields"]
    assert extracted["qa_pairs"] == {"value": _DRAFT_QA, "source": "machine"}
    assert client.post(f"/api/assets/{draft_only_id}/publish").status_code == 200

    resp = _export(client)
    assert resp.status_code == 200
    header, samples = _parse_export(resp)
    assert header["asset_count"] == 1  # 仍只有上一用例那份已发布 confirmed 对话
    assert header["sample_count"] == 2
    assert all(s["meta"]["asset_id"] not in (unpublished_id, draft_only_id) for s in samples)
    body_text = json.dumps(samples, ensure_ascii=False)
    assert "以旧换新去哪领" not in body_text  # 未发布的 confirmed 不出口
    assert "会员积分可以抵现吗" not in body_text  # 草稿（extracted）不出口


# ---------- audit 留痕 ----------


def test_audit_trail_records_operator_not_mcp(api: ApiFixture) -> None:
    client, _ = api
    _login(client)
    me = client.get("/api/auth/me").json()
    resp = _export(client)
    header, samples = _parse_export(resp)
    assert header["sample_count"] > 0
    exported_assets = {s["meta"]["asset_id"] for s in samples}
    rows_by_asset: dict[int, list[dict]] = {
        aid: _audit_rows(client, aid) for aid in exported_assets
    }
    for asset_id in exported_assets:
        sft_rows = [r for r in rows_by_asset[asset_id] if r["action"] == "export_sft"]
        assert sft_rows, f"资产 {asset_id} 缺 export_sft 留痕"
        for row in sft_rows:
            assert row["operator_id"] == me["id"]  # 登录者本人，非系统行「mcp」
            assert row["version_no"] == 1
    # 留痕不混入连接层导出口径（lineage 按 action 分派；这里钉 action 值本身）
    assert all(
        r["action"] != "export" or r["asset_id"] not in exported_assets for r in _audit_rows(client)
    )
