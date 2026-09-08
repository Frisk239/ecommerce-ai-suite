"""运营 Agent 单元测试（不依赖 DB，LLM 以替身注入不发外网；第 22 刀/ADR 0041）。

覆盖四块纯逻辑与状态机：
- 生成输出解析 parse_generated_output（好 JSON/围栏/坏 JSON/坏形状——
  「生成结果解析失败」分级）；
- prompt 组装过 redact（0038 修订：厂商 prompt 是进程边界，输入必掩）+
  compose 兜底正文/引用冻结口径（fetch_published_refs 替身）；
- 执行环 drive_steps：running 先落库再执行、failed 断链（后续步停 pending）、
  done 步不重跑（续跑语义核心，spec 序号/计数钉死）；
- start_run/retry_run/deliver_run 全链（_FakeDB）：LLM 故障分级「生成不可用」、
  deliver 状态机（未完成/已投放 409）、retry 只续跑失败步之后的步。
"""

import copy
import json
from typing import Any

import pytest
from fastapi import HTTPException

from suite_api.models import OpsRun, Product
from suite_api.services import llm as llm_module
from suite_api.services import ops as ops_module
from suite_api.services.ops import (
    DONE,
    FAILED,
    NO_REF_DETAIL,
    PENDING,
    RUNNING,
    STEP_COMPOSE,
    STEP_GEN,
    STEP_READ,
    OpsGenError,
    build_generation_prompt,
    deliver_run,
    drive_steps,
    fallback_body,
    initial_steps,
    parse_generated_output,
    read_product_detail,
    retry_run,
    spec_selling_points,
    start_run,
)

GOOD_OUTPUT = json.dumps(
    {
        "title": "钛钢保温杯：一杯守住温度",
        "body": "早九点的热水，下午三点还烫口。钛钢保温杯，通勤车载两相宜。",
    },
    ensure_ascii=False,
)


# ---------- 替身 ----------


class _FakeDB:
    """只喂 ops 服务用到的最小会话面：get(Product/OpsRun)/add/commit/refresh 记账。"""

    def __init__(self, product: Product | None, runs: list[OpsRun]) -> None:
        self.product = product
        self.runs = runs
        self.added: list[OpsRun] = []
        self.commits = 0
        # 26 刀行锁钉测：记录取 OpsRun 行时的 with_for_update 参数
        self.run_row_locks: list[bool] = []

    def get(self, model: Any, pk: Any, **kwargs: Any) -> Any:
        # 26 刀行锁：_run_or_raise 经 db.get(..., with_for_update=True) 取行——
        # 假会话不建模锁语义，吞掉 kwarg（锁形状由集成并发用例钉）
        if model is Product:
            return self.product
        if model is OpsRun:
            self.run_row_locks.append(bool(kwargs.get("with_for_update")))
            return next((r for r in self.runs + self.added if r.id == pk), None)
        return None

    def add(self, obj: OpsRun) -> None:
        self.added.append(obj)

    def commit(self) -> None:
        self.commits += 1

    def refresh(self, obj: Any) -> None:
        del obj


def _product() -> Product:
    return Product(
        id=1,
        name="钛钢保温杯",
        category="器皿",
        spec_schema={"净含量": {"required": True}, "材质": {"required": True}},
        spec_values={
            "净含量": {"value": "480ml", "source": {"asset_id": 1, "version": 1}},
        },
    )


def _run(steps: list[dict[str, Any]] | None = None, output: dict | None = None) -> OpsRun:
    return OpsRun(id=1, product_id=1, steps=steps if steps is not None else initial_steps(), output=output)


def _patch_llm(
    monkeypatch: pytest.MonkeyPatch,
    result: str | None = None,
    error: Exception | None = None,
) -> list[dict[str, str]]:
    """替换 llm.complete_chat（ops 经模块属性调用它）；捕获 prompt。"""
    calls: list[dict[str, str]] = []

    async def fake(system_prompt: str, user_prompt: str) -> Any:
        calls.append({"system": system_prompt, "user": user_prompt})
        if error is not None:
            raise error
        assert result is not None
        return result

    monkeypatch.setattr(llm_module, "complete_chat", fake)
    return calls


