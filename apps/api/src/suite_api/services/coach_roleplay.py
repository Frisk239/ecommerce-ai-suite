"""AI 客户对练模式（第 121 刀 A）：多轮对话考核。

一场对练 = AI 扮演顾客（人设含性格/情绪）↔ 受训者多轮对话；结束时整段
transcript 打四维分（同单轮 rubric）+ 证据锚 + 整改建议。行业对练产品
（UMU/北森/Megaview）的标准形态；我们的差异化：评分参照接中台已发布
口径（evidence_anchors 真接检索索引，答错直接指到正确口径在哪份资产）。

人设随机（性格决定态度）；题源四通道（gap 真实疑难优先）。turns 追加
在 CoachRoleplay JSONB；LLM 失败=顾客回一句占位（对练继续，不杀会话），
finish 失败=score NULL + turns 尾 system 注（可看原因，不可重评 v1）。
"""

import asyncio
import random
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from suite_api.models import CoachRoleplay
from suite_api.services import llm
from suite_api.services.coaching import (
    ScoreParseError,
    evidence_anchors,
    find_question,
    parse_score_output,
)
from suite_api.services.machine_wash import redact
from suite_api.settings import get_settings

# AI 客户人设库：性格×行为小矩阵（人设决定态度，态度随销售表现变化）
PERSONAS: list[dict[str, str]] = [
    {"style": "急躁", "trait": "说话直接、没耐心，销售答不到点上就打断", "quirk": "喜欢连着追问"},
    {"style": "温和", "trait": "有礼貌但犹豫，需要销售主动引导", "quirk": "爱说「我再想想」"},
    {"style": "挑剔", "trait": "什么都质疑一遍，对价格和品质都挑剔", "quirk": "喜欢拿别家对比"},
    {"style": "果断", "trait": "知道自己要什么，讨厌绕弯子", "quirk": "「你就说能不能」"},
]

_MAX_TURNS = 40  # 对话轮上限（防挂机刷轮；到上限自动可 finish）


def _roleplay_system(persona: dict[str, str], question: str) -> str:
    return (
        f"你是电商店铺里的顾客，正在和店铺销售（受训者）对话。你的人设：{persona['style']}型——"
        f"{persona['trait']}，{persona['quirk']}。\n"
        f"你的开场问题（这次对话的核心诉求）：{question}\n"
        "规则：不主动推销、不无条件配合（销售答得好你才软下来）、不用 AI 腔；"
        "一次只说 1-2 句话（真实顾客不会打长段）；如果销售的回答解决了你的问题，"
        "自然收尾（说「好的谢谢」之类）。只输出你作为顾客说的话，不要旁白。"
    )


def _history_text(turns: list[dict[str, Any]]) -> str:
    return "\n".join(
        f"{'顾客' if t['role'] == 'customer' else '销售'}：{t['text']}" for t in turns
    )


def _roleplay_reply(session: CoachRoleplay) -> tuple[str | None, str | None]:
    """AI 顾客回话（同步 LLM ≤20s；人设+题面+对话史进 prompt）。"""
    system = _roleplay_system(session.persona, session.question_text)
    prompt = f"对话史：\n{_history_text(session.turns)}\n\n现在轮到你（顾客）说话。"
    try:
        raw = asyncio.run(llm.complete_chat(system, redact(prompt)))
        return raw.strip(), None
    except llm.LLMError as exc:
        return None, str(exc)


def roleplay_start(
    db: Session, storage, operator_name: str, question_key: Any
) -> CoachRoleplay:
    """开一场对练：选题（find_question 四源通吃）→ 随机人设 → AI 顾客开场。"""
    question = find_question(db, storage, question_key)
    persona = random.choice(PERSONAS)
    session = CoachRoleplay(
        operator_name=operator_name,
        question_key=question["key"],
        question_text=redact(question["question"]),
        standard_answer=(
            redact(question["standard_answer"])
            if question["standard_answer"] is not None
            else None
        ),
        persona=persona,
        turns=[],
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    reply, err = _roleplay_reply(session)
    if reply is None:
        db.delete(session)
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=f"对练开场失败：{err}"
        )
    session.turns.append({"role": "customer", "text": reply})
    db.commit()
    db.refresh(session)
    return session


