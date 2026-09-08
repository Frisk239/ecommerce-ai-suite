"""评测集 runner（第 15 刀，ADR 0027：评测集不是资产，v1=一份可复现记录）。

形状（spec Must 2）：`apps/api/evals/golden.json` 是纯数据（问题 + 期望），本文件
是 runner——module 级 fixture 在测试库内发布 golden.json 需要的资产（标题与 JSON
锚一致），参数化逐 case 直接调 `run_ask`（订单/库存/检索/拒答全走真实路径，不
mock 引擎）。空 LLM key 下全可复现：answer 类 content 不断言，只断言 expect
声明的部分（citations/kind/handoff/tool）。

锚定口径：标题锚不 id 锚——测试库每 module 独立建库（conftest 的 api fixture），
资产 id 漂移，只有标题是稳定可复现的锚（spec 已锁）。

无 DB：runner 用例走 api fixture 自动 skip；schema 自检只读 JSON，不依赖 DB。
"""

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from suite_api.models import ServiceSession
from suite_api.services import llm as llm_module
from suite_api.services.chat_engine import AskOutcome, run_ask

ApiFixture = tuple[TestClient, Path]

GOLDEN_PATH = Path(__file__).resolve().parents[1] / "evals" / "golden.json"

# ---------- fixture 发布的资产（标题 = golden.json 的 cite_asset_title 锚） ----------

_CUP_TITLE = "保温杯规格说明"
_CUP_DOC = "钛钢保温杯产品说明\n净含量：480ml\n材质牌号未标注，详见吊牌。"
_RETURN_TITLE = "退货政策"
_RETURN_DOC = "保修与退货政策\n七天无理由退货；退货需保持吊牌完整。"
# 对话资产标题=回流会话的首问（回流登记取首问做标题，不另设）
_ENGRAVING_TITLE = "定制刻字服务怎么收费"
_ENGRAVING_QA = [{"q": "杯身刻字最多多少字", "a": "杯身刻字最多支持10个字"}]


def _load_cases() -> tuple[list[Any], str | None]:
    """读 golden.json；返回 (cases, 读取错误)。坏 JSON 不炸收集，交给 schema 自检报。"""
    try:
        data = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [], f"golden.json 读取失败: {exc}"
    if not isinstance(data, list):
        return [], "golden.json 顶层必须是 case 数组"
    return data, None


_CASES, _LOAD_ERROR = _load_cases()

_TOOLS = ("order", "stock")
_TOOL_NAMES = {"order": "get_order_status", "stock": "get_stock"}


def _case_id(case: Any, index: int) -> str:
    if isinstance(case, dict) and isinstance(case.get("id"), str) and case["id"]:
        return case["id"]
    return f"case-{index}"


def _case_problems(case: Any, index: int, seen_ids: set[str]) -> list[str]:
    """单 case schema 校验：返回问题列表（每条自带定位，报错指明 case id）。"""
    cid = case.get("id") if isinstance(case, dict) else None
    label = f"case[{index}] id={cid!r}"
    problems: list[str] = []
    if not isinstance(case, dict):
        return [f"{label}: case 必须是对象"]
    if not isinstance(cid, str) or not cid:
        problems.append(f"{label}: id 缺失或非非空字符串")
    elif cid in seen_ids:
        problems.append(f"{label}: id 重复")
    else:
        seen_ids.add(cid)
    question = case.get("question")
    if not isinstance(question, str) or not question.strip():
        problems.append(f"{label}: question 缺失或空白")
    expect = case.get("expect")
    if not isinstance(expect, dict) or not expect:
        problems.append(f"{label}: expect 缺失或非对象")
        return problems
    keys = set(expect)
    if "refuse" in keys:
        if keys != {"refuse"} or expect["refuse"] is not True:
            problems.append(f"{label}: refuse 形状非法（应恰好为 {{'refuse': true}}）")
        return problems
    if "cite_asset_title" in keys:
        if keys != {"cite_asset_title", "version_no"}:
            problems.append(f"{label}: cite 形状非法（应恰好 cite_asset_title + version_no）")
            return problems
        title = expect["cite_asset_title"]
        version_no = expect["version_no"]
        if not isinstance(title, str) or not title.strip():
            problems.append(f"{label}: cite_asset_title 缺失或非非空字符串")
        if not isinstance(version_no, int) or isinstance(version_no, bool) or version_no < 1:
            problems.append(f"{label}: version_no 应为 ≥1 的整数")
        return problems
    if "tool" in keys:
        allowed = {"tool", "tool_summary_contains", "handoff"}
        if not keys <= allowed or "tool_summary_contains" not in keys:
            problems.append(f"{label}: tool 形状非法（tool + tool_summary_contains，可选 handoff）")
        if expect.get("tool") not in _TOOLS:
            got = expect.get("tool")
            problems.append(f"{label}: tool 值非法（应为 {'/'.join(_TOOLS)}，收到 {got!r}）")
        summary = expect.get("tool_summary_contains")
        if not isinstance(summary, str) or not summary:
            problems.append(f"{label}: tool_summary_contains 缺失或非非空字符串")
        if "handoff" in keys and not isinstance(expect["handoff"], bool):
            problems.append(f"{label}: handoff 存在时必须是布尔")
        return problems
    problems.append(f"{label}: expect 不属于三形状（cite_asset_title/refuse/tool）")
    return problems


