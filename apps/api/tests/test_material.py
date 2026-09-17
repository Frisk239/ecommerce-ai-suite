"""素材中心单元测试（不依赖 DB，LLM 以替身注入不发外网；第 17 刀/ADR 0038；
第 98 刀内容套件/ADR 0055）。

覆盖纯逻辑与状态机：
- 规则质检 qc_check 四规则（正文非空/总长≤2000/标题非空/正文含商品名）；
- 生成输出解析 parse_generated_output（好 JSON/围栏/坏 JSON/坏形状）；
- 打码 redact 三态（手机号/邮箱/无 PII）+ 打码步三接入点 + 生成 user prompt 出口掩
  （第 26 刀 P1①，0038 修订补全）；
- 内容模板（第 98 刀）：三键枚举/validate_template、模板派生 system prompt 与
  配图 prompt/尺寸/标题/描述预填（纯函数钉形状）；
- LLM 事实性质检二道闸（第 98 刀）：parse_qc_output 形状校验、判定不过=failed
  文案保留、坏输出 fail-closed、规则闸先挡则 LLM 闸不跑（两闸独立记录）；
- 配图步三态（第 98 刀）：无 key 跳过/成功暂存/失败不 fail 任务（替身+内存存储）；
- 任务状态机转移矩阵（run/approve/reject/retry × 五态：非法转移 409，生成失败
  分级 failed，打回=人工打回，retry 复位重跑）。
"""

import json
from typing import Any

import pytest
from fastapi import HTTPException

from suite_api.models import MaterialTask, Product
from suite_api.services import cover_card as cover_card_module
from suite_api.services import imggen as imggen_module
from suite_api.services import llm as llm_module
from suite_api.services import material as material_module
from suite_api.services.cover_card import sanitize_title
from suite_api.services.machine_wash import (
    build_qa_prompt,
    extract_document_fields,
    parse_qa_output,
    redact,
)
from suite_api.services.material import (
    FAILED,
    IMAGE_FAILED,
    IMAGE_NONE,
    IMAGE_PENDING,
    IMAGE_REQUESTED,
    IMAGE_SKIPPED_NO_IMAGE,
    IMAGE_SKIPPED_NO_KEY,
    MAX_TOTAL_CHARS,
    PENDING_QC,
    QUEUED,
    REGISTERED,
    RUNNING,
    TEMPLATES,
    MaterialGenError,
    approve_task,
    build_edit_instruction,
    build_generation_prompt,
    build_image_prompt,
    build_qc_prompt,
    generation_system_prompt,
    image_description_preset,
    image_size_for,
    image_title,
    parse_generated_output,
    parse_qc_output,
    qc_check,
    reject_task,
    retry_task,
    run_generation_task,
    validate_template,
)

# ---------- 替身 ----------


class _FakeDB:
    """只喂 run_generation_task 用到的最小会话面：get(Product)/commit 记账；
    scalars 恒空（第 115 刀站内美化路径的参考图解析走「无图片资产」分支）。"""

    def __init__(self, product: Product) -> None:
        self.product = product
        self.commits = 0

    def get(self, model: Any, pk: Any) -> Any:
        assert model is Product
        return self.product

    def commit(self) -> None:
        self.commits += 1

    def scalars(self, _stmt: Any) -> Any:
        class _Empty:
            def all(self) -> list[Any]:
                return []

        return _Empty()


class _FakeStorage:
    """配图暂存的最小对象存储面（put/get/delete 记账，内存字典）。"""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_bytes(self, key: str, data: bytes) -> None:
        self.objects[key] = data

    def get_bytes(self, key: str) -> bytes:
        if key not in self.objects:
            raise FileNotFoundError(key)
        return self.objects[key]

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)


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


def _task(
    status: str = QUEUED, *, template: str = "station", image: str = IMAGE_NONE
) -> MaterialTask:
    return MaterialTask(
        id=1,
        product_id=1,
        status=status,
        title=None,
        content=None,
        template=template,
        image_status=image,
    )


