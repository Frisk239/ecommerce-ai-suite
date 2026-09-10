"""直播切片拣选服务（第 18 刀，ADR 0014/0015/0039；第 46 刀真链路）。

候选不是资产（0014）：``clip_candidates`` 是切片模块自有的种子 mock 表，只有
时间码和转写；人才拣选，拣选时才经 register_asset 写独立字节、登记为
kind=video、来源=切片拣选的资产（0015：只登记资产，不暗插素材任务）。

字节来源按候选的源录像分派（第 46 刀，裁决 2/3）：
- 有 ``recording_id``（上传源录像时绑定）：用 ffmpeg 真切出 mp4 片段（``-c copy``
  秒级不重编码），登记字节=真片段、对象键 clips/<uuid>/<sha16>.mp4；同时预置
  ``transcript`` 字段承载正文（字节是二进制，检索切块改由字段供给——裁决 5）。
- 无（NULL）：退回既有时间码文本字节「[HH:MM:SS-HH:MM:SS] 转写」（ADR 0039，
  旧路径保留，既有测试/历史资产继续成立）。

单向状态机：pending → registered，候选行记 ``registered_asset_id`` 回执锚
（同 MaterialTask.asset_id 先例）；已登记再拣选 409，不可撤销不可重切（Out）。

拒绝原子性（不是全批回滚）：先整批校验（任一 id 不存在 404 / 任一已登记
409）——校验在第一个字节落库之前，批量含已登记则整体 409、事务不落。
但逐候选各自 commit（register_asset 内部登记 commit + 循环末收口回执锚；
P1#2 纪律：机洗不持事务）——第 k 个候选写字节/真切失败时，前 k-1 候选已
registered 且资产已落库、该候选仍 pending 可重拣（第 46 刀裁决 7：切失败 422 +
保持 pending，不落半个资产；与「登记失败不挡字节」同形）。
"""

import hashlib
import logging
import subprocess
import tempfile
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from suite_api.models import Asset, ClipCandidate, ClipRecording
from suite_api.observability import record_clip_cut
from suite_api.services.registration import register_asset
from suite_platform.storage import ObjectStorage

# 候选两态（0039）：与 asset 三态、任务五态无关，不共用词表
logger = logging.getLogger(__name__)

PENDING = "pending"
REGISTERED = "registered"

# ffmpeg 超时（秒）：本地 copy 切段是秒级；留 120s 上限防畸形输入挂死请求线程
_FFMPEG_TIMEOUT = 120


class ClipCutError(Exception):
    """真切片失败（时间码非法 / ffmpeg 非 0 退出 / 无输出）：调用方转 422，
    候选保持 pending 可重拣（第 46 刀裁决 7）。"""


def transcript_bytes(candidate: ClipCandidate) -> bytes:
    """旧路径登记字节 = 带时间码头的转写文本（0039）：``[start-end] 转写``。"""
    return (
        f"[{candidate.timecode_start}-{candidate.timecode_end}] {candidate.transcript}"
    ).encode()


def _parse_hms(value: str) -> int:
    """``HH:MM:SS`` -> 秒。非法形状/越界抛 ClipCutError（路由层转 422）。"""
    parts = value.split(":")
    if len(parts) != 3:
        raise ClipCutError(f"时间码必须是 HH:MM:SS，收到: {value!r}")
    try:
        hours, minutes, seconds = (int(part) for part in parts)
    except ValueError as exc:
        raise ClipCutError(f"时间码必须是数字 HH:MM:SS，收到: {value!r}") from exc
    if hours < 0 or not 0 <= minutes < 60 or not 0 <= seconds < 60:
        raise ClipCutError(f"时间码越界: {value!r}")
    return hours * 3600 + minutes * 60 + seconds