# ---------- schema 自检（不依赖 DB，空收集也能报文件级错误） ----------


def test_golden_json_schema() -> None:
    if _LOAD_ERROR is not None:
        pytest.fail(_LOAD_ERROR)
    assert len(_CASES) >= 12, f"golden.json 至少 12 条 case，当前 {len(_CASES)}"
    seen_ids: set[str] = set()
    problems: list[str] = []
    for index, case in enumerate(_CASES):
        problems.extend(_case_problems(case, index, seen_ids))
    assert not problems, "golden.json schema 非法：" + "；".join(problems)


def test_golden_json_covers_spec_spectrum() -> None:
    """覆盖谱自检（spec Must 1 列的类别都要有用例）：三形状齐 + 订单/库存 + 订单优先。"""
    if _LOAD_ERROR is not None:
        pytest.skip(_LOAD_ERROR)
    by_shape: dict[str, list[str]] = {"cite": [], "refuse": [], "tool": []}
    order_seen, stock_seen, order_priority = False, False, False
    for case in _CASES:
        if not isinstance(case, dict) or not isinstance(case.get("expect"), dict):
            continue
        expect = case["expect"]
        case_id = _case_id(case, 0)
        if "cite_asset_title" in expect:
            by_shape["cite"].append(case_id)
        elif "refuse" in expect:
            by_shape["refuse"].append(case_id)
        elif "tool" in expect:
            by_shape["tool"].append(case_id)
            order_seen = order_seen or expect["tool"] == "order"
            stock_seen = stock_seen or expect["tool"] == "stock"
            order_priority = order_priority or (expect["tool"] == "order" and "priority" in case_id)
    assert all(by_shape.values()), (
        f"三形状各需用例：{ {k: v or '缺' for k, v in by_shape.items()} }"
    )
    assert order_seen and stock_seen, "订单与库存两类工具用例都要有"
    assert order_priority, "同含单号+库存词的订单优先 policy edge 必须有用例"


# ---------- fixture：在测试库内发布 golden.json 需要的资产（每 module 一次） ----------


def _login(client: TestClient) -> None:
    assert (
        client.post(
            "/api/auth/login", json={"username": "operator", "password": "operator123"}
        ).status_code
        == 200
    )


def _upload(client: TestClient, content: str, title: str) -> int:
    resp = client.post(
        "/api/assets/register",
        files={"file": ("eval.txt", content.encode(), "text/plain")},
        data={"title": title},
    )
    assert resp.status_code == 201, resp.text
    return int(resp.json()["id"])


def _run_question(client: TestClient, question: str) -> tuple[AskOutcome, int]:
    """直调 run_ask（真实引擎路径）：自建 active 会话，返回 outcome 与会话 id。"""
    factory = client.app.state.session_factory
    with factory() as db:
        session = ServiceSession(status="active")
        db.add(session)
        db.commit()
        outcome = asyncio.run(run_ask(db, session, question))
        return outcome, session.id


