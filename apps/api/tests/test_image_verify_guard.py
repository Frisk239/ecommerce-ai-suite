"""第 111 刀护栏：图片描述人洗时的 VLM 一致性复核（W7 防复发）。

四态（任务书要求，全用替身 VLM，不打真网）：
1. 交集通过（≥2 词）→ ``verify.passed=True``，人洗值照常落库；
2. 交集 0–1 词 → ``passed=False`` 警示，但 **PATCH 仍 200、值仍落库**——人仍是
   最终裁决者（护栏不阻止）；
3. key 空 → 跳过复核（``skipped="no_key"``），PATCH 照常（fail-open 到人洗兜底）；
4. 非图片资产（文档）→ 响应不带复核附注（``verify is None``，零 VLM 调用）。

另钉：env 开关关（IMAGE_VERIFY_ON_WASH=false）不调 VLM；VLM 抛错=skipped
（vlm_failed）而不是 500；附注里的 VLM 描述过 redact（出口必掩，0038）。

集成部分复用既有 ``api`` 夹具（真 PG，自建库；VLM key 在测试进程被强制空）。
"""

import base64
import os
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import suite_api.services.vlm as vlm_service
from suite_api.services import image_verify
from suite_api.services.image_verify import (
    IMAGE_VERIFY_MIN_OVERLAP,
    SKIP_DISABLED,
    SKIP_NO_KEY,
    SKIP_VLM_FAILED,
    WashVerify,
    keyword_overlap,
    keywords_of,
    verify_description,
)

ApiFixture = tuple[TestClient, Path]

_URL_ENV = "SUITE_TEST_DATABASE_URL"

# 1x1 真 PNG（魔数复验用；替身 VLM 不看像素）
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=="
)

# 与替身独立描述有两个以上共同关键词的人类描述（「显示器/可调节/支架」）
_DESCRIPTION = "显示器侧面带可调节支架，正面三边窄边框"
_DRAFT = "黑色窄边框显示器，配有可调节支架，屏幕关闭"
_UNRELATED = "女士手持咖啡胶囊，厨房背景，旁边有一盆绿植"


# ---------- 纯函数：交集口径与四态判定 ----------


def test_keyword_overlap_threshold_matches_108_audit() -> None:
    """交集口径与 108 审计同源（同一函数）：≥2 词=一致，0–1=疑似不符。"""
    assert IMAGE_VERIFY_MIN_OVERLAP == 2
    assert keyword_overlap(_DESCRIPTION, _DRAFT) >= IMAGE_VERIFY_MIN_OVERLAP
    assert keyword_overlap(_DESCRIPTION, _UNRELATED) < IMAGE_VERIFY_MIN_OVERLAP
    # 套话（画面/清晰/背景）不进关键词——A-505 实证：不剔会让两张无关图凑出交集
    boilerplate = keywords_of("画面清晰，背景干净，整体居中")
    assert {"画面", "清晰", "背景", "整体", "居中"} & boilerplate == set()


def test_verify_description_pass_and_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(image_verify, "enabled", lambda settings=None: True)
    monkeypatch.setattr(vlm_service, "is_configured", lambda: True)

    monkeypatch.setattr(vlm_service, "describe_image", lambda data: _DRAFT)
    ok = verify_description(_PNG, _DESCRIPTION)
    assert isinstance(ok, WashVerify)
    assert ok.passed is True and ok.overlap is not None
    assert ok.overlap >= IMAGE_VERIFY_MIN_OVERLAP and ok.skipped is None
    assert ok.vlm_summary == _DRAFT

    monkeypatch.setattr(vlm_service, "describe_image", lambda data: _UNRELATED)
    bad = verify_description(_PNG, _DESCRIPTION)
    assert bad.passed is False and bad.skipped is None
    assert isinstance(bad.overlap, int) and bad.overlap < IMAGE_VERIFY_MIN_OVERLAP


def test_verify_description_skips_without_key_or_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(image_verify, "enabled", lambda settings=None: True)

    def _boom(data: bytes) -> str:
        raise vlm_service.VLMUnavailable("看图服务暂时不可用")

    monkeypatch.setattr(vlm_service, "is_configured", lambda: False)
    monkeypatch.setattr(vlm_service, "describe_image", _boom)
    no_key = verify_description(_PNG, _DESCRIPTION)
    assert no_key.skipped == SKIP_NO_KEY
    assert no_key.passed is None and no_key.overlap is None  # 不写假结论

    monkeypatch.setattr(vlm_service, "is_configured", lambda: True)
    failed = verify_description(_PNG, _DESCRIPTION)
    assert failed.skipped == SKIP_VLM_FAILED and failed.passed is None


