"""素材中心单元测试（不依赖 DB，LLM 以替身注入不发外网；第 17 刀/ADR 0038）。

覆盖四块纯逻辑与状态机：
- 规则质检 qc_check 四规则（正文非空/总长≤2000/标题非空/正文含商品名）；
- 生成输出解析 parse_generated_output（好 JSON/围栏/坏 JSON/坏形状）；
- 打码 redact 三态（手机号/邮箱/无 PII）+ 打码步三接入点（prompt、qa 值、
  文档字段值）+ 生成 user prompt 出口掩（第 26 刀 P1①，0038 修订补全）；
- 任务状态机转移矩阵（run/approve/reject/retry × 五态：非法转移 409，
  生成失败分级 failed——空 key/LLM 故障=「生成不可用」，坏输出=解析失败，
  规则不过=记规则项；打回=人工打回；retry 复位重跑）。
"""

import json
from typing import Any

import pytest
from fastapi import HTTPException

from suite_api.models import MaterialTask, Product
from suite_api.services import llm as llm_module
from suite_api.services.machine_wash import (
    build_qa_prompt,
    extract_document_fields,
    parse_qa_output,
    redact,
)
from suite_api.services.material import (
    FAILED,
    MAX_TOTAL_CHARS,
    PENDING_QC,
    QUEUED,
    REGISTERED,
    RUNNING,
    MaterialGenError,
    approve_task,
    build_generation_prompt,
    parse_generated_output,
    qc_check,
    reject_task,
    retry_task,
    run_generation_task,
)

# ---------- 替身 ----------


class _FakeDB:
    """只喂 run_generation_task 用到的最小会话面：get(Product)/commit 记账。"""

    def __init__(self, product: Product) -> None:
        self.product = product
        self.commits = 0

    def get(self, model: Any, pk: Any) -> Any:
        assert model is Product
        return self.product

    def commit(self) -> None:
        self.commits += 1


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


def _task(status: str = QUEUED) -> MaterialTask:
    return MaterialTask(id=1, product_id=1, status=status, title=None, content=None)


def _patch_llm(
    monkeypatch: pytest.MonkeyPatch,
    result: str | None = None,
    error: Exception | None = None,
) -> list[dict[str, str]]:
    """替换 llm.complete_chat（material 服务经模块属性调用它）；捕获 prompt。"""
    calls: list[dict[str, str]] = []

    async def fake(system_prompt: str, user_prompt: str) -> Any:
        calls.append({"system": system_prompt, "user": user_prompt})
        if error is not None:
            raise error
        assert result is not None
        return result

    monkeypatch.setattr(llm_module, "complete_chat", fake)
    return calls


GOOD_OUTPUT = (
    '{"title": "钛钢保温杯：一杯守住温度", '
    '"content": "卖点一：钛钢保温杯，双层真空持久保温。\\n材质：钛钢\\n净含量：480ml"}'
)


# ---------- 规则质检四规则（qc_check） ----------


def test_qc_pass() -> None:
    assert qc_check("标题", "这款钛钢保温杯值得拥有", "钛钢保温杯") == []


def test_qc_empty_content() -> None:
    errors = qc_check("标题", "   ", "钛钢保温杯")
    assert "正文不能为空" in errors


def test_qc_empty_title_and_none_content() -> None:
    errors = qc_check("  ", None, "钛钢保温杯")
    assert "标题不能为空" in errors
    assert "正文不能为空" in errors


def test_qc_total_length_cap() -> None:
    long_title = "标" * 200
    long_content = "钛钢保温杯" + "杯" * 1900  # 总长 2001 > 2000
    errors = qc_check(long_title, long_content, "钛钢保温杯")
    assert any("2000" in e for e in errors)


def test_qc_boundary_exactly_2000_passes() -> None:
    title = "标" * 100
    content = "钛钢保温杯" + "杯" * (2000 - 100 - len("钛钢保温杯"))  # 总长恰好 2000
    assert qc_check(title, content, "钛钢保温杯") == []


def test_qc_content_must_contain_product_name() -> None:
    errors = qc_check("标题", "很好用的杯子", "钛钢保温杯")
    assert any("钛钢保温杯" in e for e in errors)


# ---------- 生成输出解析 ----------


def test_parse_plain_json() -> None:
    title, content = parse_generated_output(GOOD_OUTPUT)
    assert title == "钛钢保温杯：一杯守住温度"
    assert "钛钢保温杯" in content


def test_parse_strips_code_fence() -> None:
    title, content = parse_generated_output(f"```json\n{GOOD_OUTPUT}\n```")
    assert title and content


