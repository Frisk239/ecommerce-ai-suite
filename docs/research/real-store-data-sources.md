# 数码外设店数据源与选型调研（第 89 刀）

日期：2026-09-16。探针环境：本机代理 127.0.0.1:7890；query.wikidata.org、commons.wikimedia.org API、GitHub raw 均为 curl 直测。

## ① Wikidata 数码外设 SPARQL 实测

**实测/证据**（POST https://query.wikidata.org/sparql，`Accept: application/sparql-results+json`）：
- 任务书给的 QID 全部失准（Q1429826/Q208585/Q51626/Q61426 经 Special:EntityData 核验均为无关实体）。修正锚点：键盘 **Q250**、鼠标 **Q7987**、显示器 **Q5290**、耳机 **Q186819**、USB 电源适配器 **Q64684632**。
- 可跑样例 A（实测返回 16）：`SELECT (COUNT(?i) AS ?c) WHERE { ?i wdt:P31/wdt:P279* wd:Q250 }`。五类 `P31/P279*` 计数实测：键盘 16、鼠标 14、显示器 23（直接 P31 为 0，必须子类展开）、耳机 18、USB 适配器 1，**合计 72 件**（先例 `scripts/realdata/fetch_wikidata_products.py` 同通道拉到 91 件）。
- 属性覆盖实测（除 P31）：键盘 16 件——P18 图 8、P176 制造商 7、P856 官网 7；耳机 18 件——**P176 12（67%）**、P856 10、P571 上市 8、P18 6；显示器 23 件——P18 10、P2048 高 5、P2049 宽 5。质量 P2067、接口/连接器≈0。**每件平均可用规格属性仅 1–2 个**。
- 许可：CC0 确认（wikidata.org/wiki/Wikidata:Licensing）。

**结论与推荐**：沿用既有 fetch 脚本通道可行，但实例池小（72 vs 91），数码店商品以耳机/显示器为主力类目；spec_schema 只收 manufacturer(P176)、image(P18)、website(P856)、release_year(P571)，显示器另加 height/width(P2048/P2049)；质量/接口覆盖率≈0 不入 schema，靠店主讲解视频与人工补录。

**风险与边界**：中文标签稀疏（先例类目仅 6–17 条带中文标签）；WDQS 限速激进（约 1 请求/分钟，429 按 Retry-After 退避）；正确 QID 已实测，须写入脚本常量防再错。

## ② ChineseNlpCorpus 数码三类目抽样

**实测/证据**：目录实名 `online_shopping_10_cats`；全量 CSV（11.3MB，仓库内直链下载）实测 **62,774 行**，列仅 `cat/label/review`。类目规模：**平板 10,000、计算机 3,992、手机 2,323**，数码三类合计 **16,315 条（26%）**。平均长度：平板 43 字、计算机 62 字、手机 104 字。抽样质量两极：有具体机型口吻（「使用 C885 这么久…查联系人方便」「看中了宏碁的三年质保」），也有水评（「很不错。。。。。。很好的平板」）。

**结论与推荐**：三类目存在且量足；语料无商品字段，延续先例「评论资产不挂商品」（`load_reviews.py` 已验证 200 行/批 import-csv + publish 通道）在数码店直接复用，筛 cat∈{平板,计算机,手机} 灌入。

**风险与边界**：语料为 2018 年（机型老旧，作客服对话语料无碍，作商品事实源不可）；CSV 带 BOM 需剥；label 仅正/负二值。

## ③ 开放许可视频源

**实测/证据**：
- Commons API（无 key 直测）：`srsearch=keyboard filetype:video` → totalhits **158**；`product demonstration filetype:video` → **1,415**。抽检 `File:System76 Launch keyboard video soothing.webm`：`iiprop=metadata` 仅含 video 流、**无 audio 键**（有画面无语音可用元数据判别）；许可逐文件各异，该例 CC BY-SA 4.0。
- Pexels（未申请 key，按文档定案）：注册 pexels.com/api → 「Your API key」免费即发；默认 **200 请求/时、20,000/月**（pexels.com/api/documentation/）；Pexels License 免费商用、可改编、无需署名，禁转售原图/建竞品图库。

**结论与推荐**：辅轨批量拉「有画面无语音」产品展示视频**可行**——首选 Pexels（量质稳定、许可最宽、key 待申请），Commons 作免 key 补充。

**风险与边界**：红线确认——**不爬 B 站/抖音直播回放**；Commons 许可杂，须逐文件落库许可字段；Commons 有声文件靠 metadata 过滤。

## ④ 云 ASR 免费档比价（中文、带时间戳优先）

