"""素材中心集成测试（真 PG；complete_chat/imggen 替身不发外网；第 17 刀/ADR 0038；
第 98 刀内容套件/ADR 0055）。

契约：
- 建任务同步就地执行：返回即稳定态 pending_qc（生成+双闸质检都过）；
- approve -> registered + 登记出 kind=material / source_kind=material_generated
  资产（机洗按所挂商品规格字段跑正则，登记内已推进待人洗）-> 人洗确认 ->
  发布 -> 顾客问「保温杯有什么卖点」命中素材切块并引用；
- 打回 -> failed（人工打回）-> retry 新一次生成 -> 再 approve 登记；
- 空 key（无替身）-> failed「生成不可用」不降级：无任何素材资产；
- 非法转移 409（registered 再 retry / failed 直接 approve）；
- 打码步（ADR 0038 P1#4）：转写含手机号 -> QA 抽取 prompt 输入已打码、
  落库 qa_pairs 值不留裸号（断言见文件末，与回流链路同路）。

第 98 刀新增契约（ADR 0055）：
- 三模板：template 入参落库、生成 system prompt 按模板派生、坏值 422；
- LLM 事实性质检二道闸：构造矛盾文案（净含量 990ml vs 规格 480ml）被拦
  （failed 不进待抽检、文案保留、qc_llm_passed=False）；
- 配图：with_image 无 key=诚实跳过（skipped_no_key，任务照常）；替身成功=
  暂存可预览（GET tasks/{id}/image）+ 抽检通过双资产登记（material+image，
  image 的图片描述预填文案首句、VLM 无 key 时由预填兜底）+ 暂存键清理；
- imggen status 端点：configured=false（conftest 强制空 key）。
"""

import io
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sse_helpers import parse_sse_events

from suite_api.services import cjk_font
from suite_api.services import imggen as imggen_module
from suite_api.services import llm as llm_module

ApiFixture = tuple[TestClient, Path]

GOOD_TITLE = "钛钢保温杯：一杯守住温度"
GOOD_CONTENT = (
    "卖点一：钛钢保温杯双层真空，持久保温12小时。\n"
    "卖点二：钛钢保温杯轻量杯身，通勤车载两相宜。\n"
    "材质：钛钢\n"
    "净含量：480ml"
)

QC_PASS = '{"passed": true, "issues": []}'
QC_FAIL_CONTRADICTION = '{"passed": false, "issues": ["正文称净含量 990ml，与规格 480ml 矛盾"]}'

# 最小合法 PNG 字节（魔数即可：暂存键后缀/预览端点 mime/登记嗅探都只看魔数）
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def _login(client: TestClient) -> None:
    assert (
        client.post(
            "/api/auth/login", json={"username": "operator", "password": "operator123"}
        ).status_code
        == 200
    )


def _material_json(title: str = GOOD_TITLE, content: str = GOOD_CONTENT) -> str:
    return json.dumps({"title": title, "content": content}, ensure_ascii=False)


def _cup_id(client: TestClient) -> int:
    """按名取种子商品（id 分配随种子顺序，不硬编码：净含量+材质 required 的保温杯）。"""
    products = client.get("/api/products").json()
    return next(p["id"] for p in products if p["name"] == "钛钢保温杯")


def _patch_complete_chat(
    monkeypatch: pytest.MonkeyPatch,
    script: list[Any] | None = None,
    result: str | None = None,
    error: Exception | None = None,
) -> list[dict[str, str]]:
    """替换 llm.complete_chat；返回 prompt 捕获记录（同回流测试先例）。

    第 98 刀起一次任务有两处 LLM 等待点（生成 + 事实性质检）：``script``
    按调用序分派（字符串应答或 Exception 实例抛出）；``result`` 单值便捷形态。
    耗尽再被调即断言失败（意外调用哨兵）。"""
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


def _ask(client: TestClient, session_id: int, question: str) -> list[tuple[str, dict]]:
    with client.stream(
        "POST", f"/api/service/sessions/{session_id}/messages", json={"content": question}
    ) as resp:
        assert resp.status_code == 200
        raw = "".join(resp.iter_text())
    return parse_sse_events(raw)


# ---------- 全闭环：生成 -> 抽检 -> 登记 -> 人洗发布 -> 检索引用 ----------


