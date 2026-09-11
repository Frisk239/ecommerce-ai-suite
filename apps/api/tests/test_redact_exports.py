"""打码出口收口测试（第 21 刀，ADR 0038 修订段「字节不动、出口必掩」）。

出口地图（审计刀 4 P0 簇，五个漏口逐一钉死）：
- 出口 1（检索 chunk → prompt/模板回答）：retrieve 返回处统一 redact（收口点，
  见 services/retrieval.py 注释；build_prompts 另掩顾客问句行）；
- 出口 2（人洗 confirmed 值）：PATCH fields 在 validate 后、写回前掩值
  （qa_pairs 每项 q/a 与字符串值，纯函数 _mask_confirmed_value）；
- 出口 3（coaching）：题面兜底（first_customer_question 推导侧）、打分 prompt
  三输入（build_score_prompt 唯一漏斗，score/rescore 同掩）、coach_records 落库；
- 出口 4（MCP export_published）：导出正文过 redact；
- 出口 5（血缘引用问句样例）：lineage._summary 先掩后截。
- 出口 6（评审处置件 1，MCP get_asset）：正文与 extracted/confirmed 两张字段
  表出响应前过 redact（_mask_fields_map：字符串值与 qa_pairs 每项 q/a）。
- title 出口全线（评审处置件 2）：routes/service._first_question 源头收口
  （先掩后截）——回流登记资产 title=顾客首问截断，登记即净，MCP 三工具/
  降级回答标题/详情页一次全覆盖。
- 豁免钉死：GET versions/{no}/text（操作者面版本正文）仍含原文——打码永不
  回写存储（0038 修订段明文），集成用例一并断言。

第 26 刀补漏（审计刀 5 P1①②③+缺口路）：material 生成 prompt 出口掩、
MCP 三出口 title 掩（_mask_title）、ops_runs 落库掩、gaps.question 出口掩——
断言分别落在 test_material.py / test_mcp.py（本文件钉 _mask_title 形状）/
test_ops.py+test_ops_integration.py / test_service_integration.py。

单元层用假 db/纯函数即可钉；MCP 集成走官方 SDK client（自建库，模式同
test_mcp.py）。手机号统一 13812345678（redact 后 1********78，等长掩码）。
"""

import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from sqlalchemy import select
from sse_helpers import parse_sse_events

from suite_api.main import create_app
from suite_api.mcp_server import _mask_fields_map, _mask_title
from suite_api.models import RetrievalChunk
from suite_api.routes.assets import _mask_confirmed_value
from suite_api.routes.service import _first_question
from suite_api.services import llm as llm_module
from suite_api.services.coaching import build_score_prompt, first_customer_question
from suite_api.services.lineage import _summary, assemble_lineage
from suite_api.services.llm import build_prompts
from suite_api.services.machine_wash import QA_FIELD
from suite_api.services.retrieval import query_terms, retrieve, score_chunk
from suite_api.settings import Settings

API_PHONE = "13812345678"
MASKED_PHONE = "1********78"
_GOOD_SCORE_JSON = '{"accurate": 36, "evidence": 25, "tone": 28, "comment": "口径准"}'

ApiFixture = tuple[TestClient, Path]


def _login(client: TestClient) -> None:
    assert (
        client.post(
            "/api/auth/login", json={"username": "operator", "password": "operator123"}
        ).status_code
        == 200
    )


def _ask(client: TestClient, session_id: int, question: str) -> list[tuple[str, dict]]:
    with client.stream(
        "POST", f"/api/service/sessions/{session_id}/messages", json={"content": question}
    ) as resp:
        assert resp.status_code == 200
        raw = "".join(resp.iter_text())
    return parse_sse_events(raw)


def _patch_complete_chat(
    monkeypatch: pytest.MonkeyPatch, result: str | None = None, error: Exception | None = None
) -> list[dict[str, str]]:
    calls: list[dict[str, str]] = []

    async def fake(system_prompt: str, user_prompt: str) -> Any:
        calls.append({"system": system_prompt, "user": user_prompt})
        if error is not None:
            raise error
        assert result is not None
        return result

    monkeypatch.setattr(llm_module, "complete_chat", fake)
    return calls


