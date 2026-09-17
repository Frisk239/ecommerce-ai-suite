"""登记共享服务单元测试：source_kind 枚举校验（0025：服务端定值，应用层把关）
+ 机洗字段集按种类分派（第 12 刀/ADR 0035：dialogue -> qa_pairs）
+ 事务纪律（第 16 刀/审计刀 3 P1#2：机洗调 LLM 前 commit，不 idle-in-transaction）。

幂等缺口判定属 DB 交互（SELECT 同文 open 缺口），无可提炼的无副作用纯函数，
由集成测试钉死（test_service_integration 的缺口契约组）。
"""

import base64
from typing import Any

import pytest

from suite_api.models import AssetVersion, Product
from suite_api.services.registration import (
    PENDING_REVIEW,
    SOURCE_KINDS,
    machine_wash_field_names,
    register_asset,
    validate_source_kind,
)


@pytest.mark.parametrize("source_kind", SOURCE_KINDS)
def test_all_source_kinds_accepted(source_kind: str) -> None:
    assert validate_source_kind(source_kind) == source_kind


@pytest.mark.parametrize(
    "bad",
    ["", "upload ", "上传", "Session_Backflow", "session-backflow", "unknown"],
)
def test_bad_source_kind_rejected(bad: str) -> None:
    with pytest.raises(ValueError, match="来源种类"):
        validate_source_kind(bad)


def test_register_asset_validates_source_before_any_io() -> None:
    """坏来源在碰存储/数据库之前失败：可无 db/storage 直调（路由层转 422）。"""
    with pytest.raises(ValueError, match="来源种类"):
        register_asset(
            None,  # type: ignore[arg-type]  # 校验先于任何 DB 访问
            None,  # type: ignore[arg-type]  # 校验先于任何对象存储写入
            kind="document",
            title=None,
            content_bytes=b"x",
            filename=None,
            product_id=None,
            source_kind="bogus",
        )


# ---------- 机洗字段集分派（第 12 刀/ADR 0035） ----------


def test_dialogue_field_set_is_only_qa_pairs() -> None:
    # 对话种类的字段集就是 qa_pairs 一个字段（LLM 抽取），与是否挂商品无关
    assert machine_wash_field_names("dialogue", None) == ["qa_pairs"]


def test_image_field_set_is_only_image_description() -> None:
    """第 94a 刀（ADR 0051）：图片的唯一治理字段是「图片描述」（VLM 草稿/人洗
    补写），与是否挂商品无关——这个字段集同时是人洗 PATCH 的合法集合，
    描述必须可确认（否则人洗无路可走）。"""
    product = Product(
        name="显示器",
        category="显示器",
        spec_schema={"品牌": {"required": True}, "图片": {"required": False}},
    )
    assert machine_wash_field_names("image", None) == ["图片描述"]
    assert machine_wash_field_names("image", product) == ["图片描述"]  # 不跑商品规格正则


def test_document_field_set_follows_spec_schema() -> None:
    product = Product(
        name="瓶装水",
        category="食品",
        spec_schema={"净含量": {"required": True}, "保质期": {"required": True}},
    )
    assert machine_wash_field_names("document", product) == ["净含量", "保质期"]


def test_unlinked_document_field_set_is_empty() -> None:
    assert machine_wash_field_names("document", None) == []


def test_document_field_set_drops_qa_pairs_name_collision() -> None:
    """spec_schema 撞名防御：QA 是种类级语义（kind=对话才有 qa_pairs），
    文档字段集即便 schema 混入 qa_pairs 键也滤掉——机洗与人洗闸门都不放行。"""
    product = Product(name="怪键", category="测试", spec_schema={"净含量": {}, "qa_pairs": {}})
    assert machine_wash_field_names("document", product) == ["净含量"]


# ---------- 事务纪律（第 16 刀，审计刀 3 P1#2）：LLM 前 commit ----------


class _TxnRecordingDb:
    """Session 替身：模拟 autobegin/commit 的事务状态变化并记录调用序列。"""

    def __init__(self) -> None:
        self.events: list[str] = []
        self._in_tx = False

    def _begin(self) -> None:
        if not self._in_tx:
            self._in_tx = True
            self.events.append("begin")

    def add(self, _obj: Any) -> None:
        self._begin()

    def flush(self) -> None:
        self._begin()
        self.events.append("flush")

    def commit(self) -> None:
        self.events.append("commit")
        self._in_tx = False

    def in_transaction(self) -> bool:
        return self._in_tx

    def refresh(self, _obj: Any) -> None:
        pass  # retry 端点收口后 refresh——替身无需做事


