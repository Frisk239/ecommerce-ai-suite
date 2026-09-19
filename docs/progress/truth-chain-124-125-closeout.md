# 第 124/125 刀 closeout：切片真相链修根 + 真实直播素材接入

分支 `feat/truth-chain-124-125`。触发：Owner 裁决（2026-09-19）「针对验收发现的所有问题落优化路线；缺真实直播素材用 agent-reach 调研抓取；洗帧 bug 修复+健壮性；审计同类问题」。施工权威 `docs/roadmap-production-hardening.md`。

## 第 124 刀：切片真相链修根

**病症**（生产重灌库实证）：已发布视频资产 A-26/27/28 的转写说保温杯（中文话术）、切出的帧是显示器（Dell 开箱画面）——「引用=证据」红线在视频面破裂。

**根因链**（三层）：
1. `register_recording` 的「顺手默认动作」（46 刀裁决，单录像演示库时代）：上传任意录像 → 把**所有**未绑定 pending 候选（种子保温杯话术 C-1..C-4）自动绑上——多源生产库=跨场劫持。
2. reseed `pending[:3]` 全局盲选：把被劫持的演示候选注册成本录像资产。
3. 洗帧步商品匹配（`"显示器" in name`）在英文商品名库落空 + `if dell:` 无声跳过。

**修法**：
- **服务面**：顺手默认动作收窄为「**同名批次**才自动绑」（`source_video_label == label`）——ASR 转写候选创建即绑定（不走此路径，语义不变）；异名批次留给显式改绑端点（49 刀 UI 已有面）。HTTP 上传名恒 `.mp4`，种子/别场批次永远不会被顺手绑。
- **脚本面**：reseed 视频链重构为 `_video_chain()`（Dell 与 Samsung 共用）：转写带 `product_id`（类目商品归属锚）、拣选只从**本录像候选**里选（逐条打印转写头）、商品匹配按类目、失败/零候选帧必有声（✗/⚠ 不打 ✓）。
- **数据修复**：已发布错资产不可废弃（0042 权威证据闸门）——走系统自己的纠错机制：**修好的链路清库重灌**。

**验证**（重灌后库内真相）：
- 种子候选 C-1..C-4 recording_id 全空（未再被劫持）✓
- Dell 拣选 C-5/6/7=真英文转写（"Hey what's good, I'm Sadia…"），A-34/35/36 发布、洗帧出 2 候选帧、帧资产 A-37 走待人洗 ✓
- **保温杯材质问句从「带引用作答」变诚实拒答+缺口 G-1**——旧库的「正确回答」一直引的是污染转写，真相链恢复后如实暴露库内无保温杯规格文档（缺口飞轮正确触发）✓

**测试**：收窄语义更新 6 处钉子（rebind 两枚改自包含同名批次+异名不劫持钉子；real_clips/frames/compose 的 `_insert_candidate` 补 label 参数对齐上传名）；22+39 集成全绿，全量 **1904 passed**（集成环境带 SUITE_TEST_DATABASE_URL）。

## 第 125 刀：真实直播素材调研与接入

**调研**（`docs/research/live-stream-sources.md`，agent-reach）：公网「可直接下载的直播形态商品内容」主要靠 Internet Archive 的许可过滤检索；学术直播数据集（LPR4M/COPE/LiveSeg）协议墙高或体量过大；排除 B 站/抖音/Twitch（ToS 灰色，89 刀先例）。

**定案**：Samsung「Unbox & Discover 2023」官方发布会直播——**CC BY 3.0**（Samsung 官方标注）、40 分钟多商品章节（Neo QLED 8K/OLED/Gaming 屏）、184MB mp4 直链、直播形态+英文讲解。IA 条目 `youtube-vUFub76fViA`。

**接入**：`step_livestream()` 并入重灌链（与 Dell 共用 `_video_chain`）。实测：40 分钟 → **60 条 ASR 候选**；拣选 4 条（"Hello, welcome to Unboxed and Discover…"）→ A-38/39/40/41 发布；洗帧：A-38 开场段 11 帧全采 0 候选（VLM 判无信息帧——过滤正确），**A-40/41 各出 score 8 候选帧，VLM 评注「电视主体清晰、右下角主播头像」与直播画面三一致**（转写=画面=商品）；帧资产 A-42 登记（cut_from A-40·v1）走待人洗。

**E2E**（顾客 SSE，重灌库六问）：Nutella 400g e ✓ / Sennheiser 2009 年 ✓ / **"34-inch Dell curved monitor 是什么视频"→ 引 A-34/A-36 答出型号 S3423DWC** ✓ / "Unboxed and Discover 是什么活动"→ 引 A-38 ✓ / 保温杯诚实拒答+G-1 ✓ / 星巴克 OOV 拒答+工单 ✓。

## 已知残留（如实记录）

- A-38 洗帧遇云 VLM 网关超时（APITimeoutError 逐帧跳过 + 502「请稍后重试」诚实拒绝）——云侧抖动非代码缺陷，恢复后重试即出帧（A-40 实证）。
- 商品归属锚「视频图形阵列（显示器）」——Wikidata 类目采样把概念条目（VGA）当商品灌入，数据质量瑕疵记入审计债（不阻塞链路）。
- demo_prepare 对生产库 23 项 MISS 属预期（它盯旧演示库素材）；reseed verify 步已加口径说明。
