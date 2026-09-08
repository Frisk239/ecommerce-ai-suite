"""运营 Agent 集成测试（真 PG；complete_chat 替身不发外网；第 22 刀/ADR 0041）。

契约（用例按定义序跑，module 级共享库，后序用例不依赖前序清理）：
- 发布一个素材资产挂保温杯 -> 建 run 三步 done，refs 含该资产当前版本；
  素材发布 v2 后新 run refs=v2、旧 run 视图仍 v1（0007 冻结：指针前移不漂移）；
- 无已发布素材的商品（瓶装水）-> compose 兜底商品规格卖点 + detail 诚实披露；
- 空 key -> gen_material failed（read_product done、compose 停 pending）->
  patch LLM 后 retry 从失败步续跑（read_product 未重跑：步序 0 detail 逐字
  不变 + 本轮仅一次 LLM 调用）-> deliver 200 -> 再 deliver 409、retry 409；
- 投放不改资产三态：deliver 前后素材资产 status/version 指针不动；
- 闸门：商品/run 404、未登录 401、无失败步 retry 409。
"""

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from suite_api.services import llm as llm_module

ApiFixture = tuple[TestClient, Path]

OPS_TITLE = "钛钢保温杯：一杯守住温度"
OPS_BODY = "早九点的热水，下午三点还烫口。钛钢保温杯，通勤车载两相宜。"


def _login(client: TestClient) -> None:
    assert client.post(
        "/api/auth/login", json={"username": "operator", "password": "operator123"}
    ).status_code == 200


def _product_id(client: TestClient, name: str) -> int:
    products = client.get("/api/products").json()
    return next(p["id"] for p in products if p["name"] == name)


def _patch_complete_chat(
    monkeypatch: pytest.MonkeyPatch,
    result: str | None = None,
    error: Exception | None = None,
) -> list[dict[str, str]]:
    """替换 llm.complete_chat；返回 prompt 捕获记录（同素材/考核测试先例）。"""
    calls: list[dict[str, str]] = []

    async def fake(system_prompt: str, user_prompt: str) -> Any:
        calls.append({"system": system_prompt, "user": user_prompt})
        if error is not None:
            raise error
        assert result is not None
        return result

    monkeypatch.setattr(llm_module, "complete_chat", fake)
    return calls


def _ops_json() -> str:
    return json.dumps({"title": OPS_TITLE, "body": OPS_BODY}, ensure_ascii=False)


def _publish_cup_material(client: TestClient, monkeypatch: pytest.MonkeyPatch, cup_id: int) -> int:
    """素材全链（第 17 刀通道）：生成->抽检登记->发布，返回素材资产 id。"""
    _patch_complete_chat(
        monkeypatch,
        result=json.dumps(
            {
                "title": "钛钢保温杯：一杯守住温度",
                "content": "卖点一：钛钢保温杯双层真空，持久保温12小时。\n材质：钛钢",
            },
            ensure_ascii=False,
        ),
    )
    task = client.post("/api/material/tasks", json={"product_id": cup_id}).json()
    assert task["status"] == "pending_qc"
    asset_id = client.post(f"/api/material/tasks/{task['id']}/approve").json()["asset_id"]
    # 素材无规格必填闸门（第 17 刀）：登记后直接可发布
    assert client.post(f"/api/assets/{asset_id}/publish").status_code == 200
    return asset_id


# ---------- 有已发布素材：三步 done + refs 冻结当前版本 ----------