# LLM 质检通过的标准回执（第二道闸的替身应答）
QC_PASS = '{"passed": true, "issues": []}'
QC_FAIL_NET_CONTENT = '{"passed": false, "issues": ["正文称净含量 990ml，与规格 480ml 矛盾", "夸大功效：宣称永久保温"]}'


def _patch_llm(
    monkeypatch: pytest.MonkeyPatch,
    script: list[Any] | None = None,
    result: str | None = None,
    error: Exception | None = None,
) -> list[dict[str, str]]:
    """替换 llm.complete_chat（material 服务经模块属性调用它）；捕获 prompt。

    ``script``：逐次调用的应答（字符串或 Exception 实例——实例则抛出），按序
    弹出；耗尽再被调即断言失败（意外的额外 LLM 调用哨兵）。``result``/
    ``error`` 是单值便捷形态（等价单元素/每次抛）。第 98 刀起一次任务有两处
    LLM 等待点（生成 + 事实性质检），替身必须按调用序分派。
    """
    calls: list[dict[str, str]] = []
    queue: list[Any] = list(script or [])
    if result is not None and not queue:
        queue = [result]

    async def fake(system_prompt: str, user_prompt: str) -> Any:
        calls.append({"system": system_prompt, "user": user_prompt})
        if error is not None:
            raise error
        if not queue:
            raise AssertionError("意外的额外 LLM 调用（替身脚本已耗尽）")
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(llm_module, "complete_chat", fake)
    return calls


def _patch_imggen(
    monkeypatch: pytest.MonkeyPatch,
    result: bytes | None = None,
    error: Exception | None = None,
) -> list[dict[str, str]]:
    """替换 imggen.generate_image：捕获 {prompt, size}；三态由参数分派。"""
    calls: list[dict[str, str]] = []

    def fake(prompt: str, *, size: str) -> bytes:
        calls.append({"prompt": prompt, "size": size})
        if error is not None:
            raise error
        assert result is not None
        return result

    monkeypatch.setattr(imggen_module, "generate_image", fake)
    return calls


# 最小合法 PNG 字节（魔数即可——暂存键后缀与预览端点的 mime 都只看魔数）
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


GOOD_OUTPUT = (
    '{"title": "钛钢保温杯：一杯守住温度", '
    '"content": "卖点一：钛钢保温杯，双层真空持久保温。\\n材质：钛钢\\n净含量：480ml"}'
)


# ---------- 内容模板（第 98 刀，纯函数钉形状） ----------


def test_templates_three_keys_and_names() -> None:
    assert set(TEMPLATES) == {"station", "xhs", "short_video"}
    assert TEMPLATES["station"]["name"] == "站内投放文案"
    assert TEMPLATES["xhs"]["name"] == "小红书笔记体"
    assert TEMPLATES["short_video"]["name"] == "短视频口播稿"


def test_validate_template() -> None:
    assert validate_template("station") == "station"
    assert validate_template("xhs") == "xhs"
    with pytest.raises(ValueError, match="内容模板"):
        validate_template("weibo")


def test_generation_system_prompt_is_template_derived() -> None:
    station = generation_system_prompt("station")
    xhs = generation_system_prompt("xhs")
    short = generation_system_prompt("short_video")
    # 共同纪律不丢：只依据商品信息、不编造、JSON 输出形状
    for prompt in (station, xhs, short):
        assert "不要编造" in prompt
        assert '"title"' in prompt and '"content"' in prompt
    # 模板风格各自落位（站内=字段：值行文；小红书=emoji+话题标签；口播=分镜节奏）
    assert "字段：值" in station and "emoji" not in station
    assert "emoji" in xhs and "话题标签" in xhs
    assert "分镜" in short and "开场钩子" in short


