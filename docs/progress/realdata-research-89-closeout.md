# 第 89 刀 closeout：数据源调研刀

日期：2026-09-16。分支 `feat/realdata-research-89`（stacked 于 `feat/phase4-kickoff-88`，PR 链 #124→#125→本刀）。

## 交付

`docs/research/real-store-data-sources.md`——五项带实测证据的调研与定案：

1. **Wikidata 数码外设**：五类 72 件（CC0），正确 QID 实测修正为 Q250/Q7987/Q5290/Q186819/Q64684632；spec_schema 定 manufacturer/image/website/release_year（显示器+高/宽），覆盖率 22–67%。
2. **ChineseNlpCorpus 数码三类目**：平板 10,000 / 计算机 3,992 / 手机 2,323（合计 26%），延续「评论资产不挂商品」先例直接复用。
3. **开放许可视频辅轨**：Pexels 主（key 待申请，200 req/h 免费）+ Commons 补充（keyboard 视频 158、product demonstration 1,415 实测）；红线确认不爬 B 站/抖音回放。
4. **云 ASR 比价**：SiliconFlow SenseVoiceSmall 免费（无时间戳）/ Groq turbo 免费层 ≈8h/日（有时间戳、25MB 上限）/ DashScope paraformer-v2 0.288 元/时+每月 10h 免费（句级+字级时间戳）。
5. **本地兜底**：funasr paraformer-zh 主引擎（字级时间戳+中文 CER 强，与云侧 paraformer-v2 同族互备），SenseVoiceSmall 快速分支。

## Owner 裁决（补注进调研文档）

刀 93 候选聚合**需要句级时间戳**——云 ASR 实现主选定为 Groq turbo 或 DashScope paraformer-v2；SiliconFlow SenseVoiceSmall 无时间戳，降级为纯 transcript 字段快速兜底，不承载候选聚合。

## 偏差（调研过程，子代理备注收编）

- 任务书给的 5 个 QID 全部失准，子代理经 Special:EntityData 实测修正（修正值已进文档）；`online_shopping_10_cmds` 路径 404，实名 `online_shopping_10_cats`。
- WDQS 四类聚合查询超时，改单类逐查；Commons/Pexels 官网 TLS 间歇失败靠重试与旁路完成。
- Pexels 未申请 key 故无 API 实调（按文档定案）。

## 记债

1. **Pexels API key 待申请**（用户动作，辅轨拉视频/使用剧本Ⅱ之前）——免费即发。
2. Wikidata 数码池 72 件偏小（先例 91）：刀 90 以耳机/显示器为主力类目；若不足再议扩类目，不阻塞。

## 后续

第 90 刀开店数据刀：Wikidata 灌入 + 类目模板（新类目：键盘/鼠标/显示器/耳机）+ 评论数码三类目灌入 + 店主政策文档人工登记 + 基线刷新对账。
