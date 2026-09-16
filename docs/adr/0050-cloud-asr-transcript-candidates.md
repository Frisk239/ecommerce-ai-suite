# ADR 0050：ASR 转写口径（云候选 + 人工拣选闸门 / 本地仅脚本兜底 / 转写来源只读）

- 日期：2026-09-16（第 93 刀）
- 状态：接受
- 相关：ADR 0014（候选不是资产）、0015（只登记资产不造任务）、0038（字节不动、出口必掩）、0039（候选 v1 时间码文本）、0047（切片真链路：源录像非资产 / video 正文走 transcript 字段）、0033（密钥只经 env、空凭证降级）；词条「切片候选」「转写来源」「源录像」；调研 `docs/research/real-store-data-sources.md` §④⑤；迁移 0030

## 背景

0039/0047 以来候选转写只有两条来路：种子 mock 文本、WANDS 导入自带文本（都是「人工/数据通道」），操作者手填也只是改库。roadmap 第四阶段第 93 刀把「自动转写」升级为产品路径：真实自录直播录像上传后，操作者点一下就该出**带时间码的候选**——而候选质量决定切片质量（拣选闸门保留：自动产出候选，人决定切片）。

选型调研（89 刀）给了两条硬事实：①带**句级时间戳**的云免费档是 Groq `whisper-large-v3-turbo`（25MB/文件上限，`verbose_json` 出 segments），DashScope paraformer-v2 同形可换；②本地 funasr paraformer-zh 中文 CER 低、CPU RTF ~17x，但依赖 torch（GB 级）——**只配当脚本级兜底，不进服务镜像**。

## 决定

### 1. 云优先：OpenAI 兼容 `/audio/transcriptions`，env 三件套

`ASR_API_KEY` / `ASR_BASE_URL`（默认 `https://api.groq.com/openai/v1`，Groq turbo 免费层大） / `ASR_MODEL`（默认 `whisper-large-v3-turbo`）。请求 `response_format=verbose_json` 取 `segments[].{start,end,text}`（句级时间戳）。密钥纪律同 0033/LLM：只从 `.env` 读、不入库、不进日志与异常文案。

**空 key = 不建客户端、不发请求**：端点 409（「ASR 未配置」）而不是静默降级或假成功——无 key 环境人工填转写、本地脚本兜底的现状不变（fail-closed 的诚实拒绝）。

**没回句级时间戳 = 失败（502），不编时间码**：没有时间戳就聚合不出候选；只回全文的网关（如 SenseVoiceSmall 免费档）不承载候选聚合——它能做的是「纯 transcript 字段快速兜底」，不是本刀路径。

### 2. 提音轨与切段：ffmpeg 16kHz 单声道 + 按 24MB/10 分钟切 PCM

整段 mp4 直送既超单文件上限又白花带宽：`ffmpeg -vn -ac 1 -ar 16000` 抽 wav。超过 24MB（Groq 25MB 上限留余量）按 10 分钟切块——wav 是裸 PCM，**按样本对齐切片、零重编码**，块起始时间随块返回、结果按偏移合并回源录像时间轴。无音轨/提取失败 = 422（不静默回空候选）。

**同步执行**：路由是同步 def（跑线程池），ffmpeg 与云请求都是阻塞调用，不占事件循环；云请求超时 120s、0 重试（失败可再点一次，重试只把操作者等待翻倍）。契约：`POST /api/clips/recordings/{id}/transcribe` → `{candidates_created, segments, duration_ms, note}`（`segments` = 聚合前句数；`note` 如上上限合并/未识别到语音的如实说明）。

### 3. 停顿聚合是**纯函数**（本刀的产品语义核心）

`services/asr.py::aggregate_segments`：句间静默 **gap ≥1.2s 断段**（停顿=一段话说完的天然边界）；段累计时长 **≥20s 后下一句强切**（无停顿长独白不糊成一条候选）；**候选段数 ≤60/录像**——超出把相邻段按序均摊合并到 60 条，合并条数进回执（`note` 如实说明），**不静默截断**（截断=丢句子）。每候选 = `{start, end, transcript(句文本连接，中文不插空)}`，时间码 `HH:MM:SS`（起点截断、终点向上取整且恒 ≥ 起点+1s——亚秒短句若 start==end，拣选时 ffmpeg 会因「切段时长必须为正」422）。