@pytest.mark.parametrize(
    "raw",
    [
        "抱歉，我不确定该写什么。",  # 不是 JSON
        '["标题", "正文"]',  # 不是对象
        '{"title": "只有标题"}',  # 缺 content
        '{"title": "  ", "content": "正文"}',  # 标题空串
        '{"title": "标题", "content": 42}',  # 类型不对
    ],
)
def test_parse_bad_output_raises(raw: str) -> None:
    with pytest.raises(MaterialGenError, match="生成结果解析失败"):
        parse_generated_output(raw)


# ---------- 打码三态 + 接入点 ----------


def test_redact_phone_keeps_head_and_tail() -> None:
    assert redact("手机 13812345678") == "手机 1********78"


def test_redact_email_masks_local_keeps_domain() -> None:
    assert redact("邮箱 zhangsan@example.com 请查收") == "邮箱 ****@example.com 请查收"
    # local < 3 字符不打码（占位/示例噪音）
    assert redact("ab@example.com") == "ab@example.com"


def test_redact_no_pii_untouched_and_idempotent() -> None:
    clean = "净含量：480ml，保质期 12 个月，订单号 1234567890123。"
    assert redact(clean) == clean
    once = redact("手机 13812345678")
    assert redact(once) == once  # 掩码后不再是合法号段，幂等


@pytest.mark.parametrize(
    "text",
    [
        "电话13812345678",  # 紧贴中文
        "尾号13987654321。",  # 句读收尾
        "15012345678@dingtalk.com",  # 号段出现在邮箱 local：邮箱先掩，不留裸号
    ],
)
def test_redact_phone_variants(text: str) -> None:
    out = redact(text)
    assert "13812345678" not in out and "13987654321" not in out and "15012345678" not in out


def test_redact_rejects_12_digit_run() -> None:
    # 前后否定环视：更长的连续数字串不从中段咬出「手机号」
    assert redact("订单号 1234567890123456") == "订单号 1234567890123456"


def test_prompt_input_is_redacted() -> None:
    prompt = build_qa_prompt("顾客：我的手机 13812345678，邮箱 zhangsan@example.com")
    assert "13812345678" not in prompt
    assert "zhangsan" not in prompt
    assert "1********78" in prompt and "****@example.com" in prompt


def test_parsed_qa_values_are_redacted() -> None:
    pairs = parse_qa_output(
        '[{"q": "安装要留电话吗", "a": "登记 13812345678，师傅联系 zhangsan@example.com"}]'
    )
    assert pairs == [
        {"q": "安装要留电话吗", "a": "登记 1********78，师傅联系 ****@example.com"}
    ]


def test_document_field_values_are_redacted() -> None:
    # 抽取值落库前过打码（接入点 c）：字段值里混的裸号不留底
    extracted = extract_document_fields("材质：13812345678", ["材质"])
    assert extracted["材质"] == {"value": "1********78", "source": "machine"}


def test_generation_prompt_masks_product_text() -> None:
    """第 26 刀 P1①（0038 修订补全，孪生于 ops.build_generation_prompt）：素材
    生成 user prompt 送厂商前，商品名/类目/规格字段/已写回值统一过 redact——
    厂商 prompt 是进程边界。干净值幂等原样通过，不破坏既有生成断言。"""
    product = Product(
        id=1,
        name="钛钢保温杯",
        category="器皿",
        spec_schema={"净含量": {"required": True}, "售后电话": {"required": True}},
        spec_values={
            "净含量": {"value": "480ml", "source": {"asset_id": 1, "version": 1}},
            "售后电话": {"value": "13812345678", "source": {"asset_id": 1, "version": 1}},
        },
    )
    prompt = build_generation_prompt(product)
    assert "13812345678" not in prompt
    assert "1********78" in prompt
    assert "钛钢保温杯" in prompt  # 无 PII 事实照进 prompt
    assert "480ml" in prompt


# ---------- 状态机转移矩阵 ----------


@pytest.mark.parametrize("from_status", [RUNNING, PENDING_QC, REGISTERED])
def test_run_illegal_from(from_status: str) -> None:
    with pytest.raises(HTTPException, match="409"):
        run_generation_task(_FakeDB(_product()), _task(from_status))  # type: ignore[arg-type]


@pytest.mark.parametrize("from_status", [QUEUED, RUNNING, REGISTERED])
def test_approve_illegal_from(from_status: str) -> None:
    with pytest.raises(HTTPException, match="409"):
        approve_task(None, None, _task(from_status))  # type: ignore[arg-type]