def cut_clip_bytes(storage: ObjectStorage, recording: ClipRecording, start: str, end: str) -> bytes:
    """从源录像真切 [start, end) 为 mp4 字节（第 46 刀裁决 3）。

    录像字节写临时文件 -> ``ffmpeg -ss <start> -i <tmp_in> -t <dur> -c copy
    -movflags +faststart -y <tmp_out>`` -> 读输出字节 -> 临时目录随 with 清理。
    ``-ss`` 放在 ``-i`` 之前快进（输入侧 seek，避免解码到切点）；``-c copy``
    不重编码（切点吸附关键帧可接受，重编码留 Out）。任一环节失败抛
    ClipCutError（时间码非法 / 读录像失败 / ffmpeg 非 0 / 无输出）。
    """
    start_seconds = _parse_hms(start)
    end_seconds = _parse_hms(end)
    duration = end_seconds - start_seconds
    if duration <= 0:
        raise ClipCutError(f"切段时长必须为正: {start}-{end}")

    try:
        source = storage.get_bytes(recording.object_key)
    except FileNotFoundError as exc:
        raise ClipCutError(f"源录像字节不存在: {recording.object_key}") from exc

    with tempfile.TemporaryDirectory(prefix="clip-cut-") as tmp:
        tmp_dir = Path(tmp)
        in_path = tmp_dir / "in.mp4"
        out_path = tmp_dir / "out.mp4"
        in_path.write_bytes(source)
        command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            str(start_seconds),
            "-i",
            str(in_path),
            "-t",
            str(duration),
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            "-y",
            str(out_path),
        ]
        try:
            completed = subprocess.run(  # noqa: S603 - 参数全为内部构造，无 shell
                command,
                capture_output=True,
                timeout=_FFMPEG_TIMEOUT,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ClipCutError(f"ffmpeg 无法执行: {exc}") from exc
        if completed.returncode != 0:
            detail = completed.stderr.decode("utf-8", "replace").strip()[:300]
            raise ClipCutError(f"ffmpeg 切段失败（退出码 {completed.returncode}）: {detail}")
        if not out_path.is_file() or out_path.stat().st_size == 0:
            raise ClipCutError("ffmpeg 未产出片段字节")
        return out_path.read_bytes()


def register_recording(
    db: Session, storage: ObjectStorage, *, label: str, content_bytes: bytes
) -> ClipRecording:
    """落一份源录像 + 上传即绑定（第 46 刀裁决 1/2；第 49 刀加真值回执）。

    对象键 ``recordings/{uuid}/<sha16>.mp4``（源录像不是资产，不进 clips/ 资产
    前缀、不进检索、不能发布）；插行拿主键后，把**尚无源录像**（recording_id
    IS NULL）的 pending 候选 UPDATE 绑到这份录像——已登记候选不动（回执锚已
    在），已绑过的候选也不改绑（**顺手的默认动作**；改绑交给第 49 刀的显式端点
    ``bind_candidates``）。返回 (录像行, 本次绑定条数)——条数是后端真值，回执
    不再用前端猜的候选数。
    """
    digest = hashlib.sha256(content_bytes).hexdigest()[:16]
    object_key = f"recordings/{uuid4().hex}/{digest}.mp4"
    storage.put_bytes(object_key, content_bytes)

    recording = ClipRecording(label=label, object_key=object_key, size_bytes=len(content_bytes))
    db.add(recording)
    db.flush()  # 拿主键（绑定 UPDATE 要 recording_id）
    bound = bind_candidates(db, recording.id, candidate_ids=None, only_unbound=True)
    db.commit()
    db.refresh(recording)
    return recording, bound


def bind_candidates(
    db: Session,
    recording_id: int,
    *,
    candidate_ids: list[int] | None,
    only_unbound: bool = False,
) -> int:
    """把候选的源录像改绑到 `recording_id`，返回本次影响的条数（第 49 刀）。

    - `candidate_ids=None` -> 全部待拣（pending）候选；给了 id 就只动这些。
    - `only_unbound=True` -> 只动「尚无源录像」的（上传端点的顺手默认动作）；
      否则连已绑的一起改——这正是改绑要解决的问题（第 46 刀裁决 2 曾被读成
      「绑过就不许改」，把「绑错」变成终态）。
    - 已登记候选一律不动：它们已经拣选过，录像绑定是历史事实（改它不改变已切出
      的字节，只会让留痕失真）。调用方负责把「指定了已登记候选」判成 409。
    - 不 commit（由调用方收口：上传路径在 register_recording 里 commit，改绑端点
      自己 commit）。
    """
    if candidate_ids == []:
        return 0  # `in_([])` 会生成恒假条件（静默 0 行）；显式返回更诚实
    statement = update(ClipCandidate).where(ClipCandidate.status == PENDING)
    if candidate_ids is not None:
        statement = statement.where(ClipCandidate.id.in_(candidate_ids))
    if only_unbound:
        statement = statement.where(ClipCandidate.recording_id.is_(None))
    result = db.execute(statement.values(recording_id=recording_id))
    return result.rowcount


def split_pending_ids(db: Session, ids: list[int]) -> tuple[list[int], list[int]]:
    """把给定 id 分成 (库里存在的, 其中已登记的) 两拨（校验用，纯读）。

    调用方：存在的短于入参 -> 404（有 id 不存在）；已登记的非空 -> 409（与拣选的
    拒绝原子性同口径：校验先于任何写入）。
    """
    rows = db.execute(
        select(ClipCandidate.id, ClipCandidate.status).where(ClipCandidate.id.in_(ids))
    ).all()
    found = [row[0] for row in rows]
    registered = [row[0] for row in rows if row[1] != PENDING]
    return found, registered


def _claim_candidate(db: Session, candidate_id: int) -> int:
    """CAS 占位：pending -> registered，返回影响行数（0=已被别的请求拣走）。

    条件 UPDATE 是并发闸本体（审计刀 9 P1）：预检只保证「当时是 pending」，
    两个并发拣选都能过预检；把状态先抢到手，影响行数为 0 的一方 409。占位后
    立刻 commit（不持锁等 ffmpeg）。
    """
    claimed = db.execute(
        update(ClipCandidate)
        .where(ClipCandidate.id == candidate_id, ClipCandidate.status == PENDING)
        .values(status=REGISTERED)
    ).rowcount
    db.commit()
    return claimed


def _release_claim(db: Session, candidate_id: int) -> None:
    """把 CAS 占位放回 pending（真切失败/源录像缺失时用）：候选必须可重拣。"""
    db.execute(
        update(ClipCandidate)
        .where(ClipCandidate.id == candidate_id, ClipCandidate.status == REGISTERED)
        .values(status=PENDING, registered_asset_id=None)
    )
    db.commit()


def pick_candidates(db: Session, storage: ObjectStorage, ids: list[int]) -> list[Asset]:
    """批量拣选登记：pending 候选 → kind=video / source=clip_pick 资产。

    - 重复 id 去重保序（同一候选勾两次=登记一次）；
    - 任一不存在 -> 404；任一已登记 -> 409（批量含已登记整体拒绝，字节不落）；
    - 每候选一资产：title=转写截断 60 字、挂候选的商品、机洗空字段集
      （machine_wash_field_names 的 video 分支：弃权推进待人洗，0039）；
    - 有源录像（recording_id 非空）-> 真切 mp4 字节 + 预置 transcript 字段；
      切失败 -> 422，该候选保持 pending（前序资产保留，裁决 7）；
    - 无源录像 -> 既有时间码文本字节（旧路径不回归）；
    - 候选置 registered + registered_asset_id 回执锚，逐候选 commit 收口（下一
      候选切失败时前序候选已完整落库）；**真切前先 commit**——ffmpeg 跑在事务
      外（P1#2 纪律，最长 120s 不许占着池连接）。
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

    # 一次 IN 取本批用到的源录像（消掉逐候选 db.get 的写路径 N+1）
    recording_ids = {c.recording_id for c in candidates if c.recording_id is not None}
    recordings: dict[int, ClipRecording] = {}
    if recording_ids:
        recordings = {
            r.id: r
            for r in db.scalars(select(ClipRecording).where(ClipRecording.id.in_(recording_ids)))
        }

    assets: list[Asset] = []
    for candidate in candidates:
        # 收口事务再动子进程：ffmpeg 真切最长 _FFMPEG_TIMEOUT（120s），带着上面
        # 校验读开的事务等它 = idle-in-transaction 占池连接（P1#2 纪律，与
        # registration.py 的「机洗前 commit」同口径）
        db.commit()
        # 状态 CAS 占位（审计刀 9 P1）：预检只保证「当时是 pending」，两个并发
        # 拣选（双击/重试）都能过预检，各自真切、各自登记 —— 库里会出现两份视频
        # 资产而候选只锚一份，另一份成孤儿证据。用条件 UPDATE 把状态先抢到手：
        # 影响行数 0 即已被别人拣走 -> 409（与预检同文案）。占位在 ffmpeg **之前**，
        # 且立刻 commit（不持锁等子进程）；真切失败时下面把它回滚回 pending。
        claimed = _claim_candidate(db, candidate.id)
        if claimed == 0:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"切片候选 {candidate.id} 已登记为资产，不可重复拣选",
            )
        # CAS 之后**重读绑定**（第 49 刀改绑引入的竞态）：预检到占位之间若有人改绑，
        # 内存里还是旧录像——切出来的字节会与库里记的绑定不一致。refresh 一次，
        # 让「切自哪一份」与「库里绑哪一份」恒同（改绑端点只动 pending，占位后
        # 本行已 registered，不会再被改走）。
        db.refresh(candidate)
        if candidate.recording_id is not None:
            # 预取的 map 是**预检时**的快照：并发改绑把候选改到 B 之后，refresh 读到的
            # 是新 id 而 map 里没有 -> 会误判「源录像不存在」。按 id 兜底回查一次。
            recording = recordings.get(candidate.recording_id) or db.get(
                ClipRecording, candidate.recording_id
            )
            if recording is None:  # pragma: no cover - FK 保证存在，防御性同口径
                _release_claim(db, candidate.id)
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"切片候选 {candidate.id} 的源录像不存在，无法切出片段",
                )
            try:
                content_bytes = cut_clip_bytes(
                    storage, recording, candidate.timecode_start, candidate.timecode_end
                )
            except ClipCutError as exc:
                # 裁决 7：切失败不置 registered、不落半个资产，候选保持 pending 可重拣
                # （CAS 已把它写成 registered，这里必须放回去——否则「可重拣」是假的）
                _release_claim(db, candidate.id)
                record_clip_cut(result="failed")
                logger.warning("切片失败: candidate=%s reason=%s", candidate.id, exc)
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"候选 {candidate.id} 切片失败: {exc}",
                ) from exc
            key_suffix = "mp4"
            record_clip_cut(result="ok")
        else:
            content_bytes = transcript_bytes(candidate)
            key_suffix = "txt"  # 旧路径字节是文本：键不能跟着 kind 说 mp4（评审 P1-1）
            record_clip_cut(result="legacy")
        # 转写**两条路径都预置为字段**（裁决 5 + 评审 P1-2 的收口）：video 正文
        # 只从字段进索引、永不读字节（真路径字节是 mp4；旧路径字节是时间码文本，
        # 但正文口径统一走字段后就不必再读它）——旧路径的检索能力因此不回归。
        asset = register_asset(
            db,
            storage,
            kind="video",
            title=candidate.transcript[:60],
            content_bytes=content_bytes,
            filename=None,
            product_id=candidate.product_id,
            source_kind="clip_pick",
            preset_fields={"transcript": candidate.transcript},
            key_suffix=key_suffix,
        )
        candidate.status = REGISTERED
        candidate.registered_asset_id = asset.id
        assets.append(asset)
        # 逐候选收口回执锚（不只是 register_asset 内部的登记 commit）：下一个候选
        # 真切失败会抛 422 中断循环，此时本候选必须已 registered——否则会话关闭
        # 回滚会把锚丢掉，资产却已落库，重拣会重复登记（裁决 7：失败候选保持
        # pending，此前候选完整保留）。
        db.commit()
    return assets
