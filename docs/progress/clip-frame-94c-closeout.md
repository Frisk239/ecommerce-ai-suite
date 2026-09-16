# 第 94c 刀 closeout：直播洗帧（内容自循环收官）

日期：2026-09-16。分支 `feat/clip-frame-94c`（stacked 于 `feat/media-citations-94b`）。

## 交付

1. **帧候选生成**（`services/frames.py`）：**均匀采样**（每 5s/上限 24 帧）+ VLM 打分（≥6 过线/上限 8）——替代 roadmap 原案「转写句时间戳定位」（transcript 无时间戳，随刀订正并标注）；候选=请求态（480px 缩略 base64 回传，不落库）；无 VLM key 端点 409+状态端点（fail-closed）。
2. **确认登记**：`POST /{id}/frames`（at_second）→ ffmpeg 全尺寸 jpg → `register_asset(kind=image, source_kind=clip_frame)`（应用层枚举零迁移）、标题 `{名} · 实拍帧 {mm:ss}`、挂商品透传、回执 `cut_from=A-xxx·vN`。
3. **94a/94b 链路复用**：登记后 VLM 描述草稿→人洗→发布→顾客问句 media_citations 出真帧。
4. **前端**：已发布视频资产详情「洗帧到素材库」按钮（无 key 禁用+明文提示）→ FrameWashDrawer 候选网格（缩略/分数/时间点/VLM 一句话）→ 勾选登记。
5. **文档**：ADR 0053（含三项自报偏差：audit 不留痕的理由/采样替代时间戳/ffmpeg 两处实测修正）+ CONTEXT 词条「直播洗帧」+ README + roadmap 随刀订正。

## 证据

- **Owner 门禁复验**：`--junitxml` **tests=1373, failures=0, errors=0, skipped=0**；ruff 全过；前端 lint（与基线同 warnings）/build 绿。
- **真栈全链（代理实录）**：A-264（47s 已发布切片）洗帧 1.1s——采样 10 帧/7 过线 → 确认 5s 帧 → **A-505**（clip_frame/jpg/描述草稿）→ 人洗 → 发布 → 顾客问「有瓶装水直播实拍的清晰商品图吗」→ citations+media_citations 含 505、媒体端点 200 出真帧（4692B）。**内容自循环（直播→切片→帧→素材库→客服引用）全链真跑通。**
- **Owner 浏览器复核**：A-264 详情「洗帧到素材库」禁用态+提示文案如实（无 VLM key）。

## 评审实修（两轴子代理：均无 P0/无硬违规）

- frames.py docstring「幂等性只由确认登记承载」措辞美化——对齐 ADR 0053 Debt（确认动作本身无幂等键，并发双击会重复登记；单客户端 registering 守卫）。
- 确证要点：prompt 注入面干净（无用户可控文本入 prompt）；事务纪律合规（快照→rollback→ffmpeg/VLM→commit）；候选响应体积 0.3-0.6MB 可接受。

## 记债

1. 确认端点并发双击无幂等键（ADR 0053 Debt；并发场景触发时补 at_second+资产 CAS）。
2. VLM 真 key 未配（打分与描述均替身实跑；真云待 Owner）。

## 后续

第 95 刀：widget 轻量续接（未过期令牌+active 会话恢复）。**多模态线 93/94a/94b/94c 四刀至此收官。**
