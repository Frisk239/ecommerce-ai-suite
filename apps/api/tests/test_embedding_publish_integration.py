"""发布补写 embedding 集成测试（第 105 刀，向量基础设施 A1；真 PG + 真发布路径）。

三态钉（施工单 Must 3/5——「embedding 失败块照写」是发布不被云调用阻塞的钉子）：
- **未配置**（conftest 强制空 EMBED_API_KEY）：发布 200、块全落库、embedding 全
  NULL、零外网调用（is_configured=False 直接短路）；
- **替身成功**：块带 1024 维 embedding（vector_dims 直查）且替身收到的输入恰为
  该版本的切块文本（对位与批量 UPDATE 都走真路径）；
- **替身失败**：embed_texts 抛 EmbeddingUnavailable，发布仍 200、块照写、
  embedding NULL（发布不被 embedding 失败阻塞——回填脚本兜底）。

检索零变化由既有 retrieval 测试与评测尺兜住（本刀不动 retrieve）。
"""

import os
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

import suite_api.services.embedding as embedding_service
from suite_api.services.embedding import EMBEDDING_DIM, EmbeddingUnavailable

ApiFixture = tuple[TestClient, Path]

_POLICY_DOC = "退货政策\n自签收之日起15天内可无理由退货。\n退货运费由商家承担。".encode()


def _url() -> str:
    return os.environ["SUITE_TEST_DATABASE_URL"]


def _login(client: TestClient) -> None:
    assert (
        client.post(
            "/api/auth/login", json={"username": "operator", "password": "operator123"}
        ).status_code
        == 200
    )


def _register_and_publish(client: TestClient, *, title: str) -> int:
    """登记并发布一份不挂商品的文档（无必填闸），返回 asset_id。"""
    resp = client.post(
        "/api/assets/register",
        files={"file": ("policy.txt", _POLICY_DOC, "text/plain")},
        data={"source": "upload", "title": title},
    )
    assert resp.status_code == 201, resp.text
    asset_id = resp.json()["id"]
    published = client.post(f"/api/assets/{asset_id}/publish")
    assert published.status_code == 200, published.text
    return asset_id


def _chunk_stats(asset_id: int) -> tuple[int, int]:
    """(该资产块总数, embedding 非 NULL 条数)。"""
    with psycopg.connect(_url()) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT count(*), count(embedding) FROM retrieval_chunks WHERE asset_id = %s",
            (asset_id,),
        )
        total, embedded = cur.fetchone()
    return int(total), int(embedded)


def test_publish_without_key_writes_chunks_with_null_embeddings(api: ApiFixture) -> None:
    client, _ = api
    _login(client)
    asset_id = _register_and_publish(client, title="退货政策·无 key 形态")
    total, embedded = _chunk_stats(asset_id)
    assert total > 0  # 块照写（发布语义不变）
    assert embedded == 0  # 未配置 = 零云调用，embedding 全 NULL


def test_publish_embeds_chunks_via_configured_stub(api: ApiFixture) -> None:
    client, _ = api
    _login(client)
    seen_inputs: list[list[str]] = []

    def _fake_embed(texts: list[str]) -> list[list[float]]:
        seen_inputs.append(list(texts))
        # 首维带输入序号：UPDATE 若对错位，vector_dims 虽对但首维可查错
        return [[float(i), 0.5] + [0.0] * (EMBEDDING_DIM - 2) for i, _t in enumerate(texts)]

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(embedding_service, "is_configured", lambda: True)
    monkeypatch.setattr(embedding_service, "embed_texts", _fake_embed)
    try:
        asset_id = _register_and_publish(client, title="退货政策·替身嵌入")
    finally:
        monkeypatch.undo()
    with psycopg.connect(_url()) as conn, conn.cursor() as cur:
        # vector 不支持数组下标也不能直 cast float8[]：取 text 形态（'[0,0.5,...]'）
        # 在 Python 侧解析首维（UPDATE 若对错位，首维可查错）
        cur.execute(
            "SELECT chunk, vector_dims(embedding), embedding::text"
            " FROM retrieval_chunks WHERE asset_id = %s ORDER BY seq",
            (asset_id,),
        )
        rows = cur.fetchall()
    assert rows
    chunks = seen_inputs[0]  # 替身收到的输入 = 该版本的切块文本全集
    assert len(rows) == len(chunks)
    for (chunk, dims, vector_text), expected, index in zip(
        rows, chunks, range(len(chunks)), strict=True
    ):
        first = float(vector_text.strip("[]").split(",")[0])
        assert chunk == expected  # 块文本对位
        assert dims == EMBEDDING_DIM  # vector(1024) 列真吃 1024 维
        assert first == float(index)  # seq 对位：第 i 块拿第 i 条向量


def test_publish_not_blocked_by_embed_failure(api: ApiFixture) -> None:
    client, _ = api
    _login(client)
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(embedding_service, "is_configured", lambda: True)

    def _boom(texts: list[str]) -> list[list[float]]:
        raise EmbeddingUnavailable("嵌入服务暂时不可用")

    monkeypatch.setattr(embedding_service, "embed_texts", _boom)
    try:
        asset_id = _register_and_publish(client, title="退货政策·嵌入失败形态")
    finally:
        monkeypatch.undo()
    total, embedded = _chunk_stats(asset_id)
    assert total > 0  # 块照写（发布已成功）
    assert embedded == 0  # embedding 留 NULL（回填脚本可重跑兜底）