def _patch_stream(
    monkeypatch: pytest.MonkeyPatch, pieces: list[str] | None = None
) -> list[dict[str, str]]:
    calls: list[dict[str, str]] = []

    async def fake_stream(
        system_prompt: str, user_prompt: str, history: list[dict[str, str]] | None = None
    ) -> Any:
        # 第 29 刀多轮化：stream_chat 增 history 参数（默认 None）——替身按新
        # 形状补默认参数；捕获记录形状不变（既有 prompt 断言零改动）
        calls.append({"system": system_prompt, "user": user_prompt})
        for piece in pieces or []:
            yield piece

    monkeypatch.setattr(llm_module, "stream_chat", fake_stream)
    return calls


# ---------- 单元：出口 1（retrieve 收口 + build_prompts 问句行） ----------


class _FakeResult:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def all(self) -> list:
        return self._rows


class _FakeDb:
    def __init__(self, rows: list) -> None:
        # 第 39 刀保鲜：候选行随带 last_verified_at；第 66 刀评论适用域再随带
        # source_kind（retrieve 按 5 元组解包）——既有用例仍给 3 元组，此处
        # 统一补 None + "upload"（未验证不降权、非评论不被适用域闸滤）。
        self._rows = [tuple(row) + (None, "upload") for row in rows]

    def execute(self, stmt: Any) -> _FakeResult:  # noqa: ARG002 - 语句形状另有钉测
        return _FakeResult(self._rows)


def test_retrieve_returns_masked_chunk_and_scores_on_raw() -> None:
    """0038 修订出口 1：chunk 在 retrieve 返回处统一 redact——打分仍吃原文块
    （不动检索打分），返回的 chunk 已是掩码（两消费者 prompt/模板同净）。"""
    raw = f"顾客：我的电话{API_PHONE}能改收货地址吗"
    db = _FakeDb([(7, 2, raw)])
    hits = retrieve(db, "电话能改收货地址吗")  # type: ignore[arg-type]
    assert hits, "原文块应命中（打分在掩码前）"
    hit = hits[0]
    assert API_PHONE not in hit["chunk"]
    assert MASKED_PHONE in hit["chunk"]
    # 分数与原文块直打分一致（收口不动打分路径）
    assert hit["score"] == score_chunk(query_terms("电话能改收货地址吗"), raw)


def test_build_prompts_masks_customer_question_line() -> None:
    """0038 修订出口 1：顾客问句也进厂商 prompt（进程边界）——问句行过 redact；
    证据 chunk 已在 retrieve 掩码，prompt 两处都不留裸号。"""
    hits = [{"asset_id": 1, "version_no": 1, "chunk": f"答：回电{MASKED_PHONE}确认", "score": 0.5}]
    _, user_prompt = build_prompts(hits, f"我的号码是{API_PHONE}，请问什么时候发货")
    assert API_PHONE not in user_prompt
    assert f"顾客问题：我的号码是{MASKED_PHONE}，请问什么时候发货" in user_prompt
    assert MASKED_PHONE in user_prompt  # 证据行原样进（上游已掩，不双掩变形）


# ---------- 单元：出口 2（_mask_confirmed_value 纯函数层） ----------


def test_mask_confirmed_value_qa_pairs_and_strings() -> None:
    """0038 修订出口 2：qa_pairs 每项 q/a 与文档字符串值统一过 redact。"""
    masked = _mask_confirmed_value(QA_FIELD, [{"q": f"手机号{API_PHONE}怎么改", "a": "打客服电话"}])
    assert masked == [{"q": f"手机号{MASKED_PHONE}怎么改", "a": "打客服电话"}]
    assert _mask_confirmed_value("净含量", "550毫升") == "550毫升"  # 无 PII 幂等
    assert (
        _mask_confirmed_value("售后联系方式", f"热线{API_PHONE}，邮箱 zhang.san@example.com")
        == f"热线{MASKED_PHONE}，邮箱 ****@example.com"
    )


# ---------- 单元：出口 6（MCP get_asset 字段表）+ title 源头（_first_question） ----------


def test_mask_fields_map_string_and_qa_entries() -> None:
    """0038 修订出口 6（评审处置件 1）：get_asset 的 extracted/confirmed 映射
    表——字符串 value 与 qa_pairs 每项 q/a 出边界前掩；abstained 项与坏形状
    原样走；已掩值幂等不二次变形。"""
    masked = _mask_fields_map(
        {
            "净含量": {"value": f"550毫升，售后{API_PHONE}", "source": "human"},
            QA_FIELD: {
                "value": [{"q": f"电话{API_PHONE}能改吗", "a": "打客服"}],
                "source": "machine",
            },
            "保质期": {"abstained": True},
        }
    )
    dumped = json.dumps(masked, ensure_ascii=False)
    assert API_PHONE not in dumped
    assert MASKED_PHONE in dumped
    assert masked["净含量"]["source"] == "human"  # 只动 value，entry 形状不变
    assert masked["保质期"] == {"abstained": True}
    assert _mask_fields_map(masked) == masked  # 幂等（新链路写入已掩，读侧兜历史）


