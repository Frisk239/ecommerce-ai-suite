"""直播切片拣选单元测试（不依赖 DB；第 18 刀/ADR 0014/0015/0039）。

覆盖：
- 登记字节格式：``[start-end] 转写`` 文本（transcript_bytes 纯函数 +
  pick_candidates 正路径实际落存储的字节与对象键 clips/ 前缀）；
- 机洗字段集分派：video 恒空（即便挂商品也不跑规格正则，防口语转写误抽）；
- 拣选状态机：不存在 404、已登记再拣 409、**批量含已登记整体 409 且一个
  字节都不落**（校验先于第一个 put_bytes）、重复 id 去重；
- pick 视图收口：候选置 registered + registered_asset_id 回执锚、资产
  kind=video / source=clip_pick / 待人洗（机洗弃权推进）；
- 入参校验：ClipPickIn 空数组 422（pydantic min_length）。
"""

from typing import Any

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from suite_api.models import Asset, ClipCandidate, Product
from suite_api.routes.clips import ClipPickIn
from suite_api.services.clips import PENDING, pick_candidates, transcript_bytes
from suite_api.services.registration import (
    PENDING_REVIEW,
    machine_wash_field_names,
    make_object_key,
)


def _product() -> Product:
    return Product(
        id=1,
        name="钛钢保温杯",
        category="器皿",
        spec_schema={"净含量": {"required": True}, "材质": {"required": True}},
    )


def _candidate(candidate_id: int = 1, status: str = PENDING, **overrides: Any) -> ClipCandidate:
    fields: dict[str, Any] = {
        "id": candidate_id,
        "product_id": 1,
        "status": status,
        "timecode_start": "00:02:14",
        "timecode_end": "00:02:52",
        "transcript": "现场实测保温：钛钢内胆一体成型，没有焊缝。",
        "source_video_label": "2026-09-04 「钛钢保温杯 × 饮用水」专场·录像 24 分钟",
    }
    fields.update(overrides)
    return ClipCandidate(**fields)


class _FakeClipDb:
    """只喂 pick_candidates→register_asset 用到的最小会话面：
    get(ClipCandidate/Product)/add/flush（给 Asset 编主键）/commit 记账。

    CAS 占位（审计刀 9）走 `services.clips._claim_candidate` 缝：这些纯单测用
    monkeypatch 把缝钉成「总能抢到」，不必在假会话上模拟 SQLAlchemy 的 Update 语句
    （并发语义由真库集成用例兜）。"""

    def __init__(self, candidates: list[ClipCandidate], product: Product | None = None) -> None:
        self.candidates = {c.id: c for c in candidates}
        self.product = product
        self.assets: list[Asset] = []
        self.commits = 0
        self._next_asset_id = 500

    def get(self, model: Any, pk: Any) -> Any:
        if model is ClipCandidate:
            return self.candidates.get(pk)
        if model is Product and self.product is not None and pk == self.product.id:
            return self.product
        return None

    def add(self, obj: Any) -> None:
        if isinstance(obj, Asset):
            self.assets.append(obj)

    def flush(self) -> None:
        for asset in self.assets:
            if asset.id is None:
                self._next_asset_id += 1
                asset.id = self._next_asset_id

    def commit(self) -> None:
        self.commits += 1


class _MemoryStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_bytes(self, key: str, data: bytes) -> None:
        self.objects[key] = data

    def get_bytes(self, key: str) -> bytes:
        if key not in self.objects:
            raise FileNotFoundError(key)
        return self.objects[key]


# ---------- 登记字节格式与对象键（0039） ----------


def test_transcript_bytes_is_timecode_prefixed_text() -> None:
    candidate = _candidate()
    assert transcript_bytes(candidate) == (f"[00:02:14-00:02:52] {candidate.transcript}").encode()


@pytest.mark.parametrize(
    ("kind", "prefix", "extension"),
    [
        ("video", "clips", ".mp4"),  # 第 46 刀：video 字节可是真切 mp4
        ("dialogue", "dialogue", ".txt"),
        ("document", "documents", ".txt"),
        ("material", "documents", ".txt"),
    ],
)
def test_make_object_key_prefix_and_extension_dispatch(
    kind: str, prefix: str, extension: str
) -> None:
    key = make_object_key(kind, b"x")
    assert key.startswith(f"{prefix}/")
    assert key.endswith(extension)


# ---------- 机洗字段集：video 恒空（挂商品也不跑规格正则） ----------


def test_video_field_set_is_empty_even_with_product() -> None:
    assert machine_wash_field_names("video", _product()) == []
    assert machine_wash_field_names("video", None) == []


