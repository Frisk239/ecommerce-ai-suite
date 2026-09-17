# 第 94b 刀 closeout：媒体引用回答

日期：2026-09-16。分支 `feat/media-citations-94b`（stacked 于 `feat/image-activation-94a`）。

## 交付

1. **media_citations 服务端派生**：检索命中的 image/video 资产 → `complete.media_citations=[{asset_id, version_no, mime}]`（mime 常量表按 kind+后缀；无媒体恒 `[]`；模型无决定权，与 citations 同纪律）；SSE complete + 消息落库（**迁移 0031** 姊妹列）+ 操作者预览回放同构。
2. **媒体字节端点** `GET /api/customer/assets/{id}/media`：只出当前已发布指针版（未发布/废弃 404 不泄漏存在性）；图片直出；**视频 Range**（206/416，`iter_bytes` 64KiB 流式分块——不整读内存）；不暴露 object_key；双通道鉴权（顾客 Bearer/操作者 cookie）+ **query token**（`?token=` 供 `<img>/<video>` 场景，与 Bearer 同校验）。
3. **日志脱敏**：媒体端点的 `?token=` 在访问日志剥除（observability 处理器）。
4. **前端**：MessageBubble 附件区与引用芯片区并列（图片 `<img>` 限宽圆角/视频 `<video controls preload=metadata>`）；顾客页媒体请求带 query 令牌。
5. **文档**：ADR 0052（媒体引用口径+query token 边界权衡）+ CONTEXT 词条「媒体引用」+ README 媒体附件段。

## 证据

- **Owner 门禁复验**：`--junitxml` **tests=1346, failures=0, errors=0, skipped=0**（+51）；ruff 全过。
- **真栈（实现代理实录）**：顾客通道问「有带支架的显示器吗」→ `media_citations=[{501,v1,image/jpeg},{503,v2,image/jpeg}]`；`curl` 带 query token 取媒体 200/63701B/**md5 与库内对象逐字节一致**；无凭证 401；消息落库两行与载荷全等。视频：问切片命中问句 → A-264（video/mp4）在 media_citations（Range 数字见代理报告）。
- **Owner 浏览器验收（IAB 截图）**：独立顾客页问句 → 回答「有，相关显示器侧面带可调节支架[1][2]」+ **两张显示器实拍图直出**（A-0501/A-0503）+ 引用芯片并列 + 评分条照常。

## 评审实修（两轴子代理：无 P0/P1 硬违规、无 Standards 违规）

- 唯一小 wart：`test_media_integration.py` 的 `202 or 201` 松断言改精确（register 端点契约 in (201, 202)）。
- 确证要点：派生点在 OOV/覆盖闸之后（拒答不挂图）；未发布/废弃/非媒体/.txt 旧切片统一 404 同文案；query token 与 Bearer 同校验、过期 401 与发问同口径；Range 真流式（iter_bytes 64KiB 生成器，平台测试钉跨块多 yield）；widget iframe 内媒体可用（token 走 React 态非 cookie）；脱敏处理器位于 JSONRenderer 前的正确层、端到端钉子承重。

## 记债

1. 过期令牌打媒体端点的行为缺自动化钉子（代码对、口径同发问 401——审计 19 清单）。
2. 媒体端点无独立 RateLimit（ADR 0052 已记——演示形态可接受，公网部署时评估）。
3. 每次 Range 请求重验 cookie+查库（演示规模可接受；压测刀 103 观察项）。

## 后续

第 94c 刀：直播洗帧（VLM 帧候选+人工拣选闸门+clip_frame 来源词）。