@pytest.fixture(scope="module")
def anchors(api: ApiFixture) -> dict[str, int]:
    """发布评测依赖资产并返回 {标题: asset_id}（与 golden.json 标题锚一致）。

    先做对话回流（此时发布集为空，首问恒 refusal，转写有料），再传两个文档，
    最后确认+发布对话的 QA 对——发布顺序不影响标题锚的稳定性。
    """
    client, _ = api
    _login(client)

    # 1) 回流对话（QA 路径）：monkeypatch complete_chat 抽固定 QA（先例 test_reflow_qa_integration）
    _, dialogue_session_id = _run_question(client, _ENGRAVING_TITLE)

    async def fake_complete_chat(*_args: Any) -> str:
        return json.dumps(_ENGRAVING_QA, ensure_ascii=False)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(llm_module, "complete_chat", fake_complete_chat)
        registered = client.post(f"/api/service/sessions/{dialogue_session_id}/register")
    assert registered.status_code == 201, registered.text
    dialogue = registered.json()
    assert dialogue["title"] == _ENGRAVING_TITLE  # 标题锚 = 首问
    dialogue_id = int(dialogue["id"])
    assert dialogue["status"] == "pending_review"
    confirmed = client.patch(
        f"/api/assets/{dialogue_id}/versions/1/fields", json={"qa_pairs": _ENGRAVING_QA}
    )
    assert confirmed.status_code == 200, confirmed.text
    assert client.post(f"/api/assets/{dialogue_id}/publish").status_code == 200

    # 2) 两个文档资产（不挂商品：无必填闸门，发布即入索引）
    mapping = {_ENGRAVING_TITLE: dialogue_id}
    for title, content in ((_CUP_TITLE, _CUP_DOC), (_RETURN_TITLE, _RETURN_DOC)):
        asset_id = _upload(client, content, title)
        assert client.post(f"/api/assets/{asset_id}/publish").status_code == 200
        mapping[title] = asset_id
    return mapping


# ---------- 参数化 runner ----------


def _expect_problems(
    case: dict, expect: dict, outcome: AskOutcome, anchors: dict[str, int]
) -> list[str]:
    """按 expect 声明的部分断言（空 LLM key 下 answer 类 content 不比文案）。"""
    problems: list[str] = []
    answer = outcome.answer
    if "refuse" in expect:
        if answer.kind != "refusal":
            problems.append(f"应拒答（refusal），实际 kind={answer.kind!r}")
        if answer.handoff is not True:
            problems.append("拒答必须显性转人工（handoff=True）")
        if outcome.tool is not None:
            problems.append(f"拒答不该冒充工具路径：tool={outcome.tool!r}")
        if answer.citations:
            problems.append(f"拒答不应有引用：{answer.citations}")
        return problems
    if "cite_asset_title" in expect:
        title = expect["cite_asset_title"]
        if title not in anchors:
            problems.append(f"标题锚 {title!r} 未在 fixture 发布资产中——先在同标题发布资产")
            return problems
        want = {"asset_id": anchors[title], "version_no": expect["version_no"]}
        if want not in answer.citations:
            problems.append(f"citations 应含 {want}，实际 {answer.citations}")
        if answer.kind != "answer":
            problems.append(f"引用命中应 kind=answer，实际 {answer.kind!r}")
        return problems
    # tool 形状
    want_name = _TOOL_NAMES[expect["tool"]]
    if outcome.tool is None:
        problems.append(f"应走工具 {want_name}，实际未走任何工具")
        return problems
    if outcome.tool["name"] != want_name:
        problems.append(f"工具应为 {want_name}，实际 {outcome.tool['name']!r}")
    if expect["tool_summary_contains"] not in outcome.tool["result"]:
        want_summary = expect["tool_summary_contains"]
        problems.append(f"工具摘要应含 {want_summary!r}，实际 {outcome.tool['result']!r}")
    want_handoff = bool(expect.get("handoff"))
    want_kind = "handoff" if want_handoff else "answer"
    if answer.kind != want_kind:
        problems.append(f"kind 应为 {want_kind}，实际 {answer.kind!r}")
    if answer.handoff is not want_handoff:
        problems.append(f"handoff 应为 {want_handoff}，实际 {answer.handoff}")
    return problems


@pytest.mark.parametrize("case", _CASES, ids=[_case_id(case, i) for i, case in enumerate(_CASES)])
def test_golden_case(api: ApiFixture, anchors: dict[str, int], case: dict) -> None:
    assert isinstance(case, dict), "case 形状由 schema 自检报错兜底，这里不该到达"
    outcome, _ = _run_question(api[0], case["question"])
    problems = _expect_problems(case, case["expect"], outcome, anchors)
    assert not problems, f"golden case {case['id']!r}（问题 {case['question']!r}）：" + "；".join(
        problems
    )
