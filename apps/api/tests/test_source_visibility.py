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
