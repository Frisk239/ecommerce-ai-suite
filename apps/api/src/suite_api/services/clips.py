"""直播切片拣选服务（第 18 刀，ADR 0014/0015/0039）：候选 → 拣选 → 登记视频资产。

候选不是资产（0014）：``clip_candidates`` 是切片模块自有的种子 mock 表，只有
时间码和转写；人才拣选，拣选时才经 register_asset 写独立字节、登记为
kind=video、来源=切片拣选的资产（0015：只登记资产，不暗插素材任务）。
登记字节 = 「[HH:MM:SS-HH:MM:SS] 转写」文本（ADR 0039——不是 mp4，对象键前缀
clips/；真视频切出/ASR 是部署刀的事）。

单向状态机：pending → registered，候选行记 ``registered_asset_id`` 回执锚
（同 MaterialTask.asset_id 先例）；已登记再拣选 409，不可撤销不可重切（Out）。

拒绝原子性（不是全批回滚）：先整批校验（任一 id 不存在 404 / 任一已登记
409）——校验在第一个字节落库之前，批量含已登记则整体 409、事务不落。
但逐候选 register_asset 内部各自 commit（P1#2 纪律：机洗不持事务；video
字段集为空、机洗纯本地，不触发这段 LLM 等待）——第 k 个候选写字节失败时
前 k-1 资产已落库、该候选仍 pending 可重拣（与「登记失败不挡字节」同形）。
"""

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from suite_api.models import Asset, ClipCandidate
from suite_api.services.registration import register_asset
from suite_platform.storage import ObjectStorage

# 候选两态（0039）：与 asset 三态、任务五态无关，不共用词表
PENDING = "pending"
REGISTERED = "registered"


def transcript_bytes(candidate: ClipCandidate) -> bytes:
    """登记字节 = 带时间码头的转写文本（0039）：``[start-end] 转写``。"""
    return (
        f"[{candidate.timecode_start}-{candidate.timecode_end}] {candidate.transcript}"
    ).encode()


def pick_candidates(db: Session, storage: ObjectStorage, ids: list[int]) -> list[Asset]:
    """批量拣选登记：pending 候选 → kind=video / source=clip_pick 资产。

    - 重复 id 去重保序（同一候选勾两次=登记一次）；
    - 任一不存在 -> 404；任一已登记 -> 409（批量含已登记整体拒绝，字节不落）；
    - 每候选一资产：title=转写截断 60 字、挂候选的商品、机洗空字段集
      （machine_wash_field_names 的 video 分支：弃权推进待人洗，0039）；
    - 候选置 registered + registered_asset_id 回执锚，末次 commit 收口。
    返回登记出的资产列表（含 id 供前端跳治理台）。
    """
    candidates: list[ClipCandidate] = []
    for candidate_id in dict.fromkeys(ids):
        candidate = db.get(ClipCandidate, candidate_id)
        if candidate is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="切片候选不存在")
        if candidate.status != PENDING:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"切片候选 {candidate_id} 已登记为资产，不可重复拣选",
            )
        candidates.append(candidate)

    assets: list[Asset] = []
    for candidate in candidates:
        asset = register_asset(
            db,
            storage,
            kind="video",
            title=candidate.transcript[:60],
            content_bytes=transcript_bytes(candidate),
            filename=None,
            product_id=candidate.product_id,
            source_kind="clip_pick",
        )
        candidate.status = REGISTERED
        candidate.registered_asset_id = asset.id
        assets.append(asset)
    db.commit()
    return assets