def _patch_refs(monkeypatch: pytest.MonkeyPatch, refs: list[dict[str, int]]) -> list[int]:
    """替换 compose 的检索缝：返回冻结口径的 refs，并记录被调次数。"""
    calls: list[int] = []

    def fake(db: Any, product_id: int) -> list[dict[str, int]]:
        del db
        calls.append(product_id)
        return copy.deepcopy(refs)

    monkeypatch.setattr(ops_module, "fetch_published_refs", fake)
    return calls


# ---------- 生成输出解析 ----------


def test_parse_plain_json() -> None:
    title, body = parse_generated_output(GOOD_OUTPUT)
    assert title == "钛钢保温杯：一杯守住温度"
    assert "钛钢保温杯" in body


def test_parse_strips_code_fence() -> None:
    title, body = parse_generated_output(f"```json\n{GOOD_OUTPUT}\n```")
    assert title and body


@pytest.mark.parametrize(
    "raw",
    [
        "抱歉，我不确定该写什么。",  # 不是 JSON
        '["标题", "正文"]',  # 不是对象
        '{"title": "只有标题"}',  # 缺 body
        '{"title": "  ", "body": "正文"}',  # 标题空串
        '{"title": "标题", "body": 42}',  # 类型不对
    ],
)
def test_parse_bad_output_raises(raw: str) -> None:
    with pytest.raises(OpsGenError, match="生成结果解析失败"):
        parse_generated_output(raw)


# ---------- prompt 组装与打码（0038 修订：输入过 redact） ----------


def test_prompt_input_is_redacted() -> None:
    product = _product()
    product.spec_values["联系方式"] = {
        "value": "客服手机 13812345678 / 邮箱 zhangsan@example.com",
        "source": {"asset_id": 1, "version": 1},
    }
    prompt = build_generation_prompt(product)
    assert "钛钢保温杯" in prompt  # 商品名+规格事实进 prompt
    assert "13812345678" not in prompt and "zhangsan" not in prompt
    assert "1********78" in prompt and "****@example.com" in prompt


def test_read_product_detail_lists_specs() -> None:
    detail = read_product_detail(_product())
    assert "钛钢保温杯" in detail
    assert "净含量：480ml" in detail
    assert "材质：未写回" in detail  # 规格未写回如实标


def test_read_product_detail_masks_pii_before_ops_runs() -> None:
    """P1③：detail 落 ops_runs.steps（非中台表）——含 PII 的规格写回值在源头
    掩后才进字符串（coaching 落库先例；净值幂等原样，不伤既有断言）。"""
    product = _product()
    product.spec_schema = {**dict(product.spec_schema), "售后电话": {"required": False}}
    product.spec_values["售后电话"] = {
        "value": "13812345678",
        "source": {"asset_id": 1, "version": 1},
    }
    detail = read_product_detail(product)
    assert "13812345678" not in detail
    assert "1********78" in detail


def test_spec_and_fallback_body_masked_before_ops_runs() -> None:
    """P1③：兜底正文（fallback_body → ops_runs.output.body）与卖点原料
    （spec_selling_points）落库前过 redact；诚实披露句不动。"""
    product = _product()
    product.spec_values["售后"] = {
        "value": "邮箱 zhangsan@example.com 或电话 13812345678",
        "source": {"asset_id": 1, "version": 1},
    }
    points = spec_selling_points(product)
    assert any("****@example.com" in p and "1********78" in p for p in points)
    body = fallback_body(product)
    assert "zhangsan" not in body and "13812345678" not in body
    assert "****@example.com" in body and "1********78" in body
    assert NO_REF_DETAIL in body  # 披露句原样（掩的是商品文本非模板文案）


