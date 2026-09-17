"""Slice 112B evidence probe: real cloud ASR over the candidate demo file.

Reads a local mp4, extracts the audio track via the repo's own asr service,
transcribes it (single chunk for a 4:22 clip), then runs the same pause
aggregation the /transcribe route uses, and reports the expected candidate
count. Writes the evidence to out/112/asr-evidence.json. No DB writes.
"""

import json
import sys
import time
from pathlib import Path

ROOT = Path(r"E:\code\ecommerce-ai-suite")
sys.path.insert(0, str(ROOT / "apps" / "api" / "src"))

from suite_api.services import asr  # noqa: E402


def main() -> None:
    path = Path(sys.argv[1])
    data = path.read_bytes()
    t0 = time.time()
    wav = asr.extract_audio_wav(data)
    chunks = asr.split_wav_chunks(wav)
    print(f"wav bytes={len(wav)} extract_s={time.time() - t0:.1f} chunks={len(chunks)}")
    segments: list[dict] = []
    for offset, chunk in chunks:
        t1 = time.time()
        got = asr.transcribe_audio(chunk, filename=f"chunk-{offset:.0f}.wav")
        if isinstance(got, str):  # defensive: some backends return plain text
            got = [{"start": offset, "end": offset, "text": got}]
        segments.extend(got)
        print(f"chunk offset={offset:.1f} took={time.time() - t1:.1f}s segments={len(got)}")
    outcome = asr.aggregate_segments(segments)
    print(f"raw_segments={len(segments)} aggregated={len(outcome.segments)} merged={outcome.merged}")
    for seg in outcome.segments[:4]:
        print(f"  [{seg.start:.1f}-{seg.end:.1f}] {seg.transcript[:90]}")
    evidence = {
        "source_file": str(path),
        "duration_s": 262.5,
        "wav_bytes": len(wav),
        "chunks": len(chunks),
        "raw_segments": len(segments),
        "aggregated_candidates": len(outcome.segments),
        "merged": outcome.merged,
        "segments": [
            {"start": s.start, "end": s.end, "transcript": s.transcript} for s in outcome.segments
        ],
    }
    out = ROOT / "out" / "112" / "asr-evidence.json"
    out.write_text(json.dumps(evidence, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"evidence written: {out}")


if __name__ == "__main__":
    main()
