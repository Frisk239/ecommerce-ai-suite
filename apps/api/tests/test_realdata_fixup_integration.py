"""realdata 脚本 fixup 的「直写库」边界测试（第 93 刀清审计 18 P2#4；真 PG）。

``publish_digital_specs.fixup_asset_sources`` 是**脚本直写库**（绕过 services 层）：
``POST /assets/register`` 的 source_kind 是服务端定值 ``upload``，而迁移 0026/0028
的形态回填只在已有数据上跑一次——按 README 复位顺序重新导入时那批资产会全显示
「上传」，数据集来源在产品面消失。这里钉住它的边界：**只动圈定行**（来源还是
upload 且标题形如「… 规格（Wikidata）」），别的来源/别的标题一行不动；且幂等
（重跑 rowcount=0）。

未设 SUITE_TEST_DATABASE_URL 时随 `api` 夹具 skip（与其它集成用例同口径）。
"""

import os
import sys
from pathlib import Path

import psycopg
import pytest

REALDATA_DIR = Path(__file__).resolve().parents[3] / "scripts" / "realdata"
SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "scripts"
sys.path.insert(0, str(REALDATA_DIR))
sys.path.insert(0, str(SCRIPTS_DIR))

import publish_digital_specs as pds  # noqa: E402
import transcribe_local as tl  # noqa: E402

_URL_ENV = "SUITE_TEST_DATABASE_URL"


def test_fixup_asset_sources_only_touches_uploaded_wikidata_spec_rows(api) -> None:
    client, _ = api
    del client  # 只为拿「真 PG 已就绪」的夹具（本用例走脚本函数直连库）
    url = os.environ[_URL_ENV]

    with psycopg.connect(url, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO assets (kind, status, source_kind, title) VALUES"
            " ('document', 'pending_review', 'upload', '测试耳机 规格（Wikidata）'),"
            " ('document', 'pending_review', 'upload', '普通上传文档'),"
            " ('document', 'pending_review', 'review_import', '评论里的 规格（Wikidata）')"
        )

        changed = pds.fixup_asset_sources(url)
        assert changed == 1  # 只有「upload + 规格（Wikidata）」那一行

        cur.execute(
            "SELECT source_kind FROM assets WHERE title = '测试耳机 规格（Wikidata）'"
        )
        assert cur.fetchone()[0] == "wikidata"
        cur.execute("SELECT source_kind FROM assets WHERE title = '普通上传文档'")
        assert cur.fetchone()[0] == "upload"  # 标题不匹配：不动
        cur.execute("SELECT source_kind FROM assets WHERE title = '评论里的 规格（Wikidata）'")
        assert cur.fetchone()[0] == "review_import"  # 来源已定值：不动

        # 幂等：第二次跑没有行落在判据里（不重复产生副作用）
        assert pds.fixup_asset_sources(url) == 0


def test_scripts_are_importable_without_funasr() -> None:
    """本地脚本顶层不 import funasr：没装可选依赖也能 `--help` 与跑纯函数。"""
    assert tl.ms_to_seconds(1500) == 1.5
    assert tl.segments_from_funasr(
        {
            "sentence_info": [
                {"text": "第一句。", "start": 200, "end": 1400},
                {"text": "第二句。", "start": 3000, "end": 4200},
                {"text": "", "start": 0, "end": 0},
            ]
        }
    ) == [
        {"start": 0.2, "end": 1.4, "text": "第一句。"},
        {"start": 3.0, "end": 4.2, "text": "第二句。"},
    ]
    # 没有句级结果（未启用 punc/sentence_timestamp）：退回「全文 + 字级时间戳」整段，
    # 判不出句边界就不判（不编造）
    assert tl.segments_from_funasr(
        {"text": "整段话", "timestamp": [[100, 300], [300, 900]]}
    ) == [{"start": 0.1, "end": 0.9, "text": "整段话"}]
    assert tl.segments_from_funasr({"text": "", "timestamp": []}) == []


def test_transcribe_local_missing_recording_is_actionable(tmp_path: Path) -> None:
    """本地脚本对不存在的录像给可行动返回码（2）+ 文案，不抛栈。"""
    url = os.environ.get(_URL_ENV)
    if not url:
        pytest.skip(f"需真 Postgres：设 {_URL_ENV}=postgresql://suite:suite@localhost:5433/suite_test")
    assert tl.run(url, 999999, objects_root=tmp_path, product_id=None) == 2