def test_build_image_prompt_derives_from_template() -> None:
    product = _product()
    for template in TEMPLATES:
        prompt = build_image_prompt(product, template)
        assert "钛钢保温杯" in prompt  # 商品名进图 prompt（与文案配套）
        assert TEMPLATES[template]["image_prompt"][:6] in prompt  # 模板风格行在前
        assert "不要出现任何文字" in prompt  # 文生图模型渲染文字易糊


def test_build_image_prompt_masks_contact() -> None:
    product = Product(id=1, name="联系 13812345678 买杯", category="器皿")
    prompt = build_image_prompt(product, "station")
    assert "13812345678" not in prompt  # 0038 出口必掩：图 prompt 也是厂商边界


def test_image_size_for_template() -> None:
    assert image_size_for("station") == "1024x1024"  # 商品横图（方图档）
    assert image_size_for("xhs") == "768x1024"  # 小红书封面竖版
    assert image_size_for("short_video") == "768x1024"  # 口播竖版背景


def test_image_title_and_description_preset() -> None:
    assert image_title("钛钢保温杯", "xhs") == "钛钢保温杯 · 小红书笔记体配图"
    long_name = "长" * 60
    assert len(image_title(long_name, "station")) <= 200  # 基名截 40，列宽收口
    content = "第一行卖点：钛钢保温杯。\n第二行：材质钛钢。\n净含量：480ml"
    assert image_description_preset(content) == "第一行卖点：钛钢保温杯。"
    # 无非空行兜底空串（登记端不会拿空串冒充描述——register 侧 0009 闸兜底）
    assert image_description_preset("\n  \n") == ""
    # 描述预填也过 redact（0038：落库 extracted 不留裸 PII）
    assert image_description_preset("联系 13812345678 下单") == "联系 1********78 下单"


def test_qc_prompt_contains_facts_and_copy() -> None:
    product = _product()
    prompt = build_qc_prompt("标题", "正文净含量 480ml", product)
    assert "480ml" in prompt  # 规格事实面
    assert "标题" in prompt and "正文净含量 480ml" in prompt  # 待审文案


# ---------- LLM 质检输出解析（parse_qc_output） ----------


def test_parse_qc_output_pass() -> None:
    assert parse_qc_output(QC_PASS) == (True, [])


def test_parse_qc_output_fail_with_issues() -> None:
    passed, issues = parse_qc_output(QC_FAIL_NET_CONTENT)
    assert passed is False
    assert len(issues) == 2
    assert "990ml" in issues[0]


def test_parse_qc_output_strips_fence_and_blank_issues() -> None:
    passed, issues = parse_qc_output(f"```json\n{QC_PASS}\n```")
    assert (passed, issues) == (True, [])
    passed, issues = parse_qc_output('{"passed": false, "issues": ["  ", "真问题 "]}')
    assert (passed, issues) == (False, ["真问题"])


@pytest.mark.parametrize(
    "raw",
    [
        "这段文案不错。",  # 不是 JSON
        '["passed"]',  # 不是对象
        '{"issues": []}',  # 缺 passed
        '{"passed": "yes", "issues": []}',  # passed 非布尔
        '{"passed": true, "issues": "没有问题"}',  # issues 非数组
        '{"passed": true, "issues": [1]}',  # issues 项非字符串
    ],
)
def test_parse_qc_output_bad_shapes_raise(raw: str) -> None:
    with pytest.raises(MaterialGenError, match="LLM 质检输出不可解析"):
        parse_qc_output(raw)


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
    assert pairs == [{"q": "安装要留电话吗", "a": "登记 1********78，师傅联系 ****@example.com"}]


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
        run_generation_task(_FakeDB(_product()), None, _task(from_status))  # type: ignore[arg-type]


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
        retry_task(None, None, _task(from_status))  # type: ignore[arg-type]


def test_reject_pending_qc_marks_manual_failure() -> None:
    task = _task(PENDING_QC)
    db = _FakeDB(_product())
    reject_task(db, task)  # type: ignore[arg-type]
    assert task.status == FAILED
    assert task.last_error == "人工打回"
    assert db.commits == 1  # debt-2：commit 收在服务层，路由层不再补


