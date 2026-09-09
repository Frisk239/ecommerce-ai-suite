"""第 33 刀：真实类目 spec_schema 上的发布闸门、写回、检索（需 PG）。"""

from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from suite_api.models import Product, RetrievalChunk
from suite_api.services.category_schema import schema_for_category
from suite_api.services.retrieval import retrieve

ApiFixture = tuple[TestClient, Path]

_PHONE_DOC = "【智能手机规格】\n品牌：Apple\n存储容量：256GB\n".encode()
_OFF_FIXTURE = Path(__file__).resolve().parents[3] / "scripts" / "realdata" / "samples" / "off_dump_sample.tsv"


def _login(client: TestClient) -> None:
    assert client.post(
        "/api/auth/login", json={"username": "operator", "password": "operator123"}
    ).status_code == 200


def test_phone_category_gate_writeback_retrieve(api: ApiFixture) -> None:
    client, _ = api
    _login(client)
    schema = schema_for_category("智能手机")
    with client.app.state.session_factory() as db:
        phone = Product(
            name="测试旗舰手机",
            category="智能手机",
            spec_schema=schema,
            spec_values={},
            stock=3,
        )
        db.add(phone)
        db.commit()
        phone_id = phone.id

    uploaded = client.post(
        "/api/assets/register",
        files={"file": ("phone.txt", _PHONE_DOC, "text/plain")},
        data={"productId": str(phone_id), "title": "测试旗舰手机规格"},
    )
    assert uploaded.status_code == 201
    asset_id = uploaded.json()["id"]

    gate = client.post(f"/api/assets/{asset_id}/publish")
    assert gate.status_code == 422
    detail = gate.json()["detail"]
    assert "品牌" in detail["missing"] + detail["unconfirmed"]

    patched = client.patch(
        f"/api/assets/{asset_id}/versions/1/fields",
        json={"品牌": "Apple", "存储容量": "256GB"},
    )
    assert patched.status_code == 200
    published = client.post(f"/api/assets/{asset_id}/publish")
    assert published.status_code == 200
    product = client.get(f"/api/products/{phone_id}").json()
    assert product["spec_values"]["品牌"] == {
        "value": "Apple",
        "source": {"asset_id": asset_id, "version": 1},
    }

    with client.app.state.session_factory() as db:
        chunks = db.scalars(
            select(RetrievalChunk.chunk).where(RetrievalChunk.asset_id == asset_id)
        ).all()
        assert any("Apple" in chunk for chunk in chunks)
        hits = retrieve(db, "测试旗舰手机的品牌")
        assert any(hit["asset_id"] == asset_id for hit in hits)


def test_off_dump_row_register_publish_writeback_retrieve(api: ApiFixture) -> None:
    """dump 清洗行 → 登记 dump 原文规格 → 确认净含量 → 发布写回 → 检索命中。"""
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts" / "realdata"))
    import load_openfoodfacts as loff

    rows = loff.load_fixture_tsv(_OFF_FIXTURE, limit=1)
    assert rows
    row = rows[0]
    client, _ = api
    _login(client)
    with client.app.state.session_factory() as db:
        product = Product(
            name=row["name"],
            category=row["category"],
            spec_schema=row["spec_schema"],
            spec_values={},
            stock=12,
        )
        db.add(product)
        db.commit()
        product_id = product.id

    spec = loff.spec_text_from_row(row).encode("utf-8")
    uploaded = client.post(
        "/api/assets/register",
        files={"file": ("off-spec.txt", spec, "text/plain")},
        data={"productId": str(product_id), "title": f"{row['name']} 规格（OFF）"},
    )
    assert uploaded.status_code == 201
    asset = uploaded.json()
    asset_id = asset["id"]
    extracted = asset["versions"][0]["extracted_fields"]
    assert extracted["净含量"]["value"] == "550ml"
    assert "保质期" not in extracted  # schema 不含未出现在 dump 里的字段

    gate = client.post(f"/api/assets/{asset_id}/publish")
    assert gate.status_code == 422  # 未确认净含量

    patched = client.patch(
        f"/api/assets/{asset_id}/versions/1/fields",
        json={"净含量": "550ml"},
    )
    assert patched.status_code == 200
    published = client.post(f"/api/assets/{asset_id}/publish")
    assert published.status_code == 200
    product_body = client.get(f"/api/products/{product_id}").json()
    assert product_body["spec_values"]["净含量"]["value"] == "550ml"
    assert product_body["spec_values"]["净含量"]["source"]["asset_id"] == asset_id

    with client.app.state.session_factory() as db:
        hits = retrieve(db, "农夫山泉 净含量")
        assert any(hit["asset_id"] == asset_id for hit in hits)
