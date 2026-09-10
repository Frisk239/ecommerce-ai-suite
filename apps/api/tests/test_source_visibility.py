"""第 50 刀：来源可见 + 演示价回填的接口面钉子（真 PG；回填本身由
`test_migration_backfill.py::test_upgrade_0026_...` 钉迁移层）。

覆盖：
- 种子商品的来源与演示价（`source_kind='seed'`、价未被回填改动）。
- `ProductOut` 带 `source_kind`（只读展示口）。
- POST/PATCH **不收** `source_kind`（来源是既成事实，运营改不了）。
"""

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

ApiFixture = tuple[TestClient, Path]


def _login(client: TestClient) -> None:
    assert (
        client.post(
            "/api/auth/login", json={"username": "operator", "password": "operator123"}
        ).status_code
        == 200
    )


def _products(client: TestClient) -> dict[str, dict[str, Any]]:
    _login(client)
    rows = client.get("/api/products").json()
    return {row["name"]: row for row in rows}


def test_product_out_carries_source_kind(api: ApiFixture) -> None:
    client, _ = api
    products = _products(client)
    assert products, "种子商品应存在"
    for row in products.values():
        assert "source_kind" in row  # 字段恒在（未回填时为 None）
    # 种子商品：来源 seed + 第 41 刀的演示价原样（0026 不回写已定价行）
    water = products["瓶装水"]
    assert water["source_kind"] == "seed"
    assert water["price_cents"] == 300
    cup = products["钛钢保温杯"]
    assert cup["source_kind"] == "seed"
    assert cup["price_cents"] == 12900


def test_created_product_has_no_source_kind(api: ApiFixture) -> None:
    """手建商品来源为 None（来源只由导入/回填写成），且 POST body 里的
    source_kind 被忽略——表单改不了溯源。"""
    client, _ = api
    _login(client)
    created = client.post(
        "/api/products",
        json={"name": "手建来源测试品", "category": "食品", "source_kind": "seed"},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["source_kind"] is None  # 不接受客户端自报来源
    assert body["price_cents"] is None


def test_patch_does_not_change_source_kind(api: ApiFixture) -> None:
    client, _ = api
    _login(client)
    created = client.post("/api/products", json={"name": "改来源测试品", "category": "食品"})
    product_id = created.json()["id"]
    patched = client.patch(
        f"/api/products/{product_id}", json={"price_cents": 1234, "source_kind": "open_dataset"}
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["source_kind"] is None  # 多传的字段被忽略
    assert patched.json()["price_cents"] == 1234


def test_assets_can_be_filtered_by_source_kind(api: ApiFixture) -> None:
    """第 51 刀：来源过滤（工作队列首屏被导入货淹掉，运营要能只看某一来源）。

    服务端过滤（`?source_kind=`）+ 未知取值 422（别让拼错的来源静默给空列表）。
    """
    client, _ = api
    _login(client)
    # 本模块的夹具只建商品，先登记一份资产（register 端点定值 source_kind=upload）
    registered = client.post(
        "/api/assets/register",
        files={"file": ("filter.txt", "筛选用例正文".encode(), "text/plain")},
        data={"title": "来源筛选用例资产"},
    )
    assert registered.status_code == 201
    all_rows = client.get("/api/assets").json()
    assert all_rows, "登记后应至少有一条资产"
    by_source: dict[str, int] = {}
    for row in all_rows:
        by_source[row["source_kind"]] = by_source.get(row["source_kind"], 0) + 1

    for source, expected in by_source.items():
        rows = client.get("/api/assets", params={"source_kind": source}).json()
        assert len(rows) == expected
        assert {row["source_kind"] for row in rows} == {source}

    # **真钉子**：合法但当前库里没有的来源必须返回空——若后端忽略该参数，
    # 这条会拿到全量非空而失败（只用「按现有来源分组比对」是同义反复：
    # 本模块夹具只建商品，唯一资产就是上面那条 upload）
    empty = client.get("/api/assets", params={"source_kind": "seed"})
    assert empty.status_code == 200
    assert empty.json() == []

    bad = client.get("/api/assets", params={"source_kind": "not_a_source"})
    assert bad.status_code == 422
    assert "source_kind" in bad.json()["detail"]


def test_assets_source_filter_combines_with_status(api: ApiFixture) -> None:
    """组合过滤：与 status 并存。**自带数据**（不靠前序用例的副产物；否则空库下
    `all([])` 恒真，删掉过滤也绿）。"""
    client, _ = api
    _login(client)
    registered = client.post(
        "/api/assets/register",
        files={"file": ("combo.txt", "组合过滤用例正文".encode(), "text/plain")},
        data={"title": "组合过滤用例资产"},
    )
    assert registered.status_code == 201

    rows = client.get(
        "/api/assets", params={"source_kind": "upload", "status": "pending_review"}
    ).json()
    assert rows, "组合过滤不应为空（本用例刚登记了一条 upload + pending_review）"
    assert all(row["source_kind"] == "upload" for row in rows)
    assert all(row["status"] == "pending_review" for row in rows)
