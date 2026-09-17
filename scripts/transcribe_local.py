"""本地兜底转写：funasr paraformer-zh 把源录像转成切片候选（第 93 刀，ADR 0050）。

用途：**没有云 ASR key**（或云额度用尽）时的兜底通道——把某份已上传源录像本地
转写出候选，落 ``clip_candidates``（``transcript_source='local'``）。产出与云端点
同形：pending（人工拣选闸门不变）、带 ``recording_id``、时间码走同一套停顿聚合
（gap ≥1.2s 断段 / 累计 ≥20s 强切 / ≤60 段上限）。拣选→真切→发布链路完全一样。

安装（**不进 api 运行时依赖与镜像**：funasr 依赖 torch，CPU 包 GB 级；3–5 分钟
录像按 RTF ~17x 也要数十秒，塞进服务进程会砸掉镜像体积与「同步 ≤120s」两条纪律
——roadmap 刀 93 定案「本地仅脚本级兜底」）：

    uv sync --extra asr-local                 # 仓库根（workspace）；首次拉 torch，GB 级
    uv run python scripts/transcribe_local.py \
        --db postgresql://suite:suite@localhost:5433/suite --recording-id 3

引擎：funasr **paraformer-zh**（中文 CER 低 + 原生时间戳，与云侧 paraformer-v2
同源可互备；89 刀 ⑤ 定案），同带 fsmn-vad（长音频切句）与 ct-punc（标点），
``sentence_timestamp=True`` 直接给句级 start/end（毫秒），转成秒后喂云端点同一个
``aggregate_segments``——**本地与云的候选口径不会漂**（同一真源）。

边界（直连库）：``--db`` 直连写库、**绕过 services 层端点**——本机跑转写时 api
未必起，也不该为一次兜底把 torch 装进服务进程。判据/落值与云端点同源（同一聚合
函数 + 同一 ``create_candidates``），只多写 ``transcript_source='local'`` 这个通道
标注（来源是既成事实，只读）。脚本自持事务：失败不落半批候选。

需要 ffmpeg（提音轨，同云端点）与源录像字节可读（默认 ``data/objects``，
``--objects``/``STORAGE_ROOT`` 可改）。
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

# 脚本直连库（同 realdata 脚本口径）：workspace 根跑 uv run 时 suite_api 可导入
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api" / "src"))


def ms_to_seconds(value: object) -> float:
    """funasr 毫秒时间戳 → 秒（四舍五入到毫秒，够聚合与时间码用）。"""
    return round(float(value) / 1000.0, 3)


def segments_from_funasr(result: dict) -> list[dict]:
    """funasr 单条结果 → 句级段 ``[{start, end, text}]``（与云 segments 同形）。

    优先取 ``sentence_info``（带 punc/sentence_timestamp 时的句级结果）；没给句级
    就退回「全文 + 字级时间戳」的**整段一段**——判不出句边界宁可不判，不编造
    （云侧同口径：没时间戳就当失败）。文本空/时间戳缺失 → 该段不产出。
    """
    segments: list[dict] = []
    for item in result.get("sentence_info") or []:
        text = str(item.get("text", "")).strip()
        start, end = item.get("start"), item.get("end")
        if not text or start is None or end is None:
            continue
        segments.append({"start": ms_to_seconds(start), "end": ms_to_seconds(end), "text": text})
    if segments:
        return segments
    text = str(result.get("text", "")).strip()
    stamps = result.get("timestamp") or []
    if text and stamps:
        return [
            {
                "start": ms_to_seconds(stamps[0][0]),
                "end": ms_to_seconds(stamps[-1][1]),
                "text": text,
            }
        ]
    return []


def transcribe_wav_with_funasr(
    wav_path: Path, *, model_name: str = "paraformer-zh"
) -> list[dict]:
    """wav 文件 → 句级段（funasr 延迟 import：没装可选依赖时给出可行动报错）。"""
    try:
        from funasr import AutoModel
    except ImportError as exc:  # pragma: no cover - 取决于本机是否装了可选组
        raise SystemExit(
            "未安装 funasr（可选依赖组）：先跑 `uv sync --extra asr-local`，"
            "或改用云端点 POST /api/clips/recordings/{id}/transcribe"
        ) from exc
    model = AutoModel(
        model=model_name,
        vad_model="fsmn-vad",
        punc_model="ct-punc",
        # 关掉 funasr 的模型版本自检请求：本机离线/内网也能跑
        disable_update=True,
    )
    results = model.generate(input=str(wav_path), batch_size_s=300, sentence_timestamp=True)
    segments: list[dict] = []
    for result in results or []:
        segments.extend(segments_from_funasr(result))
    return segments


def run(
    db_url: str,
    recording_id: int,
    *,
    objects_root: Path,
    product_id: int | None,
    model_name: str = "paraformer-zh",
) -> int:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from suite_api.db import to_sqlalchemy_url
    from suite_api.models import ClipRecording
    from suite_api.services import asr

    engine = create_engine(to_sqlalchemy_url(db_url))
    with Session(engine) as db:
        recording = db.get(ClipRecording, recording_id)
        if recording is None:
            print(f"错误：源录像 {recording_id} 不存在", file=sys.stderr)
            return 2
        existing = asr.pending_count(db, recording_id, sources=(asr.CLOUD, asr.LOCAL))
        if existing:
            print(
                f"错误：源录像 {recording_id} 已有 {existing} 条未拣选的转写候选"
                "（先拣选/处理完再跑，避免重复堆候选）",
                file=sys.stderr,
            )
            return 2
        source = objects_root / recording.object_key
        if not source.is_file():
            print(f"错误：源录像字节不存在：{source}", file=sys.stderr)
            return 2
        ref = asr.RecordingRef.of(recording)
        db.rollback()  # 收口事务再跑本地模型（长任务不许占着池连接）

        wav_bytes = asr.extract_audio_wav(source.read_bytes())
        with tempfile.TemporaryDirectory(prefix="asr-local-") as tmp:
            wav_path = Path(tmp) / "audio.wav"
            wav_path.write_bytes(wav_bytes)
            segments = transcribe_wav_with_funasr(wav_path, model_name=model_name)
        if not segments:
            print("本地转写没有识别到语音（未生成候选）", file=sys.stderr)
            return 0
        outcome = asr.aggregate_segments(segments)
        created = asr.create_candidates(
            db, recording=ref, segments=outcome.segments, product_id=product_id, source=asr.LOCAL
        )
    print(
        f"本地转写完成：源录像 {recording_id} → {created} 条候选"
        f"（status=pending、transcript_source=local）"
    )
    if outcome.merged:
        print(
            f"  提示：聚合出 {outcome.before_cap} 段，超过 {asr.MAX_CANDIDATES} 段上限，"
            "已合并相邻段"
        )
    engine.dispose()
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="本地兜底转写（funasr paraformer-zh；第 93 刀）"
    )
    parser.add_argument(
        "--db",
        default=os.environ.get("DATABASE_URL"),
        help="演示库 URL（默认取 DATABASE_URL）；宿主 compose 用"
        " postgresql://suite:suite@localhost:5433/suite",
    )
    parser.add_argument("--recording-id", type=int, required=True, help="clip_recordings.id")
    parser.add_argument(
        "--objects",
        type=Path,
        default=Path(os.environ.get("STORAGE_ROOT", "data/objects")),
        help="对象存储根目录（默认 data/objects，或 STORAGE_ROOT）",
    )
    parser.add_argument(
        "--product-id",
        type=int,
        default=None,
        help="可选：整段录像只讲一件商品时顺手归属（默认不归属，不猜）",
    )
    parser.add_argument(
        "--model", default="paraformer-zh", help="funasr 模型名（默认 paraformer-zh）"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.db:
        print("错误：需要 --db 或 DATABASE_URL 环境变量", file=sys.stderr)
        return 2
    return run(
        args.db,
        args.recording_id,
        objects_root=args.objects,
        product_id=args.product_id,
        model_name=args.model,
    )


if __name__ == "__main__":
    raise SystemExit(main())