def test_run_with_published_material_and_ref_freeze(
    api: ApiFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = api
    _login(client)
    cup_id = _product_id(client, "钛钢保温杯")
    asset_id = _publish_cup_material(client, monkeypatch, cup_id)

    calls = _patch_complete_chat(monkeypatch, result=_ops_json())
    created = client.post("/api/ops/runs", json={"product_id": cup_id})
    assert created.status_code == 201
    run = created.json()
    assert run["product_name"] == "钛钢保温杯"
    assert [s["status"] for s in run["steps"]] == ["done", "done", "done"]
    assert [s["key"] for s in run["steps"]] == ["read_product", "gen_material", "compose"]
    assert run["steps"][1]["via"] == "厂商模型"
    assert "钛钢保温杯" in calls[0]["user"]  # 商品事实进生成 prompt
    assert run["output"]["title"] == OPS_TITLE
    assert run["output"]["body"] == OPS_BODY  # 有引用：正文=厂商草稿本体
    assert {"asset_id": asset_id, "version_no": 1} in run["output"]["refs"]
    assert run["delivered_at"] is None

    # 列表倒序：最新在前，视图与建时响应一致
    listed = client.get("/api/ops/runs").json()
    assert listed[0]["id"] == run["id"]
    assert listed[0] == run

    # 0007 冻结口径：素材发布 v2 后，新 run 引用 v2，旧 run 视图仍 v1（不漂移）
    assert client.post(f"/api/assets/{asset_id}/revisions").status_code == 201
    assert client.post(f"/api/assets/{asset_id}/publish").status_code == 200
    second = client.post("/api/ops/runs", json={"product_id": cup_id}).json()
    assert {"asset_id": asset_id, "version_no": 2} in second["output"]["refs"]
    first_again = next(r for r in client.get("/api/ops/runs").json() if r["id"] == run["id"])
    assert {"asset_id": asset_id, "version_no": 1} in first_again["output"]["refs"]
    assert {"asset_id": asset_id, "version_no": 2} not in first_again["output"]["refs"]


# ---------- 无已发布素材：compose 兜底 + 诚实披露 ----------


def test_run_without_published_material_falls_back(
    api: ApiFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = api
    _login(client)
    _patch_complete_chat(monkeypatch, result=_ops_json())
    water_id = _product_id(client, "瓶装水")
    run = client.post("/api/ops/runs", json={"product_id": water_id}).json()

    assert [s["status"] for s in run["steps"]] == ["done", "done", "done"]
    assert run["output"]["refs"] == []
    compose = run["steps"][2]
    assert compose["detail"] == "无已发布素材，正文由商品规格组装"
    assert "无已发布素材，正文由商品规格组装" in run["output"]["body"]


# ---------- 失败续跑 + 投放状态机 ----------


def test_failed_run_retry_resume_then_deliver(
    api: ApiFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = api
    _login(client)
    cup_id = _product_id(client, "钛钢保温杯")

    # 空 key（conftest 强制）：gen_material failed，后续步停 pending、无产出
    run = client.post("/api/ops/runs", json={"product_id": cup_id}).json()
    assert [s["status"] for s in run["steps"]] == ["done", "failed", "pending"]
    assert "生成不可用" in run["steps"][1]["detail"]
    assert run["output"] is None
    assert client.post(f"/api/ops/runs/{run['id']}/deliver").status_code == 409  # 未完成不可投放

    # 配好 key 重试：从失败步续跑——前序 done 不重跑（步 0 逐字不变 + 仅一次 LLM）
    read_step_before = run["steps"][0]
    calls = _patch_complete_chat(monkeypatch, result=_ops_json())
    retried = client.post(f"/api/ops/runs/{run['id']}/retry")
    assert retried.status_code == 200
    body = retried.json()
    assert [s["status"] for s in body["steps"]] == ["done", "done", "done"]
    assert body["steps"][0] == read_step_before
    assert len(calls) == 1
    assert body["output"]["title"] == OPS_TITLE
    # 前面用例已发布挂杯素材：续跑后的 compose 正常拿到引用
    assert body["output"]["refs"]

    # 投放前记录素材资产指针视图，投放后逐项对照（不改资产三态）
    assets_before = client.get("/api/assets", params={"kind": "material"}).json()
    delivered = client.post(f"/api/ops/runs/{body['id']}/deliver")
    assert delivered.status_code == 200
    assert delivered.json()["delivered_at"] is not None
    assert client.post(f"/api/ops/runs/{body['id']}/deliver").status_code == 409  # 再投 409
    assert client.post(f"/api/ops/runs/{body['id']}/retry").status_code == 409  # 已投放不可重试
    assets_after = client.get("/api/assets", params={"kind": "material"}).json()
    assert assets_after == assets_before


# ---------- 闸门与错误契约 ----------


def test_ops_gate_and_error_contract(api: ApiFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = api
    _login(client)
    assert client.post("/api/ops/runs", json={"product_id": 9999}).status_code == 404
    assert client.post("/api/ops/runs", json={}).status_code == 422
    assert client.post("/api/ops/runs/999999/retry").status_code == 404
    assert client.post("/api/ops/runs/999999/deliver").status_code == 404

    _patch_complete_chat(monkeypatch, result=_ops_json())
    cup_id = _product_id(client, "钛钢保温杯")
    done_run = client.post("/api/ops/runs", json={"product_id": cup_id}).json()
    # 三步全 done：没有失败步可重试
    assert client.post(f"/api/ops/runs/{done_run['id']}/retry").status_code == 409

    # 未登录 401（编排是操作者动作）
    client.cookies.clear()
    assert client.get("/api/ops/runs").status_code == 401
    assert client.post("/api/ops/runs", json={"product_id": cup_id}).status_code == 401
    assert client.post(f"/api/ops/runs/{done_run['id']}/deliver").status_code == 401
    _login(client)