def roleplay_turn(db: Session, roleplay_id: int, trainee_text: str) -> CoachRoleplay:
    """受训者一句话 → AI 顾客回一句（追加两轮，保持 active）。"""
    session = db.get(CoachRoleplay, roleplay_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="对练会话不存在")
    if session.status != "active":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="对练已结束（评分后重开新会话）"
        )
    if len([t for t in session.turns if t["role"] == "trainee"]) >= _MAX_TURNS:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"已达对话轮上限（{_MAX_TURNS}），请结束评分",
        )
    text = redact(trainee_text.strip())
    if not text:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="空消息不能发送"
        )
    session.turns.append({"role": "trainee", "text": text})
    db.commit()  # 先落受训者轮（LLM 等待不持事务）
    reply, _err = _roleplay_reply(session)
    if reply is None:
        # 顾客回话失败：占位一句（对练继续，不杀会话——单点失败不杀整场的同族纪律）
        session.turns.append(
            {"role": "customer", "text": "（顾客这边网络卡了一下，你再说说看？）"}
        )
    else:
        session.turns.append({"role": "customer", "text": reply})
    db.commit()
    db.refresh(session)
    return session


_FINISH_SYSTEM = (
    "你是电商客服销售考核的评分官。以下是一场完整的销售-顾客多轮对练记录，"
    "对受训销售（「销售」侧）的整体表现按四维 rubric 打分。\n"
    "口径准确（0-30）：不编造承诺、不用极限词、不超范围——合规红线在这维扣分。\n"
    "异议处理（0-25）：顾客质疑/拒绝/砍价时的化解能力——共情接住情绪、给出理由"
    "或替代方案，不死板不硬顶。\n"
    "证据贴合（0-25）：回答扣住顾客问题与事实口径，不跑题不空泛。\n"
    "服务语气（0-20）：礼貌专业、有成交推进但不压迫。\n"
    "评分参照（可能为空）：{standard}\n"
    '只输出一个 JSON 对象：{"accurate": 0-30, "objection": 0-25, "evidence": 0-25, '
    '"tone": 0-20, "comment": "一句话总评", "remediation": "一条最该改进的建议"}'
)


def roleplay_finish(db: Session, roleplay_id: int) -> CoachRoleplay:
    """结束对练：整段 transcript 打四维分 + 证据锚 + 整改建议。"""
    session = db.get(CoachRoleplay, roleplay_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="对练会话不存在")
    if session.status != "active":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="对练已结束")
    trainee_turns = [t for t in session.turns if t["role"] == "trainee"]
    if not trainee_turns:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="还没有说过话，无对练可评"
        )
    session.status = "finished"
    db.commit()  # 终态先落（LLM 等待不持事务）
    system = _FINISH_SYSTEM.replace(
        "{standard}",
        redact(session.standard_answer) if session.standard_answer else "（无——真实疑难题）",
    )
    transcript = redact(_history_text(session.turns))
    try:
        raw = asyncio.run(llm.complete_chat(system, transcript))
        score = parse_score_output(raw)
        remediation = ""
        if isinstance(score.get("comment"), str):
            pass  # comment 在 parse 已收
        # remediation 从原始 JSON 补取（parse_score_output 只认四维+comment）
        import json

        from suite_api.services.machine_wash import strip_code_fence

        try:
            data = json.loads(strip_code_fence(raw))
            remediation = str(data.get("remediation", "")).strip()
        except (ValueError, TypeError, AttributeError):
            remediation = ""
        score["remediation"] = remediation
    except (llm.LLMError, ScoreParseError) as exc:
        # 失败态：score 留 NULL + turns 尾记 system 注（前端显示「未评分 + 原因」）
        session.score = None
        session.model_name = None
        session.turns.append({"role": "system", "text": f"评分未完成：{exc}"})
        db.commit()
        db.refresh(session)
        return session
    anchors = evidence_anchors(db, session.question_text)
    if anchors:
        score["anchors"] = anchors
    session.score = score
    session.model_name = get_settings().llm_model
    db.commit()
    db.refresh(session)
    return session


def list_roleplays(db: Session, limit: int = 20) -> list[CoachRoleplay]:
    return list(
        db.query(CoachRoleplay).order_by(CoachRoleplay.id.desc()).limit(limit).all()
    )