@pytest.mark.parametrize("from_status", [QUEUED, RUNNING, REGISTERED])
def test_reject_illegal_from(from_status: str) -> None:
    db = _FakeDB(_product())
    with pytest.raises(HTTPException, match="409"):
        reject_task(db, _task(from_status))  # type: ignore[arg-type]
    assert db.commits == 0  # 非法转移抛在 commit 前，半途写不落库


@pytest.mark.parametrize("from_status", [QUEUED, RUNNING, PENDING_QC, REGISTERED])
def test_retry_illegal_from(from_status: str) -> None:
    with pytest.raises(HTTPException, match="409"):
        retry_task(None, _task(from_status))  # type: ignore[arg-type]


def test_reject_pending_qc_marks_manual_failure() -> None:
    task = _task(PENDING_QC)
    db = _FakeDB(_product())
    reject_task(db, task)  # type: ignore[arg-type]
    assert task.status == FAILED
    assert task.last_error == "人工打回"
    assert db.commits == 1  # debt-2：commit 收在服务层，路由层不再补


def test_run_queued_to_pending_qc(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_llm(monkeypatch, result=GOOD_OUTPUT)
    task = _task(QUEUED)
    db = _FakeDB(_product())
    run_generation_task(db, task)  # type: ignore[arg-type]
    assert task.status == PENDING_QC
    assert task.title == "钛钢保温杯：一杯守住温度"
    assert task.last_error is None
    assert db.commits >= 2  # running 先落库（LLM 等待不持事务），终态再收口
    assert "钛钢保温杯" in calls[0]["user"]  # 商品名进 prompt


def test_retry_enters_run_from_failed_state_not_queued(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """debt-2 死转移收口：retry 以行真实态 failed 直接进 run（run 内推到
    running），不再先伪造内存 queued 中间态。"""
    seen: list[str] = []

    def spy(_db: Any, t: MaterialTask) -> MaterialTask:
        seen.append(t.status)
        return t

    monkeypatch.setattr(
        "suite_api.services.material.run_generation_task", spy
    )
    task = _task(FAILED)
    task.last_error = "人工打回"
    retry_task(None, task)  # type: ignore[arg-type]
    assert seen == [FAILED]


def test_qc_uses_truncated_title_matching_persisted_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """debt-2 qc 口径：质检用截断后 title（与落库同一字符串）——原始 250 字
    标题 + 1800 字正文总长 2050 会误判超限，按 String(200) 列宽截断后 2000
    恰好过线，且落库标题就是参与质检的那 200 字。"""
    long_title = "标" * 250
    content = "钛钢保温杯" + "杯" * 1795  # 1800 字，含商品名
    _patch_llm(monkeypatch, result=json.dumps({"title": long_title, "content": content}))
    task = _task(QUEUED)
    run_generation_task(_FakeDB(_product()), task)  # type: ignore[arg-type]
    assert task.status == PENDING_QC
    assert task.title == "标" * 200
    assert len(task.title) + len(task.content) == MAX_TOTAL_CHARS


@pytest.mark.parametrize(
    "error",
    [
        llm_module.LLMNotConfigured("未配置 LLM_API_KEY，厂商生成不可用"),
        llm_module.LLMUnavailable("厂商模型暂时不可用"),
    ],
)
def test_run_llm_error_fails_without_fallback(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    _patch_llm(monkeypatch, error=error)
    task = _task(QUEUED)
    run_generation_task(_FakeDB(_product()), task)  # type: ignore[arg-type]
    assert task.status == FAILED
    assert "生成不可用" in task.last_error
    assert task.content is None  # 无降级模板：失败不留任何生成物


def test_run_bad_output_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_llm(monkeypatch, result="这我写不出来。")
    task = _task(QUEUED)
    run_generation_task(_FakeDB(_product()), task)  # type: ignore[arg-type]
    assert task.status == FAILED
    assert "生成结果解析失败" in task.last_error


def test_run_qc_violation_records_rule_items(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_llm(
        monkeypatch,
        result='{"title": "好文案", "content": "完全没有商品名的正文"}',
    )
    task = _task(QUEUED)
    run_generation_task(_FakeDB(_product()), task)  # type: ignore[arg-type]
    assert task.status == FAILED
    assert "规则质检不过线" in task.last_error
    assert "商品名" in task.last_error


def test_retry_reruns_failed_task(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_llm(monkeypatch, result=GOOD_OUTPUT)
    task = _task(FAILED)
    task.last_error = "生成不可用：上次故障"
    retry_task(_FakeDB(_product()), task)  # type: ignore[arg-type]
    assert task.status == PENDING_QC
    assert task.last_error is None  # 复位：旧故障原因清空
    assert "钛钢保温杯" in (task.content or "")
