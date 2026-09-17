# 第 112 刀 closeout：洗帧 502 修复 + 拣选回执跳转 + 真实素材调研

日期：2026-09-17。分支 `feat/frame-wash-112`。

## 交付

### A. 洗帧韧性（P0×2 修——VLM 超时+抽帧失败两侧都单帧跳过）

- **VLM 侧**（112A）：单帧 `VLMUnavailable` → 跳过+warning+计数；全帧评不上才 502（文案带帧数可行动）；`failed_frames` 字段回传前端显示「N 帧打分失败已跳过」。
- **抽帧侧**（Owner 补修，审计 113 P0）：单帧 `FrameExtractionError` → 跳过+计数；502 文案改「全部 N 帧处理失败（抽帧或打分）」。
- **测试** +6：4 单测（VLM 跳过/全失败抛/解析失败边界/NotConfigured 冒泡）+ 1 集成（真 ffmpeg+PG 单帧超时→200）+ 1 Owner 补（抽帧单帧失败跳过）。

### B. 拣选回执跳转（112A）

- PickReceipt 组件：「查看 A-xxxx」内联链接（上限 6 条+「另 N 条」），四处清回执。
- 真栈 GUI 验收：C-0040 → 回执「查看 A-0526」→ 点击跳转 ✓。

### C. 真实素材调研（112B）

- **最优候选**：Wikimedia Commons `Dell S3423DWC 34-Inch Curved Monitor Unboxing`（CC BY 4.0、4:22、英文口播讲解）。
- **已下载+全链实测**：上传→ASR（Groq 6.8s/49 句/12 候选）→拣选（A-527/A-528）→发布→**洗帧 200（采样 5 帧/候选 2 条/failed_frames=0）**——VLM 打出真实商品帧评分「显示器主体完整清晰 score=8」。
- 其他源结论：Pexels 无讲解语音；Internet Archive PD 合集需裁剪；NASA 非商品。

### D. 健壮性审计（第 113 刀，只读）

- 已修三处未回退（judge/ASR 预算/CAS）✓
- 新发现：P0 抽帧单点（本刀已修）+ P1×3（ASR 中途丢已转块/拣选第 k 条切失败无部分回执/成片单素材缺失杀整单）→ backlog
- fail-fast 合理六处不改。

## 证据

Owner 门禁 **1682/0/0/0**（+20）+ ruff + 前端 lint/build。真实素材全链实测（见上）。审计报告已入档。

## 后续

Owner 继续第四站验收（洗帧现在能正常出候选帧+有真实素材）→ 五/六/七站 → W8-W11 优化刀。