def test_mask_title_masks_pii_and_passes_clean() -> None:
    """第 26 刀 P1②（单元层）：MCP 三出口 title 统一过 _mask_title——回流
    title 虽已在源头收掩（上方 _first_question 用例），但切片 title=
    transcript[:60] 裸转写截断（services/clips.py）、素材/上传 title 非净源，
    出口侧统一兜底（双保险取一）。None/空/净值原样（幂等，不伤既有断言）。"""
    assert (
        _mask_title(f"转写首问：我的电话{API_PHONE}能改吗")
        == f"转写首问：我的电话{MASKED_PHONE}能改吗"
    )
    assert _mask_title("盲盒可以指定款式吗") == "盲盒可以指定款式吗"
    assert _mask_title(None) is None
    assert _mask_title("") == ""


def test_first_question_masks_title_at_source() -> None:
    """评审处置件 2：回流 title/会话摘要在 _first_question 源头收口——先掩
    后截（跨 60 字边界的号码不会被拦腰留下裸号前缀），无 PII 输入逐字节
    不变（不破坏既有标题断言）。"""
    assert (
        _first_question(f"我的手机号{API_PHONE}还能改绑吗") == f"我的手机号{MASKED_PHONE}还能改绑吗"
    )
    boundary = "x" * 55 + API_PHONE + "尾" * 20
    out = _first_question(boundary)
    assert "13812" not in out and API_PHONE not in out  # 先截后掩的事故形态不许出现
    assert _first_question("盲盒可以指定款式吗") == "盲盒可以指定款式吗"  # 幂等


# ---------- 单元：出口 3（coaching 纯函数层） ----------


def test_first_customer_question_masks_fallback_text() -> None:
    """0038 修订出口 3：转写兜底题面直读版本字节（未掩区），推导出口处掩。"""
    transcript = f"客服：您好\n顾客：我的{API_PHONE}能改绑吗\n顾客：第二问"
    assert first_customer_question(transcript) == f"我的{MASKED_PHONE}能改绑吗"
    assert first_customer_question("客服：您好") is None


def test_build_score_prompt_masks_three_inputs() -> None:
    """0038 修订出口 3：打分 prompt 唯一漏斗——题面/标准答案/受训者手输三输入
    全掩（score_attempt 与 rescore_record 两条链同口）。"""
    prompt = build_score_prompt(
        f"顾客电话{API_PHONE}能改吗", f"回电{API_PHONE}确认", f"我的号码{API_PHONE}，请安排回电"
    )
    assert API_PHONE not in prompt
    assert prompt.count(MASKED_PHONE) == 3
    prompt_empty_ref = build_score_prompt(f"手机号{API_PHONE}改绑", None, "打客服")
    assert API_PHONE not in prompt_empty_ref and MASKED_PHONE in prompt_empty_ref


# ---------- 单元：出口 5（lineage._summary 先掩后截） ----------


def test_summary_masks_before_truncate() -> None:
    """0038 修订出口 5：先掩后截——手机号恰好跨 60 字边界时，若先截后掩会
    拦腰咬断数字串逃过正则（裸号前缀照泄）；先掩则任何截断位只切掩码。"""
    text = "x" * 55 + API_PHONE + "y" * 20  # 号码起点 55，先截 [:60] 会留下 "13812"
    out = _summary(text)
    assert API_PHONE not in out
    assert "13812" not in out  # 先截后掩的事故形态：半截裸号——不许出现
    assert out.endswith("…")
    assert _summary(f"问：我的电话{API_PHONE}") == f"问：我的电话{MASKED_PHONE}"


