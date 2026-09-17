# 第 113 刀 closeout：健壮性审计——「单点失败杀整批」全系统排查

日期：2026-09-17。只读子代理。

## 已修三处未回退 ✓

1. run_eval judge（87 刀）：3 次重试+fail-soft ✓
2. asr 转写（审计 19）：300s 预算+超限先落部分候选 ✓
3. compose publish CAS（审计 19）：REGISTERING 瞬态+条件更新 ✓

## 新发现

| 级 | 位置 | 问题 |
|---|---|---|
| **P0** | `frames.generate_candidates` 抽帧 | ffmpeg 单帧失败（越界/损坏/超时）→ 422 杀整批零候选——**112 只包了 VLM 侧，抽帧侧还要修** |
| P1 | `asr.transcribe_recording` 中途块 ASRUnavailable | 丢弃已转块（仅预算路径存部分）；重跑全量重付费+409 挡——取舍 |
| P1 | `clips.pick_candidates` 第 k 条切失败 | 422 无部分回执；前端 catch 不 reload，同选择重选撞 409 卡死 |
| P1 | `video_compose.plan_compose` 单素材字节缺失 | 整单 422；同函数 ffprobe/正文失败均跳过——降级口径不一致 |
| P2 | material.approve 配图暂存 409 | material 资产已落库成孤儿，重试重复登记（已知债） |
| P2 | embed 一批失败整版 NULL；load_reviews 无异常兜底 | 观察项 |

## fail-fast 合理不改（有 failed+last_error+重试语义）

material / ops / machine_wash / coaching / import-csv（逐行 try+commit）/ 改绑（原子单 UPDATE）。

## 处置

P0（抽帧单帧跳过）随 112 刀实修；P1×3 记入后续 backlog（各需独立刀）；P2 维持观察。