class _MemoryStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_bytes(self, key: str, data: bytes) -> None:
        self.objects[key] = data

    def get_bytes(self, key: str) -> bytes:
        if key not in self.objects:
            raise FileNotFoundError(key)
        return self.objects[key]


_TRANSCRIPT = "顾客：退款几天到账？\n客服：质检后3个工作日到账".encode()


def test_register_asset_dialogue_releases_transaction_before_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """机洗（含 LLM QA 抽取，≤20s）不得在持有写事务状态下跑：register_asset
    先 flush+commit 落登记行，再调 complete_chat——调用时刻 in_transaction 为
    False、commit 先于 LLM（对齐第 11 刀 run_ask 纪律；先例
    test_run_ask_commits_before_llm_stream 的 commit 序列断言同款思路）。"""
    db = _TxnRecordingDb()
    storage = _MemoryStorage()
    seen: dict[str, Any] = {}

    async def fake_complete_chat(_system: str, _user: str) -> str:
        seen["in_transaction"] = db.in_transaction()
        seen["events_at_call"] = list(db.events)
        return '[{"q": "退款几天到账", "a": "质检后3个工作日到账"}]'

    monkeypatch.setattr("suite_api.services.llm.complete_chat", fake_complete_chat)

    asset = register_asset(
        db,  # type: ignore[arg-type]
        storage,  # type: ignore[arg-type]
        kind="dialogue",
        title=None,
        content_bytes=_TRANSCRIPT,
        filename=None,
        product_id=None,
        source_kind="session_backflow",
    )

    assert seen["in_transaction"] is False, "LLM 调用时刻不得持有事务（P1#2）"
    assert "commit" in seen["events_at_call"], "complete_chat 前必须已 commit"
    assert db.events[:3] == ["begin", "flush", "commit"]
    # 成功终态（待人洗）在 commit 之后的新事务里，由调用方最终 commit 收口
    assert asset.status == PENDING_REVIEW
    assert db.events.count("commit") == 1  # register_asset 只提交登记行这一次


def test_register_asset_llm_failure_still_lands_ingested_last_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """既有语义不回归：登记行先 commit 后机洗失败，资产仍以 ingested +
    last_error 返回（「登记失败不挡字节」），失败终态并进调用方事务。"""
    db = _TxnRecordingDb()
    storage = _MemoryStorage()

    from suite_api.services import llm as llm_module

    async def _boom(_system: str, _user: str) -> str:
        raise llm_module.LLMUnavailable("厂商模型暂时不可用")

    monkeypatch.setattr(llm_module, "complete_chat", _boom)

    asset = register_asset(
        db,  # type: ignore[arg-type]
        storage,  # type: ignore[arg-type]
        kind="dialogue",
        title=None,
        content_bytes=_TRANSCRIPT,
        filename=None,
        product_id=None,
        source_kind="session_backflow",
    )

    assert db.events == ["begin", "flush", "commit"]
    assert asset.status == "ingested"
    assert "LLM QA 抽取失败" in (asset.last_error or "")


def test_retry_machine_wash_releases_transaction_before_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """评审(16) 补钉：retry 端点与登记同纪律——机洗（dialogue 含 LLM ≤20s）
    调用时刻不得持有事务（P1#2 的 retry 路径，routes/assets.py 机洗前 commit）。"""
    from types import SimpleNamespace

    from suite_api.routes import assets as assets_routes
    from suite_api.services import llm as llm_module

    db = _TxnRecordingDb()
    storage = _MemoryStorage()
    storage.put_bytes("dialogue/x/y.txt", _TRANSCRIPT)
    asset = SimpleNamespace(
        kind="dialogue", status="ingested", last_error="旧失败", id=1, discarded_at=None
    )
    version = SimpleNamespace(asset_id=1, version_no=1, object_key="dialogue/x/y.txt")
    seen: dict[str, Any] = {}

    async def fake_complete_chat(_system: str, _user: str) -> str:
        seen["in_transaction"] = db.in_transaction()
        return "[]"

    monkeypatch.setattr(llm_module, "complete_chat", fake_complete_chat)
    monkeypatch.setattr(assets_routes, "_get_asset_or_404", lambda _db, _aid: asset)
    monkeypatch.setattr(assets_routes, "_latest_version_or_404", lambda _db, _a: version)
    monkeypatch.setattr(assets_routes, "_product_or_none", lambda _db, _a: None)
    monkeypatch.setattr(assets_routes, "to_asset_detail", lambda _db, _a: SimpleNamespace())

    assets_routes.retry_machine_wash(
        asset_id=1,
        db=db,
        operator=SimpleNamespace(),
        storage=storage,  # type: ignore[arg-type]
    )

    assert seen["in_transaction"] is False, "retry 的 LLM 调用时刻不得持有事务（P1#2）"
    assert asset.status == "pending_review"
    assert asset.last_error is None