def test_run_queued_to_pending_qc(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_llm(monkeypatch, script=[GOOD_OUTPUT, QC_PASS])
    task = _task(QUEUED)
    db = _FakeDB(_product())
    run_generation_task(db, None, task)  # type: ignore[arg-type]
    assert task.status == PENDING_QC
    assert task.title == "钛钢保温杯：一杯守住温度"
    assert task.last_error is None
    assert task.qc_llm_passed is True  # 两闸独立记录：LLM 闸结果落列
    assert db.commits >= 2  # running 先落库（LLM 等待不持事务），终态再收口
    assert "钛钢保温杯" in calls[0]["user"]  # 商品名进生成 prompt
    # 第二次调用=LLM 事实性质检：同一事实面 + 待审文案
    assert "480ml" in calls[1]["user"] and "钛钢保温杯" in calls[1]["user"]


def test_run_uses_template_system_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_llm(monkeypatch, script=[GOOD_OUTPUT, QC_PASS])
    task = _task(QUEUED, template="xhs")
    run_generation_task(_FakeDB(_product()), None, task)  # type: ignore[arg-type]
    assert task.status == PENDING_QC
    assert "小红书" in calls[0]["system"]  # 模板风格进生成 system prompt


def test_retry_enters_run_from_failed_state_not_queued(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """debt-2 死转移收口：retry 以行真实态 failed 直接进 run（run 内推到
    running），不再先伪造内存 queued 中间态。"""
    seen: list[str] = []

    def spy(_db: Any, _storage: Any, t: MaterialTask) -> MaterialTask:
        seen.append(t.status)
        return t

    monkeypatch.setattr(material_module, "run_generation_task", spy)
    task = _task(FAILED)
    task.last_error = "人工打回"
    retry_task(None, None, task)  # type: ignore[arg-type]
    assert seen == [FAILED]


def test_qc_uses_truncated_title_matching_persisted_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """debt-2 qc 口径：质检用截断后 title（与落库同一字符串）——原始 250 字
    标题 + 1800 字正文总长 2050 会误判超限，按 String(200) 列宽截断后 2000
    恰好过线，且落库标题就是参与质检的那 200 字。"""
    long_title = "标" * 250
    content = "钛钢保温杯" + "杯" * 1795  # 1800 字，含商品名
    _patch_llm(
        monkeypatch,
        script=[json.dumps({"title": long_title, "content": content}), QC_PASS],
    )
    task = _task(QUEUED)
    run_generation_task(_FakeDB(_product()), None, task)  # type: ignore[arg-type]
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
    run_generation_task(_FakeDB(_product()), None, task)  # type: ignore[arg-type]
    assert task.status == FAILED
    assert "生成不可用" in task.last_error
    assert task.content is None  # 无降级模板：失败不留任何生成物


def test_run_bad_output_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_llm(monkeypatch, script=["这我写不出来。"])
    task = _task(QUEUED)
    run_generation_task(_FakeDB(_product()), None, task)  # type: ignore[arg-type]
    assert task.status == FAILED
    assert "生成结果解析失败" in task.last_error


def test_run_qc_violation_records_rule_items(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_llm(
        monkeypatch,
        script=['{"title": "好文案", "content": "完全没有商品名的正文"}'],
    )
    task = _task(QUEUED)
    run_generation_task(_FakeDB(_product()), None, task)  # type: ignore[arg-type]
    assert task.status == FAILED
    assert "规则质检不过线" in task.last_error
    assert "商品名" in task.last_error
    assert task.qc_llm_passed is None  # 规则闸先挡下：LLM 闸未跑到（独立记录）
    assert len(calls) == 1  # 哨兵：规则不过不再调 LLM 质检


# ---------- LLM 事实性质检二道闸（第 98 刀） ----------


def test_llm_qc_fail_blocks_pending_qc(monkeypatch: pytest.MonkeyPatch) -> None:
    """矛盾文案被二道闸拦截：不进待抽检、failed 可重试、文案保留供人看。"""
    _patch_llm(monkeypatch, script=[GOOD_OUTPUT, QC_FAIL_NET_CONTENT])
    task = _task(QUEUED)
    run_generation_task(_FakeDB(_product()), None, task)  # type: ignore[arg-type]
    assert task.status == FAILED
    assert "LLM 事实性质检不过线" in task.last_error
    assert "990ml" in task.last_error  # 拦截原因带具体矛盾
    assert task.qc_llm_passed is False
    assert task.title is not None and "钛钢保温杯" in task.content  # 预览面保留


def test_llm_qc_unparseable_output_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_llm(monkeypatch, script=[GOOD_OUTPUT, "我觉得还行。"])
    task = _task(QUEUED)
    run_generation_task(_FakeDB(_product()), None, task)  # type: ignore[arg-type]
    assert task.status == FAILED
    assert "LLM 质检输出不可解析" in task.last_error
    assert task.qc_llm_passed is False


def test_llm_qc_unavailable_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_llm(
        monkeypatch,
        script=[GOOD_OUTPUT, llm_module.LLMUnavailable("厂商模型暂时不可用")],
    )
    task = _task(QUEUED)
    run_generation_task(_FakeDB(_product()), None, task)  # type: ignore[arg-type]
    assert task.status == FAILED
    assert "LLM 事实性质检不可用" in task.last_error
    assert task.qc_llm_passed is False


def test_llm_qc_fail_skips_image_step(monkeypatch: pytest.MonkeyPatch) -> None:
    """双闸没过线不给文案配图（省一次厂商调用，也避免给废文案产图）。"""
    _patch_llm(monkeypatch, script=[GOOD_OUTPUT, QC_FAIL_NET_CONTENT])
    img_calls = _patch_imggen(monkeypatch, result=PNG_BYTES)
    task = _task(QUEUED, image=IMAGE_REQUESTED)
    run_generation_task(_FakeDB(_product()), _FakeStorage(), task)  # type: ignore[arg-type]
    assert task.status == FAILED
    assert img_calls == []  # 质检闸在配图步之前
    assert task.image_status == IMAGE_REQUESTED  # 请求标志保留（重试再要图）


# ---------- 配图步三态（第 98 刀，替身 + 内存存储） ----------


def test_image_step_no_key_skips_honestly(monkeypatch: pytest.MonkeyPatch) -> None:
    """无 IMGGEN key：配图步诚实跳过（不 fail 任务）——纯文案套件。
    第 115 刀起站内路径由 is_configured 入口闸拦截（替身补丁只是哨兵）。"""
    _patch_llm(monkeypatch, script=[GOOD_OUTPUT, QC_PASS])
    img_calls = _patch_imggen(monkeypatch, result=PNG_BYTES)  # 哨兵：不应被调用
    task = _task(QUEUED, template="station", image=IMAGE_REQUESTED)
    run_generation_task(_FakeDB(_product()), _FakeStorage(), task)  # type: ignore[arg-type]
    assert task.status == PENDING_QC  # 任务不 fail
    assert task.image_status == IMAGE_SKIPPED_NO_KEY
    assert task.image_object_key is None
    assert img_calls == []


def test_image_step_success_stages_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    """口播模板=文生图背景图（第 115 刀后唯一文生图路径）：替身成功暂存字节。"""
    _patch_llm(monkeypatch, script=[GOOD_OUTPUT, QC_PASS])
    monkeypatch.setattr(imggen_module, "is_configured", lambda: True)
    img_calls = _patch_imggen(monkeypatch, result=PNG_BYTES)
    storage = _FakeStorage()
    task = _task(QUEUED, template="short_video", image=IMAGE_REQUESTED)
    run_generation_task(_FakeDB(_product()), storage, task)  # type: ignore[arg-type]
    assert task.status == PENDING_QC
    assert task.image_status == IMAGE_PENDING
    # 暂存键：material/ 前缀 + 魔数派生 .png 后缀（不是资产键前缀）
    assert task.image_object_key is not None
    assert task.image_object_key.startswith("material/") and task.image_object_key.endswith(".png")
    assert storage.objects[task.image_object_key] == PNG_BYTES
    assert task.image_reference_asset_id is None  # 文生图背景无参考锚
    # 配图 prompt/尺寸由模板派生（口播=竖版背景 768x1024）
    assert len(img_calls) == 1
    assert img_calls[0]["size"] == "768x1024"
    assert "竖版" in img_calls[0]["prompt"] and "钛钢保温杯" in img_calls[0]["prompt"]


def test_image_step_xhs_renders_cover_card_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    """第 115 刀 W15b：小红书配图=文字卡流水线——不调 imggen（无 key 也出图）、
    CardSpec 带文案标题与商品事实。"""
    _patch_llm(monkeypatch, script=[GOOD_OUTPUT, QC_PASS])
    img_calls = _patch_imggen(monkeypatch, result=PNG_BYTES)  # 哨兵：不应被调用
    card_calls: list[Any] = []

    def fake_card(spec: Any) -> bytes:
        card_calls.append(spec)
        return PNG_BYTES

    monkeypatch.setattr(cover_card_module, "render_cover_card", fake_card)
    storage = _FakeStorage()
    task = _task(QUEUED, template="xhs", image=IMAGE_REQUESTED)
    run_generation_task(_FakeDB(_product()), storage, task)  # type: ignore[arg-type]
    assert task.status == PENDING_QC
    assert task.image_status == IMAGE_PENDING  # 无 key 照样出（流水线不是 AI）
    assert len(img_calls) == 0
    assert len(card_calls) == 1
    assert card_calls[0].title == json.loads(GOOD_OUTPUT)["title"]  # 标题进封面
    assert card_calls[0].product_name == "钛钢保温杯"
    assert card_calls[0].category == "器皿"
    assert task.image_reference_asset_id is None  # 文字卡不基于商品图


def test_image_step_station_without_real_image_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    """第 115 刀 W15a：站内模板有 key 但商品没有图片资产——诚实跳过
    skipped_no_image（不文生图冒充商品），编辑调用不发生。"""
    _patch_llm(monkeypatch, script=[GOOD_OUTPUT, QC_PASS])
    monkeypatch.setattr(imggen_module, "is_configured", lambda: True)
    edit_calls: list[Any] = []

    def fake_edit(instruction: str, image_bytes: bytes) -> bytes:
        edit_calls.append((instruction, image_bytes))
        return PNG_BYTES

    monkeypatch.setattr(imggen_module, "edit_image", fake_edit)
    task = _task(QUEUED, template="station", image=IMAGE_REQUESTED)
    run_generation_task(_FakeDB(_product()), _FakeStorage(), task)  # type: ignore[arg-type]
    assert task.status == PENDING_QC  # 跳过不 fail 任务
    assert task.image_status == IMAGE_SKIPPED_NO_IMAGE
    assert task.image_object_key is None
    assert task.image_reference_asset_id is None
    assert edit_calls == []  # 没有真图就不进编辑（探针实证：否则凭空画商品）


def test_image_step_failure_does_not_fail_task(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_llm(monkeypatch, script=[GOOD_OUTPUT, QC_PASS])
    monkeypatch.setattr(imggen_module, "is_configured", lambda: True)
    _patch_imggen(monkeypatch, error=imggen_module.ImggenUnavailable("文生图服务暂时不可用"))
    task = _task(QUEUED, template="short_video", image=IMAGE_REQUESTED)
    run_generation_task(_FakeDB(_product()), _FakeStorage(), task)  # type: ignore[arg-type]
    assert task.status == PENDING_QC  # 配图失败不影响文案任务
    assert task.image_status == IMAGE_FAILED
    assert task.image_object_key is None


def test_image_step_not_requested_never_called(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_llm(monkeypatch, script=[GOOD_OUTPUT, QC_PASS])
    img_calls = _patch_imggen(monkeypatch, result=PNG_BYTES)
    task = _task(QUEUED, image=IMAGE_NONE)
    run_generation_task(_FakeDB(_product()), _FakeStorage(), task)  # type: ignore[arg-type]
    assert task.status == PENDING_QC
    assert task.image_status == IMAGE_NONE
    assert img_calls == []


def test_image_step_retry_replaces_stale_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    """重试重跑配图：旧暂存字节被清理（防孤儿堆积），键换新。"""
    _patch_llm(monkeypatch, script=[GOOD_OUTPUT, QC_PASS, GOOD_OUTPUT, QC_PASS])
    monkeypatch.setattr(imggen_module, "is_configured", lambda: True)
    _patch_imggen(monkeypatch, result=PNG_BYTES)
    storage = _FakeStorage()
    task = _task(QUEUED, template="short_video", image=IMAGE_REQUESTED)
    task.status = FAILED  # 直接从 failed 重试（retry 放行 failed）
    retry_task(_FakeDB(_product()), storage, task)  # type: ignore[arg-type]
    first_key = task.image_object_key
    assert first_key is not None
    # 第二轮：重打回再重试，旧键删除、新键落位
    reject_like_fail = _FakeDB(_product())
    task.status = FAILED
    retry_task(reject_like_fail, storage, task)  # type: ignore[arg-type]
    assert task.image_status == IMAGE_PENDING
    assert task.image_object_key is not None
    assert task.image_object_key != first_key
    assert first_key not in storage.objects  # 旧暂存键已清


# ---------- 既有失败/重试口径回归 ----------


def test_retry_reruns_failed_task(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_llm(monkeypatch, script=[GOOD_OUTPUT, QC_PASS])
    task = _task(FAILED)
    task.last_error = "生成不可用：上次故障"
    retry_task(_FakeDB(_product()), None, task)  # type: ignore[arg-type]
    assert task.status == PENDING_QC
    assert task.last_error is None  # 复位：旧故障原因清空
    assert "钛钢保温杯" in (task.content or "")


# ---------- 第 115 刀 W15/W16：编辑指令 / 标题清洗 / 模板升级 ----------


def test_build_edit_instruction_keeps_product_unchanged() -> None:
    """美化产品图的编辑指令：动词前置 + 保留项申明（探针调研口径）+ 禁文字。"""
    instruction = build_edit_instruction(_product())
    assert instruction.startswith("将背景替换为")  # 动词前置
    assert "保持商品的外观、颜色、材质、比例与所有细节完全不变" in instruction
    assert "不要改变商品本身" in instruction
    assert "光照方向与原图一致" in instruction
    assert "钛钢保温杯" in instruction
    assert "不要在画面中添加任何文字、水印或 logo" in instruction


def test_sanitize_title_strips_emoji_and_caps_length() -> None:
    assert sanitize_title("🍵钛钢保温杯也太会装了吧！") == "钛钢保温杯也太会装了吧！"
    assert sanitize_title("  多个   空格\n换行  ") == "多个 空格 换行"
    assert len(sanitize_title("字" * 40)) == 24  # 截断保形
    assert sanitize_title("") == ""


def test_xhs_gen_style_has_viral_structure() -> None:
    """W16：小红书模板升级为显式爆款结构（标题公式/SCQA/标签配比）。"""
    style = TEMPLATES["xhs"]["gen_style"]
    assert "标题" in style and "20 字" in style
    assert "痛点+方案" in style and "数字+结果" in style and "对比+反转" in style
    assert "场景共鸣" in style and "行动引导" in style
    assert "3-5 个" in style and "泛流量" in style and "长尾" in style
    assert "极限词" in style  # 广告法边界进生成纪律