参数是产品语义（改常量即改行为），单测逐值钉死（边界两侧都钉）。

### 4. 落库形状：pending 候选 + 直接绑 recording_id + `transcript_source`

- `status=pending`：**人工拣选闸门保留**（本刀不做自动拣选；「自动候选 + 人工确认」是第四次复用，见 94c 洗帧）。
- **直接带 `recording_id`**：转写候选的源就是这份录像。第 46 刀「上传即绑」只圈 `recording_id IS NULL` 的候选——转写候选因此不会被后续上传误绑；第 49 刀改绑端点仍可把它们改走（pending 才可改）。
- `product_id` **缺省为空**（0030 放开 NOT NULL）：句子里没有商品归属——归属是人/治理动作，不编造。请求体可带 `product_id` 给「整段只讲一件商品」的场景显式归属；未归属候选拣选出的资产 `product_id` 为空。
- `transcript_source`（0030，String(10) 无 CHECK，取值由 `services/asr` 常量收口）：`cloud` / `local` / `manual`。**来源是既成事实，只读**——没有改来源的端点（可改就成可造假的溯源），也不参与拣选/发布闸门。

**幂等口径**：该录像已有**未拣选**的转写候选时重跑 = 409（detail 带现有条数），**不是追加**（避免重复堆候选）；全部拣选/登记后可再生成一批（重跑受控，产物可重来）。

### 5. 本地兜底只做脚本，依赖进可选组（绝不进 api 运行时/镜像）

`scripts/transcribe_local.py`：funasr **paraformer-zh**（中文 CER 低 + 原生时间戳，与云侧 paraformer-v2 同源互备）+ fsmn-vad/ct-punc，`sentence_timestamp=True` 出句级 start/end（毫秒→秒），喂**同一个** `aggregate_segments`——本地与云的候选口径不会漂。依赖声明在 `apps/api` 的 `[project.optional-dependencies] asr-local`；Dockerfile 的 `uv sync --frozen --no-dev` 不带 `--extra`，装不到它（torch 是 GB 级，且 CPU 转写数十秒，塞进服务进程会同时砸掉镜像体积与「同步 ≤120s」两条纪律）。

直连库（`--db`）绕开端点：本机跑转写时 api 未必起，也不该为一次兜底给服务进程装 torch。落值判据与云端点同源（同一聚合函数 + `create_candidates`），只把来源标 `local`。

## 动机

「15 分钟切 20 片」的故事此前无实现（product-gap-analysis：切片=纸糊）。本刀把候选从「人填/导入文本」升级为「真录像自动转写」，同时**不动**三件已有的治理事实：候选不是资产（0014）、拣选才切字节（0015/0047）、发布权在人（0005）。自动只做到「候选」，切片质量仍由人守。

## 后面

- **Out**：自动拣选/自动切出（拣选闸门是治理故事的一部分）；说话人分离/多轨混音/重编码；录像删除替换；云 ASR 用量配额与计费；跨块句子的时间戳平滑（块边界处按偏移合并，句子不会被切开——切点落在 PCM 样本上，不落在 ASR 段上）。
- **Debt**：真云验收需要 `ASR_API_KEY`（仓库不带；closeout 记降级口径——无 key 时以 mock 集成测试 + 无 key 409 实跑替代，真云由 Owner 带 key 补）。
- **Debt**：`_segments_of` 只认 `segments` 字段（OpenAI verbose_json 形状）；换只回 `words`/`chunks` 的网关要加适配层（本刀不做）。
- **Debt**：多块请求是串行发（每块一次 POST）：5 分钟以下录像只有一块，长录像按 10 分钟一块线性增长；并发上送留后续刀（受云端 RPM 限制，串行更保守）。
- 测试：`apps/api/tests/test_asr.py`（聚合/切段/客户端契约，离线）、`test_asr_integration.py`（真 ffmpeg 提音轨 + 替身云响应，钉 409/422/502/成功/幂等/不误绑）、`test_migration_backfill.py::test_upgrade_0030_*`（存量回填 manual + product_id 放开）。