# ---------- 第 94a 刀：图片登记（对象键跟字节走 + VLM 草稿三态） ----------

# 1x1 真 PNG（魔数 + 完整字节流）：登记路径按魔数复验，测试用它当合法图片字节
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=="
)


def test_make_object_key_image_suffix_follows_bytes() -> None:
    """键后缀**跟字节走**（第 46 刀裁决 4 的图片延伸）：不给 suffix 时按魔数
    嗅探（png/jpeg/webp），嗅不出退 bin（不拿假后缀冒充）；显式 suffix 优先。"""
    from suite_api.services.registration import make_object_key

    png = b"\x89PNG\r\n\x1a\n" + b"payload"
    jpeg = b"\xff\xd8\xff\xe0" + b"payload"
    webp = b"RIFF\x00\x00\x00\x00WEBP" + b"payload"
    assert make_object_key("image", png).endswith(".png")
    assert make_object_key("image", jpeg).endswith(".jpg")
    assert make_object_key("image", webp).endswith(".webp")
    assert make_object_key("image", b"not-an-image").endswith(".bin")
    assert make_object_key("image", png, suffix="png").endswith(".png")
    assert make_object_key("image", png).startswith("documents/")  # 图片不是切片，不走 clips/


def test_register_asset_image_without_vlm_key_abstains_and_advances() -> None:
    """空 key = 无草稿（弃权）且照常推进待人洗（fail-诚实：登记不因草稿缺位失败）。"""
    db = _RegisterStub()
    storage = _MemoryStorage()
    asset = register_asset(
        db,  # type: ignore[arg-type]
        storage,  # type: ignore[arg-type]
        kind="image",
        title="商品图",
        content_bytes=_PNG,
        filename="a.png",
        product_id=None,
        source_kind="upload",
    )
    assert asset.status == PENDING_REVIEW
    assert asset.last_error is None
    version = db.version()
    assert version is not None
    assert version.extracted_fields == {"图片描述": {"abstained": True}}
    assert version.object_key.endswith(".png")  # 键后缀跟字节走


def test_register_asset_image_vlm_failure_is_abstained_not_ingested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """VLM 失败**不 fail 整个登记**（第 94a 刀裁决）：草稿缺位 → 弃权 →
    照常待人洗，人洗补写兜底（与 dialogue 的「已配置失败=停已接入」刻意不同级）。"""
    from suite_api.services import vlm as vlm_module

    def _boom(_image_bytes: bytes) -> str:
        raise vlm_module.VLMUnavailable("看图服务暂时不可用")

    monkeypatch.setattr(vlm_module, "describe_image", _boom)
    db = _RegisterStub()
    asset = register_asset(
        db,  # type: ignore[arg-type]
        _MemoryStorage(),  # type: ignore[arg-type]
        kind="image",
        title=None,
        content_bytes=_PNG,
        filename=None,
        product_id=None,
        source_kind="upload",
    )
    assert asset.status == PENDING_REVIEW
    assert asset.last_error is None
    version = db.version()
    assert version is not None and version.extracted_fields["图片描述"] == {"abstained": True}


def test_register_asset_image_vlm_draft_lands_as_machine_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """草稿进 extracted（source=machine，草稿不是事实）——**不进 confirmed**：
    人确认才生效（0010：confirmed 才进索引）。草稿值过 redact（0038 出口必掩）。"""
    from suite_api.services import vlm as vlm_module

    monkeypatch.setattr(
        vlm_module,
        "describe_image",
        lambda _bytes: "显示器侧面带可调节支架，客服电话13812345678。",
    )
    db = _RegisterStub()
    asset = register_asset(
        db,  # type: ignore[arg-type]
        _MemoryStorage(),  # type: ignore[arg-type]
        kind="image",
        title="商品图",
        content_bytes=_PNG,
        filename=None,
        product_id=None,
        source_kind="upload",
    )
    assert asset.status == PENDING_REVIEW
    version = db.version()
    assert version is not None
    entry = version.extracted_fields["图片描述"]
    assert entry["source"] == "machine"  # 草稿：机洗面，人确认才转 confirmed
    assert "13812345678" not in entry["value"]  # 打码步（ADR 0038）
    assert version.confirmed_fields == {}


class _RegisterStub:
    """登记用 Session 替身：留下被 add 的行对象（版本行从里面取来断言）。"""

    def __init__(self) -> None:
        self.added: list[Any] = []

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    def flush(self) -> None:
        pass

    def commit(self) -> None:
        pass

    def refresh(self, _obj: Any) -> None:
        pass

    def version(self) -> Any:
        return next((o for o in self.added if isinstance(o, AssetVersion)), None)