def _claim_always_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    """CAS 缝：纯单测不模拟并发，钉成「总能抢到」（=1 影响行数）。"""
    monkeypatch.setattr("suite_api.services.clips._claim_candidate", lambda _db, _cid: 1)


# ---------- 正路径：批量登记收口 ----------


def test_pick_candidates_registers_video_assets(monkeypatch: pytest.MonkeyPatch) -> None:
    _claim_always_wins(monkeypatch)
    c1 = _candidate(1)
    c2 = _candidate(
        2, timecode_start="00:14:05", timecode_end="00:14:38", transcript="整箱24瓶带走。"
    )
    db = _FakeClipDb([c1, c2], product=_product())
    storage = _MemoryStorage()

    assets = pick_candidates(db, storage, [1, 2])  # type: ignore[arg-type]

    assert [a.kind for a in assets] == ["video", "video"]
    assert [a.source_kind for a in assets] == ["clip_pick", "clip_pick"]
    assert [a.status for a in assets] == [PENDING_REVIEW, PENDING_REVIEW]
    assert [a.product_id for a in assets] == [1, 1]
    # 回执锚：候选 registered 且指向登记出的资产
    assert (c1.status, c1.registered_asset_id) == ("registered", assets[0].id)
    assert (c2.status, c2.registered_asset_id) == ("registered", assets[1].id)
    # 字节与对象键：各一条 clips/ 前缀对象，内容 = [start-end] 转写
    assert len(storage.objects) == 2
    assert all(key.startswith("clips/") for key in storage.objects)
    assert transcript_bytes(c1) in storage.objects.values()
    assert transcript_bytes(c2) in storage.objects.values()
    # title=转写截断 60 字
    assert assets[0].title == c1.transcript[:60]
    assert assets[1].title == "整箱24瓶带走。"


def test_pick_title_truncates_transcript_to_60_chars(monkeypatch: pytest.MonkeyPatch) -> None:
    _claim_always_wins(monkeypatch)
    long_transcript = "杯" * 100
    candidate = _candidate(1, transcript=long_transcript)
    db = _FakeClipDb([candidate], product=_product())
    assets = pick_candidates(db, _MemoryStorage(), [1])  # type: ignore[arg-type]
    assert assets[0].title == long_transcript[:60] == "杯" * 60


def test_pick_dedupes_repeated_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    _claim_always_wins(monkeypatch)
    candidate = _candidate(1)
    db = _FakeClipDb([candidate], product=_product())
    storage = _MemoryStorage()
    assets = pick_candidates(db, storage, [1, 1])  # type: ignore[arg-type]
    assert len(assets) == 1
    assert len(storage.objects) == 1  # 同一候选勾两次=登记一次


# ---------- 404/409 与批量原子性 ----------


def test_pick_missing_candidate_is_404_before_any_io() -> None:
    db = _FakeClipDb([_candidate(1)], product=_product())
    storage = _MemoryStorage()
    with pytest.raises(HTTPException) as exc:
        pick_candidates(db, storage, [1, 999])  # type: ignore[arg-type]
    assert exc.value.status_code == 404
    assert storage.objects == {}  # 校验先于第一个字节落库
    assert db.assets == []


def test_pick_already_registered_is_409() -> None:
    candidate = _candidate(1, status="registered")
    db = _FakeClipDb([candidate], product=_product())
    with pytest.raises(HTTPException) as exc:
        pick_candidates(db, _MemoryStorage(), [1])  # type: ignore[arg-type]
    assert exc.value.status_code == 409
    assert "不可重复拣选" in exc.value.detail


def test_pick_batch_with_one_registered_fails_atomically() -> None:
    """批量含已登记 → 整体 409，事务不落：pending 那条也保持 pending、无字节无资产。"""
    pending, registered = _candidate(1), _candidate(2, status="registered")
    db = _FakeClipDb([pending, registered], product=_product())
    storage = _MemoryStorage()
    with pytest.raises(HTTPException) as exc:
        pick_candidates(db, storage, [1, 2])  # type: ignore[arg-type]
    assert exc.value.status_code == 409
    assert storage.objects == {}
    assert db.assets == []
    assert (pending.status, pending.registered_asset_id) == (PENDING, None)


# ---------- 路由入参形状 ----------


def test_pick_in_rejects_empty_ids() -> None:
    with pytest.raises(ValidationError):
        ClipPickIn(ids=[])
    assert ClipPickIn(ids=[1]).ids == [1]
