# 工程第 32 刀 closeout：多来源数据 II 收口（feat/real-data-2）

日期：2026-09-09。上刀 intake：`docs/progress/real-data-1-intake.md`（通过）。短对齐：`.scratch/real-data-2/spec.md`（roadmap §4 第 32 行；零产品代码）。基线=`origin/main`（PR #39 / `6ba599d`）。

## 交付（四源四通道故事可讲——到此停灌）

1. **`scripts/realdata/load_abcd_dialogues.py`**：ABCD（MIT）gzip json → 三切分展平 → action 轮丢弃 → 转写 `顾客：`/`客服：`（与回流同构）→ 直调 `register_asset(kind=dialogue, source_kind=session_backflow)`；`--publish K` 经 API 确认 qa_pairs+发布。
2. **`scripts/realdata/load_wands_clips.py`**：WANDS（MIT）三件 TSV → Exact join → 合成切片候选 → 直连幂等灌 `clip_candidates`（承载商品「WANDS 家具（演示）」；幂等键 timecode+transcript）。
3. 离线单测 +11（ABCD 解析/转写/title/抽样；WANDS TSV/join/候选/timecode/CSV）；`scripts/realdata/README.md` 两源许可与通道映射。

## Owner 验收

- `git diff origin/main -- apps/api/src apps/web packages` 空。
- `test_realdata_scripts.py` 27 passed（含刀 I+II）。
- 评审：`docs/progress/real-data-2-review.md` 净可合。

## 计数（摘自 junitxml，机械）

- ruff（apps packages scripts）：All checks passed
- 无 DB：`tests=583 passed=437 failed/errored=0 skipped=146`
- 带 DB（5433）：`tests=583 passed=583 failed/errored=0 skipped=0`（基线 572 → 583 只增 11）

## 遗留

Wikidata/WANDS 商品仍 `spec_schema={}`（0010/0019 在这批真名字上未走通）→ 第 33 刀。禁止数据 III。

## 下一刀

**第 33 刀：数据契约与口径**（真实 spec_schema + 写回/闸门测试 + README 去掉 pgvector RAG /「AI 扮演顾客」）。
