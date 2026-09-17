# 第 94a 刀 closeout：图片资产活化 + 商品素材视图

日期：2026-09-16。分支 `feat/image-activation-94a`（stacked 于 `feat/asr-93`；#132 为平行链）。

## 交付

1. **「图片描述」治理字段**：图片资产（kind=image）登记预置字段（机洗不冒充=abstained）、人洗确认生效；**检索切块只从该字段进**（对 image 永不读字节，同 video.transcript 先例）；`read_version_text` 对 image 回落该字段（confirmed 优先）——正文出口全收（MCP get_asset/export/正文端点/考核抽题）。
2. **VLM 描述草稿**：`VLM_*` 三 env（key 空=不建客户端；OpenAI 兼容 image_url base64；同步 ≤20s；失败不 fail 登记=纯人洗兜底）。新 `services/vlm.py`（同 asr/llm 风格）。
3. **商品素材聚合**：`GET /api/products/{id}/assets` + 商品卡「素材」段（图/视频/文案/其他分组、**已发布在上+未发布灰标**、懒加载折叠才请求）。
4. **spec_schema 补位**（审计 18 归位）：数码四类模板加 `图片`/`官网` optional 字段（仅字段位，不拉取）。
5. **零迁移**（描述走 fields JSONB 既有机制）。
6. **文档**：ADR 0051（图片活化口径：描述=检索文本面/VLM 只出草稿/证据面生图仍 Out）+ CONTEXT 词条「图片描述」「素材库」+ README 图片段。

## 证据

- **Owner 门禁复验**：1295/0/0/0（+35）+ ruff 全过 + 前端 lint 7/0/build 绿。
- **真栈全链**（实现代理）：抽帧→登记（无 key：描述=abstained）→人洗 PATCH→发布→`retrieval_chunks` 恰 1 块=描述文本→**顾客问「有带支架的显示器吗」命中 A-501 带引用**。
- **VLM 替身实跑**：草稿落 extracted(source=machine)→不确认发布索引 0 块→修订确认 v2→索引=描述、citations 含 `{503,v2}`（人洗闸门钉死）。
- **Owner 浏览器复核**：商品卡素材段展开截图（视频组 2 条：已发布在上/未发布灰标；文案组 1 条）——分组与权威口径呈现正确。
- **export 容错真栈验证**（评审修后）：发布无描述图片 A-502→`export_published` **200+占位「（此版本暂无可读正文：…）」**、A-501/502 均在列（修复前=整体 tool error）。

## 评审实修（两轴子代理：P0×0）

1. **`export_published` 逐资产容错**（真 P1，46 刀「一份资产带崩全局读链路」同形状）：无描述已发布图片不再炸全量导出——该资产照常在列、正文给可行动占位；`get_asset`/正文端点保持单资产 409 诚实错误。
2. **video 换字节 409 死代码清除**：`_classify_upload` 对 mp4 恒 None 先撞 415（文案误导「类型不收」）——video 闸**提前于类型闸**，专门 409 文案（「正文由 transcript 承载」）。
3. **素材段「重试」按钮假行为**：`onClick=toggle` 只折叠不重试——抽 `load()`，重试按钮真重试。
4. **无描述错误文案语义化**：「版本字节不是合法 UTF-8」→「该图片资产尚无图片描述…（先补描述再读）」；两处旧文案断言同步更新。

## 记债

1. `ops.REF_KINDS` 仍只 material/video——图片资产不进运营编排投放引用（98/98b 内容线时重审）。
2. export 容错缺自动化钉子（真栈已验证；审计 19 清单）。
3. VLM 真 key 未配（草稿路径以替身实跑，真云待 Owner）。

## 后续

第 94b 刀：媒体引用回答（media_citations 图+视频 + 鉴权媒体端点 + 客服/widget 渲染）。