def test_verify_description_disabled_by_env_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    """开关关=连 is_configured 都不看（省调用）；显式 False 语义见 settings 测试。"""
    monkeypatch.setattr(image_verify, "enabled", lambda settings=None: False)

    def _must_not_call(data: bytes) -> str:
        raise AssertionError("开关关闭时不该调 VLM")

    monkeypatch.setattr(vlm_service, "is_configured", lambda: True)
    monkeypatch.setattr(vlm_service, "describe_image", _must_not_call)
    outcome = verify_description(_PNG, _DESCRIPTION)
    assert outcome.skipped == SKIP_DISABLED and outcome.passed is None


def test_verify_summary_is_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    """VLM 描述里的手机号/邮箱在附注里也掩掉（出口必掩，0038 同口径）。"""
    monkeypatch.setattr(image_verify, "enabled", lambda settings=None: True)
    monkeypatch.setattr(vlm_service, "is_configured", lambda: True)
    monkeypatch.setattr(
        vlm_service,
        "describe_image",
        lambda data: "显示器支架，标签上有 13812345678 与 alice@shop.com",
    )
    outcome = verify_description(_PNG, "显示器带可调节支架")
    assert outcome.vlm_summary is not None
    assert "13812345678" not in outcome.vlm_summary
    assert "alice@shop.com" not in outcome.vlm_summary
    assert "****@shop.com" in outcome.vlm_summary


def test_settings_env_switch_defaults_on_and_parses_false() -> None:
    """IMAGE_VERIFY_ON_WASH：默认 on；空串（compose 占位）=on；显式 false 才关。"""
    from suite_api.settings import Settings

    assert Settings().image_verify_on_wash is True
    assert Settings(image_verify_on_wash="").image_verify_on_wash is True
    assert Settings(image_verify_on_wash="false").image_verify_on_wash is False
    assert Settings(image_verify_on_wash="0").image_verify_on_wash is False


# ---------- 集成：PATCH 四态（真 PG；VLM 替身） ----------

_needs_db = pytest.mark.skipif(
    not os.environ.get(_URL_ENV), reason="需真 Postgres：设 SUITE_TEST_DATABASE_URL"
)


def _login(client: TestClient) -> None:
    assert (
        client.post(
            "/api/auth/login", json={"username": "operator", "password": "operator123"}
        ).status_code
        == 200
    )