**实测/证据**：
- **SiliconFlow**：SenseVoiceSmall **免费档**，OpenAI 兼容 `POST api.siliconflow.cn/v1/audio/transcriptions`（api-docs.siliconflow.cn）；SenseVoiceSmall **不输出时间戳**（FunASR issue #2027）。
- **DashScope**（help.aliyun.com/zh/model-studio/model-pricing）：paraformer-v2 **0.00008 元/秒（≈0.288 元/时）**+ 每月 36,000 秒（10h）免费（仅北京地域）；paraformer 原生**句级+字级毫秒时间戳**；qwen3-asr-flash 0.00022 元/秒，时间戳仅异步 filetrans 且由 enable_words 控制。
- **Groq**（console.groq.com/docs/rate-limits）：whisper-large-v3-turbo 免费层 20 RPM、2,000 RPD、7,200 audio-s/时、**28,800 audio-s/日（≈8h/日）**、单文件 25MB；OpenAI 兼容，verbose_json 带时间戳。
- 中文口碑：SenseVoice AISHELL-1 CER≈2.94、快 15 倍（arXiv 2407.04051）；Whisper 系中文基线弱（Belle-whisper 微调可再提升 24–65% 反证）。

**结论与推荐**：无时间戳转写→SiliconFlow SenseVoiceSmall（免费+中文最优）；带时间戳免费档→Groq turbo；高精度长视频+时间戳→DashScope paraformer-v2。

**风险与边界**：免费额度分地域/按月发放；Groq 25MB 上限需 ffmpeg 预切分。

## ⑤ 本地 ASR 兜底选型

**实测/证据**：
- funasr SenseVoiceSmall：AISHELL-1 **CER≈2.94**、比 whisper-large-v3 快 15 倍（arXiv 2407.04051）；H100 RTF 169.6x、**CPU 17.2x**，184 中文文件 CER 7.81% 约为 Whisper 一半（funasr.com benchmark）；无时间戳。
- funasr paraformer-zh：**原生字级时间戳**（funasr.com 实战文），中文 CER 口碑强。
- faster-whisper small：ctranslate2 依赖轻（无 torch）、word 级时间戳，但中文 **CER 差约 2.7 倍**（funasr 同音频实测对比）。

**结论与推荐**：`scripts/transcribe_local.py` 以 **funasr paraformer-zh 为主引擎**（中文 CER 低+字级时间戳，与云侧 paraformer-v2 同源可互备），SenseVoiceSmall 作无时间戳快速分支；仅当拒装 torch 才退 faster-whisper small。

**风险与边界**：torch+funasr 体积 GB 级；CPU 转写 3–5 分钟视频按 RTF 17x 约需数十秒，可接受。

## 定案汇总表

| 项 | 定案 | 关键数（可溯源） |
|---|---|---|
| 商品源 | Wikidata Q250/Q7987/Q5290/Q186819/Q64684632，沿用既有 fetch 通道 | 72 件，CC0 |
| spec_schema | manufacturer/image/website/release_year（显示器+高/宽） | 覆盖 22–67% |
| 评论源 | online_shopping_10_cats 筛平板/计算机/手机，不挂商品 | 16,315/62,774 条实测 |
| 辅轨视频 | Pexels 主（key 待申请）+ Commons 补充 | 158 / 1,415 实测 |
| ASR | 云：SiliconFlow 免费→Groq turbo（时间戳）→paraformer-v2（0.288 元/时）；本地：funasr paraformer-zh | CER 2.94 / RTF 17x |

**主轨源录像方案确认**：确认主轨仍为店主自录 3–5 分钟数码产品讲解视频（mp4 ≤200MB）；云 ASR 主选按 Owner 裁决（带时间戳档：Groq turbo / paraformer-v2），本地 funasr 仅脚本级兜底（见上方裁决段——本句为调研原文保留）。Pexels/Commons 仅作辅轨画面素材。

**Owner 裁决（2026-09-16，本刀 closeout 补注）**：刀 93 的候选聚合需要**句级时间戳**——云 ASR 实现主选因此定为 **Groq turbo（免费层大）或 DashScope paraformer-v2**；SiliconFlow SenseVoiceSmall 无时间戳，降级为「纯 transcript 字段快速兜底」用途，不承载候选聚合。

Sources: [Wikidata:Licensing](https://www.wikidata.org/wiki/Wikidata:Licensing)、[ChineseNlpCorpus](https://github.com/SophonPlus/ChineseNlpCorpus)、[Pexels API 文档](https://www.pexels.com/api/documentation/)、[Groq rate limits](https://console.groq.com/docs/rate-limits)、[百炼模型价格](https://help.aliyun.com/zh/model-studio/model-pricing)、[SiliconFlow API 文档](https://api-docs.siliconflow.cn)、[SenseVoice 论文](https://arxiv.org/abs/2407.04051)、[FunASR issue #2027](https://github.com/modelscope/FunASR/issues/2027)、[FunASR 基准](https://www.funasr.com/blog/funasr-vs-whisper-benchmark.html)