def test_retry_and_deliver_fetch_run_row_with_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    """P1⑥ 锁形状：retry/deliver 的状态机入口取 run 行必须 with_for_update
    （行锁串行化「读→判→复位」临界区）；start_run 建轨不锁形（新行无竞态）。
    锁的并发行为由 test_ops_integration 真 PG 双 deliver 用例钉。"""
    _patch_llm(monkeypatch, result=GOOD_OUTPUT)
    _patch_refs(monkeypatch, [])

    steps = initial_steps()
    steps[0]["status"] = DONE
    steps[1]["status"] = FAILED
    db = _FakeDB(_product(), [_run(steps)])
    retry_run(db, 1)  # 复位续跑到全 done——只钉取行形状
    assert db.run_row_locks == [True]

    done_steps = initial_steps()
    for step in done_steps:
        step["status"] = DONE
        step["detail"] = "ok"
    db2 = _FakeDB(_product(), [_run(done_steps)])
    deliver_run(db2, 1)
    assert db2.run_row_locks == [True]

    fresh = _FakeDB(_product(), [])
    start_run(fresh, 1)
    assert fresh.run_row_locks == []  # 新建无竞态：不锁


# ---------- compose 兜底与引用口径（纯函数面） ----------


def test_fallback_body_carries_specs_and_disclosure() -> None:
    body = fallback_body(_product())
    assert "净含量：480ml" in body  # 规格卖点兜底
    assert NO_REF_DETAIL in body  # 诚实披露
    assert "重新编排" in body


# ---------- 执行环状态机（drive_steps 纯逻辑） ----------


def _plan(statuses: list[str]) -> dict[str, Any]:
    steps = initial_steps()
    for step, status in zip(steps, statuses, strict=True):
        step["status"] = status
    return {"steps": steps, "output": None}


def test_drive_steps_runs_pending_and_skips_done() -> None:
    plan = _plan([DONE, PENDING, PENDING])
    plan["steps"][0]["detail"] = "已读：钛钢保温杯"
    seen: list[tuple[str, Any]] = []
    commits: list[list[str]] = []

    def executor(step: dict[str, Any], p: dict[str, Any]) -> None:
        # 执行时该步必须已是 running（running 前置 commit 的环内兑现）
        assert step["status"] == RUNNING
        seen.append((step["key"], copy.deepcopy(p)))
        if step["key"] == STEP_GEN:
            p["output"] = {"title": "t", "body": "b", "refs": []}
        step["detail"] = f"{step['key']} ok"

    def commit() -> None:
        commits.append([s["status"] for s in plan["steps"]])

    executors = {k: executor for k in (STEP_READ, STEP_GEN, STEP_COMPOSE)}
    drive_steps(plan, executors, commit)

    assert [s[0] for s in seen] == [STEP_GEN, STEP_COMPOSE]  # done 步不重跑
    assert plan["steps"][0]["detail"] == "已读：钛钢保温杯"  # done 步原样
    assert [s["status"] for s in plan["steps"]] == [DONE, DONE, DONE]
    assert commits[0] == [DONE, RUNNING, PENDING]  # running 先落库


def test_drive_steps_failure_breaks_chain_and_opsgen_marks_detail() -> None:
    plan = _plan([PENDING, PENDING, PENDING])
    touched: list[str] = []

    def executor(step: dict[str, Any], p: dict[str, Any]) -> None:
        del p
        touched.append(step["key"])
        if step["key"] == STEP_GEN:
            raise OpsGenError("生成结果解析失败：输出不是合法 JSON")
        step["detail"] = "ok"

    executors = {k: executor for k in (STEP_READ, STEP_GEN, STEP_COMPOSE)}
    drive_steps(plan, executors, lambda: None)

    assert touched == [STEP_READ, STEP_GEN]  # 失败步之后不执行
    assert [s["status"] for s in plan["steps"]] == [DONE, FAILED, PENDING]  # 后续停 pending
    assert plan["steps"][1]["detail"] == "生成结果解析失败：输出不是合法 JSON"


# ---------- start_run / retry_run / deliver_run 全链（_FakeDB） ----------