def test_assemble_lineage_samples_masked() -> None:
    """出口 5 聚合层：引用样例问句与考核题面快照经 _summary 出口即掩。"""
    now = datetime(2026, 9, 8, tzinfo=UTC)
    out = assemble_lineage(
        asset_id=7,
        source_kind="dialogue_reflow",
        product_id=None,
        audit_rows=[],
        citation_rows=[
            (3, f"顾客：{API_PHONE}能改地址吗", [{"asset_id": 7, "version_no": 1}], now)
        ],
        citations_total=1,
        coach_rows=[(9, f"题面带{API_PHONE}", {"asset_id": 7, "version_no": 1}, now)],
        version_fields={},
    )
    samples = out.usages.citations.samples
    assert len(samples) == 1
    assert API_PHONE not in samples[0].question
    assert MASKED_PHONE in samples[0].question
    assert API_PHONE not in out.usages.coaching[0].question


# ---------- 集成：出口 1+2+5 + 版本正文豁免（真 PG） ----------


def test_dialogue_with_phone_prompt_index_lineage_clean_text_exempt(
    api: ApiFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = api
    _login(client)

    # 回流路径：会话转写带裸号 -> complete_chat 替身让机洗草稿 q/a 也带号
    _patch_complete_chat(
        monkeypatch,
        result=f'[{{"q": "手机号{API_PHONE}还能改绑吗", "a": "客服回电{API_PHONE}办理"}}]',
    )
    sid = client.post("/api/service/sessions").json()["id"]
    _ask(client, sid, f"我的手机号{API_PHONE}还能改绑吗")
    reg = client.post(f"/api/service/sessions/{sid}/register")
    assert reg.status_code == 201
    asset_id = reg.json()["id"]
    # 评审处置件 2（源头验证）：回流登记即净——title=首问截断已在
    # _first_question 收口，登记响应返回的资产视图无裸号（下游 to_asset_out/
    # MCP/详情页全线受益）
    assert API_PHONE not in reg.json()["title"]
    assert MASKED_PHONE in reg.json()["title"]
    # 入口侧（第 17 刀既有行为）：机洗草稿值落库即掩——顺带钉住不被本刀破坏
    draft = reg.json()["versions"][0]["extracted_fields"][QA_FIELD]
    assert API_PHONE not in json.dumps(draft, ensure_ascii=False)

    # 出口 2：人洗 PATCH 手填含号 q/a -> 写回 confirmed 前掩
    patched = client.patch(
        f"/api/assets/{asset_id}/versions/1/fields",
        json={"qa_pairs": [{"q": f"手机号{API_PHONE}改绑要带什么", "a": "回电客服办理"}]},
    )
    assert patched.status_code == 200
    confirmed = patched.json()["confirmed_fields"][QA_FIELD]
    assert API_PHONE not in json.dumps(confirmed, ensure_ascii=False)
    assert confirmed["value"][0]["q"] == f"手机号{MASKED_PHONE}改绑要带什么"

    assert client.post(f"/api/assets/{asset_id}/publish").status_code == 200

    # 出口 2 的下游：confirmed qa 块入索引无裸号（切块入口随 confirmed 数据天然
    # 干净）；转写正文块按「字节不动」保留原文（检索读侧掩，出口 1 兜）
    session_factory = client.app.state.session_factory
    with session_factory() as db:
        qa_chunks = [
            c
            for (c,) in db.execute(
                select(RetrievalChunk.chunk).where(RetrievalChunk.asset_id == asset_id)
            ).all()
            if c.startswith("问：")
        ]
    assert qa_chunks and all(API_PHONE not in c for c in qa_chunks)

    # 出口 1（模板降级路径，空 key）：降级回答=检索命中块拼装 -> 无裸号有掩码
    events = _ask(client, client.post("/api/service/sessions").json()["id"], "手机号改绑要带什么")
    complete = events[-1][1]
    assert complete["kind"] == "answer"
    answer = "".join(d["text"] for e, d in events if e == "delta")
    assert API_PHONE not in answer
    assert MASKED_PHONE in answer

    # 出口 1（厂商 prompt 路径）：monkeypatch stream_chat 捕获 prompt 无裸号
    calls = _patch_stream(monkeypatch, pieces=["可携带证件联系客服办理。"])
    events = _ask(
        client, client.post("/api/service/sessions").json()["id"], f"手机号{API_PHONE}改绑要带什么"
    )
    assert len(calls) == 1
    assert API_PHONE not in calls[0]["user"] + calls[0]["system"]
    assert MASKED_PHONE in calls[0]["user"]  # 证据与问句都是掩码形态
    assert events[-1][1]["kind"] == "answer"
    assert {"asset_id": asset_id, "version_no": 1} in events[-1][1]["citations"]

    # 出口 5：血缘引用样例问句（顾客原问带裸号落 service_messages）-> 展示掩码
    lineage = client.get(f"/api/assets/{asset_id}/lineage")
    assert lineage.status_code == 200
    assert API_PHONE not in lineage.text
    assert MASKED_PHONE in lineage.text

    # 豁免钉死（0038 修订段明文）：操作者面版本正文端点仍含原文
    text_resp = client.get(f"/api/assets/{asset_id}/versions/1/text")
    assert text_resp.status_code == 200
    assert API_PHONE in text_resp.text


# ---------- 集成：出口 3 coaching（题面兜底/落库/打分 prompt/重评） ----------


def test_coaching_inputs_and_records_masked(
    api: ApiFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = api
    _login(client)

    # 空 key 登记（QA 弃权）-> 确认「没有 QA」-> 发布：题库走转写首问兜底
    sid = client.post("/api/service/sessions").json()["id"]
    _ask(client, sid, f"我的{API_PHONE}什么时候能收到退款")
    asset_id = client.post(f"/api/service/sessions/{sid}/register").json()["id"]
    assert (
        client.patch(f"/api/assets/{asset_id}/versions/1/fields", json={"qa_pairs": []}).status_code
        == 200
    )
    assert client.post(f"/api/assets/{asset_id}/publish").status_code == 200

    questions = client.get("/api/coach/questions").json()
    fallback = next(
        q
        for q in questions
        if q["key"]["asset_id"] == asset_id and q["key"]["source"] == "transcript"
    )
    # 兜底题面推导侧掩码（出口 3 第一点：题库列表与下游消费者拿到的都是掩码）。
    # 注意 asset_title 不在五出口地图内（登记侧标题行为第 12 刀既有语义，出口
    # 之外零改），故只对题面/参照字段做无裸号断言。
    assert API_PHONE not in fallback["question"]
    assert MASKED_PHONE in fallback["question"]

    # 作答含裸号 -> 替身打分：prompt 三输入无裸号 + coach_records 落库无裸号
    calls = _patch_complete_chat(monkeypatch, result=_GOOD_SCORE_JSON)
    rec = client.post(
        "/api/coach/attempts",
        json={"question_key": fallback["key"], "answer": f"让顾客回拨{API_PHONE}核实"},
    ).json()
    assert rec["status"] == "scored"
    assert len(calls) == 1
    assert API_PHONE not in calls[0]["user"] + calls[0]["system"]
    assert rec["trainee_answer"] == f"让顾客回拨{MASKED_PHONE}核实"
    assert API_PHONE not in json.dumps(rec, ensure_ascii=False)

    # 未评分行（撤替身 -> 空 key）落库仍掩 + rescore 重进 prompt 再兜一道
    monkeypatch.undo()
    rec2 = client.post(
        "/api/coach/attempts",
        json={"question_key": fallback["key"], "answer": f"登记号码{API_PHONE}"},
    ).json()
    assert rec2["status"] == "unscored"
    assert API_PHONE not in rec2["trainee_answer"] and MASKED_PHONE in rec2["trainee_answer"]
    calls2 = _patch_complete_chat(monkeypatch, result=_GOOD_SCORE_JSON)
    rescored = client.post(f"/api/coach/records/{rec2['id']}/rescore").json()
    assert rescored["status"] == "scored"
    assert calls2 and API_PHONE not in calls2[-1]["user"]


# ---------- 集成：出口 4 MCP export_published（真 PG + 官方 SDK client） ----------

_MCP_TOKEN = "test-redact-bearer"
_MCP_BASE = "http://localhost:8000"


def _mcp_payload(result: Any) -> Any:
    """call_tool 结果解包（口径同 test_mcp.py）。"""
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict) and "result" in structured:
        return structured["result"]
    if structured is not None:
        return structured
    texts = [b.text for b in result.content if hasattr(b, "text")]
    return json.loads(texts[0]) if len(texts) == 1 else texts


@pytest.fixture(scope="module")
def mcp_env(tmp_path_factory: pytest.TempPathFactory) -> Any:
    url = os.environ.get("SUITE_TEST_DATABASE_URL")
    if not url:
        pytest.skip("需真 Postgres：SUITE_TEST_DATABASE_URL")
    parts = urlsplit(url)
    admin_url = urlunsplit((parts.scheme, parts.netloc, "/postgres", "", ""))
    dbname = "suite_redact_mcp_test"

    def _run(sql: str) -> None:
        with psycopg.connect(admin_url, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(sql)

    _run(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
    _run(f'CREATE DATABASE "{dbname}"')
    db_url = urlunsplit((parts.scheme, parts.netloc, f"/{dbname}", "", ""))
    storage_root = tmp_path_factory.mktemp("redact_mcp_objects")
    settings = Settings(
        database_url=db_url,
        storage_root=storage_root,
        mcp_bearer_token=_MCP_TOKEN,
        llm_api_key="",
    )
    app = create_app(settings)
    yield app, settings
    _run(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')


def test_mcp_export_and_search_masked(mcp_env: Any) -> None:
    """0038 修订出口 4：export_published 正文过 redact（掩码不回写字节——
    同库版本正文端点/对象字节仍是原文）；search_published 的 chunk 由
    retrieve 收口点同口径带掩。"""
    app, settings = mcp_env

    def _factory(headers=None, timeout=None, auth=None):  # noqa: ANN001, ANN202 - SDK 签名
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url=_MCP_BASE,
            follow_redirects=True,
            headers=headers,
            timeout=timeout or httpx.Timeout(30),
        )

    async def scenario() -> dict:
        out: dict = {}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=_MCP_BASE
        ) as api:
            login = await api.post(
                "/api/auth/login", json={"username": "operator", "password": "operator123"}
            )
            assert login.status_code == 200
            api.cookies.update(login.cookies)
            body = f"退款到账说明\n顾客电话{API_PHONE}原路退回"
            up = await api.post(
                "/api/assets/register",
                files={"file": ("refund.txt", body.encode("utf-8"), "text/plain")},
                data={"title": "红测刀出口四说明"},
            )
            assert up.status_code == 201, up.text
            asset_id = up.json()["id"]
            assert (await api.post(f"/api/assets/{asset_id}/publish")).status_code == 200

        async with streamablehttp_client(
            f"{_MCP_BASE}/mcp/",
            headers={"Authorization": f"Bearer {settings.mcp_bearer_token}"},
            httpx_client_factory=_factory,
        ) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                exported = await session.call_tool("export_published", {})
                assert not exported.isError
                out["export"] = _mcp_payload(exported)
                searched = await session.call_tool(
                    "search_published", {"query": "顾客电话原路退回"}
                )
                assert not searched.isError
                out["search"] = _mcp_payload(searched)
                # 评审处置件 1（第六出口）：含号资产 get_asset -> 响应无裸号
                got = await session.call_tool("get_asset", {"asset_id": asset_id})
                assert not got.isError
                out["get"] = _mcp_payload(got)
        return out

    async def wrapped() -> dict:
        # mounted 子应用 lifespan 不执行：host lifespan 手动进（session manager 同 loop）
        async with app.router.lifespan_context(app):
            return await scenario()

    out = asyncio.run(wrapped())

    export = [r for r in out["export"] if r["title"] == "红测刀出口四说明"]
    assert export, "已发布资产应出现在导出里"
    assert API_PHONE not in json.dumps(out["export"], ensure_ascii=False)
    assert MASKED_PHONE in export[0]["content"]  # 正文以掩码形态出边界
    assert API_PHONE not in json.dumps(out["search"], ensure_ascii=False)

    # get_asset（第六出口钉测）：同资产响应无裸号，content 呈掩码
    assert API_PHONE not in json.dumps(out["get"], ensure_ascii=False)
    assert MASKED_PHONE in out["get"]["content"]
    # 对象键/版本指针不动（字节不动纪律）：响应仍带真实 object_key
    assert out["get"]["object_key"]


def test_first_question_masks_separated_phone_and_short_domain_email() -> None:
    """审计刀 8 P1：`_first_question` 的产物是**对话资产 title**（随 MCP/导出外流），
    必须用覆盖面更广的 `redact_contact`——`redact` 漏 `138-0013-8000` 这类带分隔
    号码与 `ab@x.co` 这类短域邮箱（第 42 刀的 P1 已实证）。
    """
    from suite_api.routes.service import _first_question

    assert "138-0013-8000" not in _first_question("我的电话是 138-0013-8000，麻烦回电")
    assert "ab@x.co" not in _first_question("邮箱 ab@x.co 可以联系我")
    # 常规形态仍掩（redact 的既有能力不回退）
    assert "13800138000" not in _first_question("手机 13800138000")
    assert "zhang@example.com" not in _first_question("邮箱 zhang@example.com")