def test_full_loop_generate_approve_publish_and_retrieval(
    api: ApiFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = api
    _login(client)
    cup_id = _cup_id(client)
    calls = _patch_complete_chat(monkeypatch, script=[_material_json(), QC_PASS])

    created = client.post("/api/material/tasks", json={"product_id": cup_id})
    assert created.status_code == 201
    task = created.json()
    task_id = task["id"]
    assert task["status"] == "pending_qc"
    assert task["title"] == GOOD_TITLE
    assert task["product_name"] == "钛钢保温杯"
    assert task["asset_id"] is None
    assert task["last_error"] is None
    # 第 98 刀默认形态：站内模板、无配图请求、双闸都过
    assert task["template"] == "station"
    assert task["template_name"] == "站内投放文案"
    assert task["qc_llm_passed"] is True
    assert task["image_status"] == "none"
    assert task["image_asset_id"] is None
    # 两次 LLM 调用：生成（模板 system prompt + 商品事实面）+ 事实性质检
    assert len(calls) == 2
    assert "钛钢保温杯" in calls[0]["user"]  # 商品名+规格事实进 prompt
    assert "站内投放" not in calls[0]["system"] or "字段：值" in calls[0]["system"]
    assert "480ml" in calls[1]["user"]  # 质检 prompt 带规格事实
    assert GOOD_TITLE in calls[1]["user"]  # 质检 prompt 带待审文案

    # 列表与详情视图一致
    listed = client.get("/api/material/tasks").json()
    assert [t["id"] for t in listed][0] == task_id  # id 倒序
    assert client.get(f"/api/material/tasks/{task_id}").json() == task

    approved = client.post(f"/api/material/tasks/{task_id}/approve")
    assert approved.status_code == 200
    registered = approved.json()
    assert registered["status"] == "registered"
    asset_id = registered["asset_id"]
    assert asset_id is not None
    assert registered["image_asset_id"] is None  # 未请求配图=单资产回执

    # 登记出的素材资产：kind=material、来源=素材生成、挂商品、机洗已弃权/推进
    asset = client.get(f"/api/assets/{asset_id}").json()
    assert asset["kind"] == "material"
    assert asset["source_kind"] == "material_generated"
    assert asset["status"] == "pending_review"
    assert asset["product"]["id"] == cup_id
    extracted = asset["versions"][0]["extracted_fields"]
    assert extracted["材质"] == {"value": "钛钢", "source": "machine"}
    assert extracted["净含量"] == {"value": "480ml", "source": "machine"}

    # 必填闸门仅种类=文档且挂商品（CONTEXT「必填字段」词条）：素材没有规格
    # 必填——机洗抽到值未确认也直接可发布（第 17 刀修复的潜伏偏离，对照
    # 文档挂商品未确认仍 422 的既有测试 test_publish_gate）
    assert client.post(f"/api/assets/{asset_id}/publish").status_code == 200

    # 顾客问卖点 -> 命中素材正文切块并引用 v1
    sid = client.post("/api/service/sessions").json()["id"]
    events = _ask(client, sid, "保温杯有什么卖点")
    complete = events[-1][1]
    assert complete["kind"] == "answer"
    assert {"asset_id": asset_id, "version_no": 1} in complete["citations"]
    answer = "".join(d["text"] for e, d in events if e == "delta")
    assert "钛钢保温杯" in answer

    # registered 是终态：再重试/打回都 409
    assert client.post(f"/api/material/tasks/{task_id}/retry").status_code == 409
    assert client.post(f"/api/material/tasks/{task_id}/reject").status_code == 409


# ---------- 打回 -> 失败 -> 重试 -> 再抽检通过 ----------


def test_reject_retry_then_approve(api: ApiFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = api
    _login(client)
    cup_id = _cup_id(client)
    _patch_complete_chat(monkeypatch, script=[_material_json(), QC_PASS])
    task_id = client.post("/api/material/tasks", json={"product_id": cup_id}).json()["id"]

    rejected = client.post(f"/api/material/tasks/{task_id}/reject").json()
    assert rejected["status"] == "failed"
    assert rejected["last_error"] == "人工打回"
    assert rejected["asset_id"] is None
    # failed 不能直接抽检通过（生成侧与抽检侧闸门互斥）
    assert client.post(f"/api/material/tasks/{task_id}/approve").status_code == 409

    _patch_complete_chat(monkeypatch, script=[_material_json(), QC_PASS])
    retried = client.post(f"/api/material/tasks/{task_id}/retry")
    assert retried.status_code == 200
    body = retried.json()
    assert body["status"] == "pending_qc"
    assert body["last_error"] is None  # 复位：打回原因清空

    again = client.post(f"/api/material/tasks/{task_id}/approve").json()
    assert again["status"] == "registered"
    assert again["asset_id"] is not None


# ---------- 列表批取（debt-2 N+1 收口） ----------


def test_list_tasks_batches_product_names(api: ApiFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    """N+1 钉测：任务列表一次 IN 查询批取商品名——请求期间对 products 的
    SELECT 恰为 1（旧逐行 db.get 在多商品任务下为 N 次）；行为等价
    （每行商品名与 /api/products 视图一致）。"""
    client, _ = api
    _login(client)
    _patch_complete_chat(monkeypatch, script=[_material_json(), QC_PASS])
    products = client.get("/api/products").json()
    assert len(products) >= 2
    for p in products[:2]:  # 跨两个商品建任务，批取才有判别力
        assert client.post("/api/material/tasks", json={"product_id": p["id"]}).status_code == 201

    from sqlalchemy import event

    session = client.app.state.session_factory()
    engine = session.get_bind()
    product_selects: list[str] = []

    def _listen(
        conn: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        del conn, cursor, parameters, context, executemany
        if "FROM products" in statement:
            product_selects.append(statement)

    event.listen(engine, "before_cursor_execute", _listen)
    try:
        listed = client.get("/api/material/tasks").json()
    finally:
        event.remove(engine, "before_cursor_execute", _listen)
        session.close()

    assert len({t["product_id"] for t in listed}) >= 2
    assert len(product_selects) == 1  # 批取：全列表只一条 products 查询
    name_by_id = {p["id"]: p["name"] for p in products}
    assert all(t["product_name"] == name_by_id[t["product_id"]] for t in listed)


# ---------- 空 key：failed 不降级、不进中台 ----------


def test_no_llm_key_fails_without_fallback(api: ApiFixture) -> None:
    client, _ = api
    _login(client)
    # 前面用例登记的素材资产数（本用例只验「失败不新增」，不验库为空）
    material_before = len([a for a in client.get("/api/assets").json() if a["kind"] == "material"])
    # 不 patch：conftest 空凭证进程里 complete_chat 抛 LLMNotConfigured
    task = client.post("/api/material/tasks", json={"product_id": _cup_id(client)}).json()
    assert task["status"] == "failed"
    assert "生成不可用" in task["last_error"]
    assert task["content"] is None
    assert task["asset_id"] is None
    # 失败不进中台（0029）：素材通道名下一个字节都没多
    material_after = len([a for a in client.get("/api/assets").json() if a["kind"] == "material"])
    assert material_after == material_before


# ---------- 入参与闸门 404/422/401 ----------


def test_gate_and_error_contract(api: ApiFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = api
    _login(client)
    assert client.post("/api/material/tasks", json={"product_id": 9999}).status_code == 404
    assert client.post("/api/material/tasks", json={}).status_code == 422
    assert client.get("/api/material/tasks/999999").status_code == 404
    assert client.post("/api/material/tasks/999999/approve").status_code == 404
    # 坏模板 422（第 98 刀：模板键在路由层校验）
    assert (
        client.post(
            "/api/material/tasks", json={"product_id": _cup_id(client), "template": "weibo"}
        ).status_code
        == 422
    )

    calls = _patch_complete_chat(
        monkeypatch,
        result=_material_json(content="卖点：这款杯子很好用。"),  # 缺商品名
    )
    bad = client.post("/api/material/tasks", json={"product_id": _cup_id(client)}).json()
    assert bad["status"] == "failed"
    assert "商品名" in bad["last_error"]
    assert bad["qc_llm_passed"] is None  # 规则闸先挡：LLM 闸未跑到
    assert len(calls) == 1  # 哨兵：规则不过不再调 LLM 质检
    assert client.post(f"/api/material/tasks/{bad['id']}/approve").status_code == 409

    # 未登录 401（素材是操作者动作；含 imggen/status 与配图预览）
    client.cookies.clear()
    assert client.get("/api/material/tasks").status_code == 401
    assert client.post("/api/material/tasks", json={"product_id": 1}).status_code == 401
    assert client.get("/api/material/imggen/status").status_code == 401
    assert client.get("/api/material/tasks/1/image").status_code == 401
    _login(client)


# ---------- 打码步集成（ADR 0038 P1#4，回流 QA 链路） ----------


def test_redact_in_reflow_qa_pipeline(api: ApiFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = api
    _login(client)
    calls = _patch_complete_chat(
        monkeypatch,
        result=(
            '[{"q": "上门安装要留电话吗",'
            ' "a": "留 13812345678 或邮箱 zhangsan@example.com，师傅回电"}]'
        ),
    )
    sid = client.post("/api/service/sessions").json()["id"]
    _ask(client, sid, "上门安装要留电话 13812345678 吗")

    register = client.post(f"/api/service/sessions/{sid}/register")
    assert register.status_code == 201
    asset = register.json()
    assert asset["status"] == "pending_review"

    # 接入点 a：转写送厂商前已打码——prompt 里无裸号
    assert len(calls) == 1
    assert "13812345678" not in calls[0]["user"]
    assert "1********78" in calls[0]["user"]

    # 接入点 b：模型把裸号带进答案也留不住——落库 qa_pairs 值已打码
    pairs = asset["versions"][0]["extracted_fields"]["qa_pairs"]["value"]
    flat = "".join(f"{p['q']}{p['a']}" for p in pairs)
    assert "13812345678" not in flat and "zhangsan@example.com" not in flat
    assert "1********78" in flat and "****@example.com" in flat

    # 版本字节不动（ADR 0038：打码只在抽取侧，对象键锁原字节）——转写原文仍含裸号
    version_text = client.get(f"/api/assets/{asset['id']}/versions/1/text")
    assert version_text.status_code == 200
    assert "13812345678" in version_text.text


# ---------- 第 98 刀：三模板 + 配图跳过（无 key 形态） ----------


def test_xhs_template_with_image_renders_cover_card_without_key(
    api: ApiFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """第 115 刀 W15b：小红书配图=封面文字卡**流水线**——不调 AI、不需要
    IMGGEN key、不需要商品真图：无 key 环境照样出 pending 配图（可预览），
    approve 双资产登记。文生图替身一次都不该被调用。"""
    font = cjk_font.find_cjk_font()
    if font is None:  # 容器/CI 已装 fonts-noto-cjk；裸环境跳过渲染用例
        pytest.skip("封面文字卡渲染需要 CJK 字体（fonts-noto-cjk / 系统字体）")
    client, _ = api
    _login(client)
    _patch_complete_chat(monkeypatch, script=[_material_json(), QC_PASS])
    img_calls = _patch_imggen(monkeypatch, result=PNG_BYTES)  # 哨兵：不应被调用

    task = client.post(
        "/api/material/tasks",
        json={"product_id": _cup_id(client), "template": "xhs", "with_image": True},
    ).json()
    assert task["status"] == "pending_qc"
    assert task["template"] == "xhs"
    assert task["image_status"] == "pending"  # 流水线渲染成功，与 key 无关
    assert task["image_reference_asset_id"] is None  # 文字卡不基于商品图
    assert len(img_calls) == 0  # 无 AI 调用（流水线的本质）

    preview = client.get(f"/api/material/tasks/{task['id']}/image")
    assert preview.status_code == 200
    assert preview.headers["content-type"] == "image/png"
    with Image.open(io.BytesIO(preview.content)) as card:
        assert card.size == (1080, 1440)  # 3:4 竖版封面

    approved = client.post(f"/api/material/tasks/{task['id']}/approve").json()
    assert approved["status"] == "registered"
    assert approved["asset_id"] is not None
    assert approved["image_asset_id"] is not None  # 文字卡也是配图资产（双资产）


def test_station_image_without_key_skips_honestly(
    api: ApiFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """第 115 刀 W15a：站内配图=美化产品图（编辑路径）——无 IMGGEN key 诚实
    跳过（skipped_no_key，任务照常待抽检），文生图/编辑都不被调用。"""
    client, _ = api
    _login(client)
    _patch_complete_chat(monkeypatch, script=[_material_json(), QC_PASS])
    img_calls = _patch_imggen(monkeypatch, result=PNG_BYTES)  # 哨兵：不应被调用

    task = client.post(
        "/api/material/tasks",
        json={"product_id": _cup_id(client), "template": "station", "with_image": True},
    ).json()
    assert task["status"] == "pending_qc"
    assert task["image_status"] == "skipped_no_key"  # conftest 强制空 key
    assert task["image_reference_asset_id"] is None
    assert len(img_calls) == 0


def test_station_beautify_pipeline_reference_skip_and_retry(
    api: ApiFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """第 115 刀 W15a 全链：站内模板美化产品图——

    1. 有 key 但商品**没有图片资产** → skipped_no_image（诚实跳过，
       不文生图冒充）；
    2. 给商品登记一张真图（productId 表单）→ **补配图端点**只重跑配图步：
       编辑指令带保留项、参考图字节来自真图版本、image_reference_asset_id
       落锚、配图可预览（不动已过闸文案）；
    3. 闸门：已 pending 的配图再补 409。
    """
    client, _ = api
    _login(client)
    monkeypatch.setattr(imggen_module, "is_configured", lambda: True)
    edit_calls: list[dict[str, object]] = []

    def fake_edit(instruction: str, image_bytes: bytes) -> bytes:
        edit_calls.append({"instruction": instruction, "bytes": image_bytes})
        return PNG_BYTES

    monkeypatch.setattr(imggen_module, "edit_image", fake_edit)

    # 新建商品做「无图起点」——共享库里保温杯已被前序用例的 approve 挂上
    # 文字卡/配图资产（pick_reference_image 会命中），本用例要钉的是「无图跳过」
    fresh = client.post(
        "/api/products", json={"name": "美化管线测试杯", "category": "器皿"}
    )
    assert fresh.status_code in (200, 201), fresh.text
    cup = int(fresh.json()["id"])

    # 替身文案按新商品名生成（规则闸要求正文含商品名）；四次调用=两次建任务
    gen = _material_json(
        title="美化管线测试杯：一杯守住温度",
        content=GOOD_CONTENT.replace("钛钢保温杯", "美化管线测试杯"),
    )
    _patch_complete_chat(monkeypatch, script=[gen, QC_PASS, gen, QC_PASS])

    task = client.post(
        "/api/material/tasks",
        json={"product_id": cup, "template": "station", "with_image": True},
    ).json()
    assert task["status"] == "pending_qc"
    assert task["image_status"] == "skipped_no_image"  # 新商品无图片资产
    assert edit_calls == []

    # 补前置条件：给商品登记一张真图（登记抽屉同款 productId 表单字段）
    registered = client.post(
        "/api/assets/register",
        files={"file": ("cup.png", PNG_BYTES, "image/png")},
        data={"title": "美化管线测试杯实拍图", "productId": str(cup)},
    )
    assert registered.status_code == 201, registered.text
    reference_id = int(registered.json()["id"])

    # 补配图：只重跑配图步（文案不动），编辑路径吃到真图字节
    retried = client.post(f"/api/material/tasks/{task['id']}/retry-image").json()
    assert retried["image_status"] == "pending"
    assert retried["title"] == task["title"]  # 文案原样（补配图不重新生成）
    assert retried["image_reference_asset_id"] == reference_id
    assert len(edit_calls) == 1
    assert "保持商品的外观、颜色、材质、比例与所有细节完全不变" in edit_calls[0]["instruction"]
    assert "美化管线测试杯" in edit_calls[0]["instruction"]
    assert edit_calls[0]["bytes"] == PNG_BYTES  # 参考图字节 = 真图版本字节

    preview = client.get(f"/api/material/tasks/{task['id']}/image")
    assert preview.status_code == 200 and preview.content == PNG_BYTES

    # 闸门：配图已 pending 再补 → 409；未请求配图的任务补 → 409
    assert client.post(f"/api/material/tasks/{task['id']}/retry-image").status_code == 409
    plain = client.post("/api/material/tasks", json={"product_id": cup}).json()
    assert plain["image_status"] == "none"
    assert client.post(f"/api/material/tasks/{plain['id']}/retry-image").status_code == 409

    # 收尾 approve：双资产登记（含美化图）并清暂存——共享存储目录不留
    # material/ 残留（下游用例对暂存清洁度有全局断言）
    approved = client.post(f"/api/material/tasks/{task['id']}/approve").json()
    assert approved["status"] == "registered"
    assert approved["image_asset_id"] is not None
    assert approved["image_reference_asset_id"] == reference_id  # 血缘锚随回执可见


def test_imggen_status_endpoint(api: ApiFixture) -> None:
    client, _ = api
    _login(client)
    body = client.get("/api/material/imggen/status").json()
    assert body == {"configured": False}  # conftest 强制空 IMGGEN_API_KEY


# ---------- 第 98 刀：LLM 事实性质检二道闸 ----------


def test_llm_qc_gate_blocks_contradictory_copy(
    api: ApiFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """构造矛盾文案（净含量 990ml vs 规格 480ml）被二道闸实测拦截：不进
    待抽检、failed 带具体原因、文案保留预览、可重试。"""
    client, _ = api
    _login(client)
    contradictory = _material_json(content="卖点：钛钢保温杯超大容量。\n材质：钛钢\n净含量：990ml")
    _patch_complete_chat(monkeypatch, script=[contradictory, QC_FAIL_CONTRADICTION])

    task = client.post("/api/material/tasks", json={"product_id": _cup_id(client)}).json()
    assert task["status"] == "failed"  # 不进待抽检（roadmap：失败不进待抽检）
    assert "LLM 事实性质检不过线" in task["last_error"]
    assert "990ml" in task["last_error"]
    assert task["qc_llm_passed"] is False
    assert task["title"] == GOOD_TITLE  # 文案保留预览面
    assert "990ml" in task["content"]
    assert task["asset_id"] is None
    assert client.post(f"/api/material/tasks/{task['id']}/approve").status_code == 409

    # 重试（换正常质检判定）-> 过线进待抽检
    _patch_complete_chat(monkeypatch, script=[_material_json(), QC_PASS])
    retried = client.post(f"/api/material/tasks/{task['id']}/retry").json()
    assert retried["status"] == "pending_qc"
    assert retried["qc_llm_passed"] is True


def test_llm_qc_bad_output_fails_closed(api: ApiFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = api
    _login(client)
    _patch_complete_chat(monkeypatch, script=[_material_json(), "我觉得没问题。"])
    task = client.post("/api/material/tasks", json={"product_id": _cup_id(client)}).json()
    assert task["status"] == "failed"
    assert "LLM 质检输出不可解析" in task["last_error"]
    assert task["qc_llm_passed"] is False


# ---------- 第 98 刀：配图全链（替身成功 -> 预览 -> 双资产登记） ----------


def test_image_full_path_staged_preview_and_double_asset(
    api: ApiFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = api
    _login(client)
    _patch_complete_chat(monkeypatch, script=[_material_json(), QC_PASS])
    # 第 115 刀起配图步有 is_configured 入口闸（conftest 强制空 key）——口播
    # 文生图路径的替身要配「有 key」才走得到 generate_image
    monkeypatch.setattr(imggen_module, "is_configured", lambda: True)
    img_calls = _patch_imggen(monkeypatch, result=PNG_BYTES)

    task = client.post(
        "/api/material/tasks",
        json={"product_id": _cup_id(client), "template": "short_video", "with_image": True},
    ).json()
    assert task["status"] == "pending_qc"
    assert task["image_status"] == "pending"
    # 配图 prompt/尺寸按模板派生（口播=竖版背景图 768x1024）
    assert len(img_calls) == 1
    assert img_calls[0]["size"] == "768x1024"
    assert "竖版" in img_calls[0]["prompt"] and "钛钢保温杯" in img_calls[0]["prompt"]

    # 抽检前预览：暂存字节端点（操作者面）直出 png
    preview = client.get(f"/api/material/tasks/{task['id']}/image")
    assert preview.status_code == 200
    assert preview.headers["content-type"] == "image/png"
    assert preview.content == PNG_BYTES
    # 未请求配图的任务没有可预览的配图（404 同文案）
    assert client.get("/api/material/tasks/999999/image").status_code == 404

    # 抽检通过：双资产登记（文案 material + 配图 image）
    approved = client.post(f"/api/material/tasks/{task['id']}/approve").json()
    assert approved["status"] == "registered"
    material_id = approved["asset_id"]
    image_id = approved["image_asset_id"]
    assert material_id is not None and image_id is not None
    assert material_id != image_id

    image_asset = client.get(f"/api/assets/{image_id}").json()
    assert image_asset["kind"] == "image"
    assert image_asset["source_kind"] == "material_generated"
    assert image_asset["status"] == "pending_review"  # 走 94a 待人洗治理
    assert image_asset["product"]["id"] == _cup_id(client)
    assert image_asset["title"] == "钛钢保温杯 · 短视频口播稿配图"
    # 图片描述：VLM 无 key（conftest 空 key）→ 文案首句预填兜底（extracted，
    # 待人洗可改）；键后缀按字节魔数=png
    extracted = image_asset["versions"][0]["extracted_fields"]
    assert extracted["图片描述"] == {
        "value": "卖点一：钛钢保温杯双层真空，持久保温12小时。",
        "source": "machine",
    }
    assert image_asset["versions"][0]["object_key"].endswith(".png")

    # 登记后暂存预览端点 404（预览去治理台资产详情），暂存目录无残留文件
    # （转正即清；空目录壳与资产键删除同款，不算残留）
    assert client.get(f"/api/material/tasks/{task['id']}/image").status_code == 404
    _, storage_root = api
    assert [p for p in storage_root.glob("material/**/*") if p.is_file()] == []