def test_start_run_product_404(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_llm(monkeypatch, result=GOOD_OUTPUT)
    with pytest.raises(HTTPException, match="404"):
        start_run(_FakeDB(None, []), 9999)


def test_start_run_happy_path_with_refs(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_llm(monkeypatch, result=GOOD_OUTPUT)
    refs_calls = _patch_refs(monkeypatch, [{"asset_id": 5, "version_no": 2}])
    db = _FakeDB(_product(), [])
    run = start_run(db, 1)

    assert [s["status"] for s in run.steps] == [DONE, DONE, DONE]
    assert [s["key"] for s in run.steps] == [STEP_READ, STEP_GEN, STEP_COMPOSE]
    assert "净含量：480ml" in run.steps[0]["detail"]
    assert "钛钢保温杯" in calls[0]["user"]  # 生成依据=商品事实
    assert run.output == {
        "title": "钛钢保温杯：一杯守住温度",
        "body": json.loads(GOOD_OUTPUT)["body"],
        "refs": [{"asset_id": 5, "version_no": 2}],  # refs=fetch 的冻结版本原样
    }
    assert "引用 1 件" in run.steps[2]["detail"]
    assert refs_calls == [1]
    # 每步 running 前置 commit + 终态 commit：建 run 1 + 3×2 = 7
    assert db.commits == 7


def test_start_run_no_refs_compose_fallback_discloses(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_llm(monkeypatch, result=GOOD_OUTPUT)
    _patch_refs(monkeypatch, [])
    run = start_run(_FakeDB(_product(), []), 1)

    assert [s["status"] for s in run.steps] == [DONE, DONE, DONE]
    assert run.output["refs"] == []
    assert run.output["body"] != json.loads(GOOD_OUTPUT)["body"]  # 兜底正文替换草稿
    assert NO_REF_DETAIL in run.output["body"]
    assert run.steps[2]["detail"] == NO_REF_DETAIL  # 步轨迹诚实披露


def test_generated_output_masked_before_ops_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    """第 26 刀评审收尾件（0038 出口必掩）：厂商返回的 title/body 落
    ops_runs.output 前过 redact——prompt 已掩但模型可自发吐裸号，草稿落
    非中台表不留底；compose 兜底标题同口径（防御分支，gen done 时不触）。"""
    pii_output = json.dumps(
        {
            "title": "钛钢保温杯热销 13812345678",
            "body": "有问题联系 zhangsan@example.com，钛钢保温杯好用",
        },
        ensure_ascii=False,
    )
    _patch_llm(monkeypatch, result=pii_output)
    _patch_refs(monkeypatch, [{"asset_id": 5, "version_no": 1}])  # 有引用：output 走草稿本体
    run = start_run(_FakeDB(_product(), []), 1)

    assert run.output is not None
    assert run.output["title"] == "钛钢保温杯热销 1********78"
    assert run.output["body"] == "有问题联系 ****@example.com，钛钢保温杯好用"
    assert "13812345678" not in json.dumps(run.output, ensure_ascii=False)
    assert "zhangsan" not in json.dumps(run.output, ensure_ascii=False)
    # 草稿标题进 steps.detail 同掩（同一字符串进两处都净）
    assert "13812345678" not in run.steps[1]["detail"]


def test_compose_fallback_title_masked_for_pii_product_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同口径的兜底标题：gen 成功即不触（output 非空），但商品名带号进防御
    分支时仍掩——用空 output 直调 compose 钉住该形状。"""
    product = _product()
    product.name = "钛钢保温杯 13812345678"
    _patch_refs(monkeypatch, [])  # 无引用 -> 走兜底标题分支
    db = _FakeDB(product, [])
    step = initial_steps()[2]
    plan: dict = {"steps": [], "output": None}
    ops_module._run_executors(db, product)[STEP_COMPOSE](step, plan)
    assert "13812345678" not in plan["output"]["title"]
    assert "1********78" in plan["output"]["title"]


def test_gen_material_no_key_fails_without_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_llm(
        monkeypatch,
        error=llm_module.LLMNotConfigured("未配置 LLM_API_KEY，厂商生成不可用"),
    )
    _patch_refs(monkeypatch, [])
    run = start_run(_FakeDB(_product(), []), 1)

    assert [s["status"] for s in run.steps] == [DONE, FAILED, PENDING]
    assert "生成不可用" in run.steps[1]["detail"]
    assert run.output is None  # 无降级：失败不留生成物
    assert run.delivered_at is None


def test_gen_material_llm_error_and_bad_output_grading(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_llm(monkeypatch, error=llm_module.LLMUnavailable("厂商模型暂时不可用"))
    _patch_refs(monkeypatch, [])
    run = start_run(_FakeDB(_product(), []), 1)
    assert "生成不可用" in run.steps[1]["detail"]

    _patch_llm(monkeypatch, result="这我写不出来。")
    run = start_run(_FakeDB(_product(), []), 1)
    assert "生成结果解析失败" in run.steps[1]["detail"]


def test_retry_resumes_from_failed_step_without_rerun_done(monkeypatch: pytest.MonkeyPatch) -> None:
    # 第一轮：LLM 故障 → 断在 gen_material
    _patch_llm(
        monkeypatch,
        error=llm_module.LLMNotConfigured("未配置 LLM_API_KEY，厂商生成不可用"),
    )
    _patch_refs(monkeypatch, [])
    db = _FakeDB(_product(), [])
    run = start_run(db, 1)
    read_detail = copy.deepcopy(run.steps[0])

    # 第二轮：配好 key 重试 → 从失败步续跑
    calls = _patch_llm(monkeypatch, result=GOOD_OUTPUT)
    retries = retry_run(db, run.id)

    assert [s["status"] for s in retries.steps] == [DONE, DONE, DONE]
    assert retries.steps[0] == read_detail  # done 步原样未重跑（detail 逐字不变）
    assert len(calls) == 1  # 重试只补生成一次
    assert retries.output["refs"] == []
    assert retries.output["body"] != json.loads(GOOD_OUTPUT)["body"]  # 兜底披露仍在


def test_retry_illegal_transitions_409(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_llm(monkeypatch, result=GOOD_OUTPUT)
    _patch_refs(monkeypatch, [])
    db = _FakeDB(_product(), [])
    run = start_run(db, 1)
    # 全部 done：没有失败步可续跑
    with pytest.raises(HTTPException, match="409"):
        retry_run(db, run.id)
    # 已投放：终局确认后不再改轨迹
    deliver_run(db, run.id)
    with pytest.raises(HTTPException, match="409"):
        retry_run(db, run.id)
    # run 不存在 404
    with pytest.raises(HTTPException, match="404"):
        retry_run(db, 9999)


def test_retry_restores_stale_running_step(monkeypatch: pytest.MonkeyPatch) -> None:
    # 进程崩在 LLM 等待里的残存 running 态：retry 视同失败步复位续跑
    steps = initial_steps()
    steps[0]["status"] = DONE
    steps[1]["status"] = RUNNING
    _patch_llm(monkeypatch, result=GOOD_OUTPUT)
    _patch_refs(monkeypatch, [])
    db = _FakeDB(_product(), [_run(steps)])
    run = retry_run(db, 1)
    assert [s["status"] for s in run.steps] == [DONE, DONE, DONE]


def test_deliver_state_machine(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_llm(monkeypatch, result=GOOD_OUTPUT)
    _patch_refs(monkeypatch, [{"asset_id": 5, "version_no": 1}])
    db = _FakeDB(_product(), [])
    run = start_run(db, 1)

    delivered = deliver_run(db, run.id)
    assert delivered.delivered_at is not None
    assert delivered.output is not None  # 投放不改产出

    # 不改任何资产三态：本服务全程没碰 Asset 写路径（无 storage/Asset 入参即结构保证）
    with pytest.raises(HTTPException, match="已投放"):
        deliver_run(db, run.id)

    # 未完成不可投放
    _patch_llm(
        monkeypatch,
        error=llm_module.LLMNotConfigured("未配置 LLM_API_KEY，厂商生成不可用"),
    )
    unfinished = start_run(_FakeDB(_product(), []), 1)
    db2 = _FakeDB(_product(), [unfinished])
    with pytest.raises(HTTPException, match="409"):
        deliver_run(db2, unfinished.id)

    with pytest.raises(HTTPException, match="404"):
        deliver_run(db2, 9999)


def test_initial_steps_shape_frozen() -> None:
    steps = initial_steps()
    assert [s["key"] for s in steps] == [STEP_READ, STEP_GEN, STEP_COMPOSE]
    assert all(s["status"] == PENDING and s["detail"] == "" for s in steps)
    assert all({"key", "name", "via", "status", "detail"} == set(s) for s in steps)
    assert steps[1]["via"] == "厂商模型"  # 草稿不落任务不落成资产，via 如实标