def _upload_image(client: TestClient, title: str) -> dict[str, Any]:
    resp = client.post(
        "/api/assets/register",
        files={"file": ("frame.png", _PNG, "image/png")},
        data={"title": title},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _enable_verify(monkeypatch: pytest.MonkeyPatch, summary: str) -> None:
    """替身 VLM（配置态 + 固定独立描述）；开关按 settings 默认 on。"""
    monkeypatch.setattr(vlm_service, "is_configured", lambda: True)
    monkeypatch.setattr(vlm_service, "describe_image", lambda data: summary)
    monkeypatch.setattr(image_verify, "enabled", lambda settings=None: True)


def _product_id() -> int:
    """取一个挂商品用的 product id（文档资产才有合法规格字段集）。"""
    import psycopg

    with psycopg.connect(os.environ[_URL_ENV]) as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM products ORDER BY id LIMIT 1")
        rows = cur.fetchall()
    assert rows
    return int(rows[0][0])


@_needs_db
def test_patch_image_description_pass_state(
    api: ApiFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """态 1：交集 ≥2 词 → verify.passed=True，人洗值照常落库。"""
    client, _ = api
    _login(client)
    asset = _upload_image(client, "护栏通过态商品图")
    _enable_verify(monkeypatch, _DRAFT)

    patched = client.patch(
        f"/api/assets/{asset['id']}/versions/1/fields", json={"图片描述": _DESCRIPTION}
    )
    assert patched.status_code == 200, patched.text
    payload = patched.json()
    assert payload["verify"]["passed"] is True
    assert payload["verify"]["overlap"] >= IMAGE_VERIFY_MIN_OVERLAP
    assert payload["verify"]["skipped"] is None
    assert payload["verify"]["vlm_summary"] == _DRAFT
    assert payload["confirmed_fields"]["图片描述"] == {"value": _DESCRIPTION, "source": "human"}


@_needs_db
def test_patch_image_description_mismatch_warns_but_does_not_block(
    api: ApiFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """态 2：交集 0–1 词 → passed=False 警示；**PATCH 仍 200、值仍落库**（人裁决）。"""
    client, _ = api
    _login(client)
    asset = _upload_image(client, "护栏疑似不符商品图")
    _enable_verify(monkeypatch, _UNRELATED)

    patched = client.patch(
        f"/api/assets/{asset['id']}/versions/1/fields", json={"图片描述": _DESCRIPTION}
    )
    assert patched.status_code == 200, patched.text  # 不阻止
    payload = patched.json()
    assert payload["verify"]["passed"] is False
    assert payload["verify"]["overlap"] < IMAGE_VERIFY_MIN_OVERLAP
    assert payload["confirmed_fields"]["图片描述"]["value"] == _DESCRIPTION  # 人仍是裁决者
    # 照常可发布（护栏不是发布闸门）
    assert client.post(f"/api/assets/{asset['id']}/publish").status_code == 200


@_needs_db
def test_patch_image_description_skips_without_vlm_key(
    api: ApiFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """态 3：key 空 → 跳过复核（skipped=no_key），PATCH 照常 200（fail-open）。"""
    client, _ = api
    _login(client)
    asset = _upload_image(client, "护栏无 key 商品图")

    def _must_not_call(data: bytes) -> str:
        raise AssertionError("key 空时不该调 VLM")

    monkeypatch.setattr(vlm_service, "is_configured", lambda: False)
    monkeypatch.setattr(vlm_service, "describe_image", _must_not_call)
    monkeypatch.setattr(image_verify, "enabled", lambda settings=None: True)

    patched = client.patch(
        f"/api/assets/{asset['id']}/versions/1/fields", json={"图片描述": _DESCRIPTION}
    )
    assert patched.status_code == 200, patched.text
    payload = patched.json()
    assert payload["verify"] == {
        "passed": None,
        "overlap": None,
        "vlm_summary": None,
        "skipped": "no_key",
    }
    assert payload["confirmed_fields"]["图片描述"]["value"] == _DESCRIPTION


@_needs_db
def test_patch_image_description_guard_disabled_by_env(
    api: ApiFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """开关显式关：不调 VLM，附注 skipped=disabled，人洗照常。"""
    client, _ = api
    _login(client)
    asset = _upload_image(client, "护栏开关关商品图")

    def _must_not_call(data: bytes) -> str:
        raise AssertionError("护栏关闭时不该调 VLM")

    monkeypatch.setattr(vlm_service, "is_configured", lambda: True)
    monkeypatch.setattr(vlm_service, "describe_image", _must_not_call)
    monkeypatch.setattr(image_verify, "enabled", lambda settings=None: False)

    patched = client.patch(
        f"/api/assets/{asset['id']}/versions/1/fields", json={"图片描述": _DESCRIPTION}
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["verify"]["skipped"] == "disabled"


@_needs_db
def test_patch_non_image_asset_has_no_verify_note(
    api: ApiFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """态 4：文档资产 PATCH 规格字段 → 响应不带复核附注（零 VLM 调用）。"""
    client, _ = api
    _login(client)
    product_id = _product_id()
    resp = client.post(
        "/api/assets/register",
        files={"file": ("spec.txt", "净含量：550毫升。保质期：12个月。".encode(), "text/plain")},
        data={"productId": str(product_id), "title": "护栏非图片资产"},
    )
    assert resp.status_code == 201, resp.text
    asset_id = int(resp.json()["id"])
    _enable_verify(monkeypatch, _DRAFT)  # 配置了也不该被调用（非图片）

    patched = client.patch(f"/api/assets/{asset_id}/versions/1/fields", json={"净含量": "550毫升"})
    assert patched.status_code == 200, patched.text
    assert patched.json()["verify"] is None
    assert patched.json()["confirmed_fields"]["净含量"]["value"] == "550毫升"


@_needs_db
def test_verify_vlm_failure_is_skipped_not_500(
    api: ApiFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """看图失败 = 附注 skipped（vlm_failed），绝不把合法人洗变 500。"""
    client, _ = api
    _login(client)
    asset = _upload_image(client, "护栏看图失败商品图")

    def _boom(data: bytes) -> str:
        raise vlm_service.VLMUnavailable("看图服务暂时不可用")

    monkeypatch.setattr(vlm_service, "is_configured", lambda: True)
    monkeypatch.setattr(vlm_service, "describe_image", _boom)
    monkeypatch.setattr(image_verify, "enabled", lambda settings=None: True)

    patched = client.patch(
        f"/api/assets/{asset['id']}/versions/1/fields", json={"图片描述": _DESCRIPTION}
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["verify"]["skipped"] == SKIP_VLM_FAILED
    assert patched.json()["verify"]["skipped"] == "vlm_failed"
