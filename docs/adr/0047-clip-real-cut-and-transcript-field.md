# ADR 0047：切片真链路（源录像非资产 / ffmpeg 真切 mp4 / video 正文改由转写字段承载）

- 日期：2026-09-11（第 46 刀）
- 状态：接受
- 相关：ADR 0003（每版一把键）、0014（候选不是资产）、0015（只登记资产不造任务）、0025（来源种类）、0038（字节不动、出口必掩）、**0039（本 ADR 部分取代：登记字节「非 mp4」的边界到此为止）**；词条「切片候选」
- 取代范围：0039 的「登记字节=带时间码转写文本（**不是 mp4**）、真视频切出留部署刀」——本 ADR 把真 mp4 切出落地，旧路径保留为无源录像时的回落。0039 的其余决定（候选是种子 mock 不是中台对象、只登记资产不造任务、单向状态机、字段集为空）**全部继续有效**。

## 背景

0039 把切片 v1 的边界记成「时间码文本冒充视频字节」：`clips/<uuid>/<sha16>.txt` 里放的是 `[00:02:14-00:02:52] 现场实测保温…`。roadmap 第 46 刀要求演示故事升级为「真 mp4 切出真片段」。三处硬约束让这不是一次孤立替换：

1. **对象键扩展名此前硬编码 `.txt`**（`make_object_key`）——字节换成 mp4 后，键名与实际内容不符（浏览器/CDN 靠扩展名给 Content-Type）。
2. **检索切块读的就是这份字节**（`index_chunks_for_version` → `read_index_text`）——字节变 mp4 后读回是二进制，切块路径必然坏。
3. **没有源录像就没有可切的东西**——`clip_candidates.source_video_label` 只是名称字符串（mock 语境），仓库里从不存录像字节。

## 决定

### 1. 新表 `clip_recordings`（迁移 0023）——源录像不是中台对象

| 列 | 说明 |
| --- | --- |
| `id` PK | |
| `label` | 上传文件名（展示用，截 200） |
| `object_key` | `recordings/<uuid>/<sha16>.mp4`（**不复用 clips/ 资产前缀**） |
| `size_bytes` / `created_at` | |

`clip_candidates.recording_id` nullable FK 指向它。录像是**切片模块自有的输入源与溯源锚**：不进检索、不能发布、不进治理台、无 MCP 触点、无资产语义列——与候选本身（0014）同一性质，不是新中台对象。

### 2. 上传即绑定：只管「尚无源录像」的待拣候选

`POST /api/clips/recordings` 落字节后，一条 UPDATE 把 `recording_id IS NULL` 且 `status='pending'` 的候选绑到这份录像。已登记候选不动（回执锚已定），已绑过的候选不改绑——**本刀是单源模型**（多源按候选分别绑定留 Out）。前端把「被框住」的候选显示为「当前源录像」（取 pending 候选里 `created_at` 最新的那份）。

### 3. 拣选真切：`ffmpeg -ss <start> -i <源录像> -t <dur> -c copy -movflags +faststart`

录像字节写临时文件 → 切 → 读输出 → 临时目录随 with 清理。`-ss` 在 `-i` 之前（输入侧 seek，不解码到切点）；`-c copy` 秒级不重编码（切点吸附关键帧可接受）。**切失败 → 422 且该候选保持 pending**（裁决 7，不落半个资产），前序已登记候选保留（逐候选 commit 收口回执锚）。

**订正（实现期实测）**：intake 原写「时间码越界就是 ffmpeg 失败」不成立——`-ss` 越过 EOF 时 ffmpeg seek 到末关键帧照样产出文件。硬闸越界需 ffprobe 时长校验，明确留 Out；当前只硬闸「时间码形状非法 / 时长为非正 / ffmpeg 非 0 退出 / 无输出」四类。

### 4. 对象键扩展名跟实际字节走（修正 0003 的「固定 .txt」）

`make_object_key(kind, bytes, *, suffix=None)`：`suffix` 显式给定时以它为准（真切 mp4 路径传 `.mp4`、**无源录像的旧文本路径传 `.txt`**）；不给则按 kind 兜底（video→mp4，其余→txt）。**键必须与字节一致**——这是本 ADR 取代 0003 中「扩展名固定 .txt」那句的唯一一处。

### 5. video 正文改由 `transcript` 字段承载（本刀最关键）

登记视频资产时**预置 `transcript` 字段**（`source=machine`，值=候选转写）；`index_chunks_for_version` 对 `kind='video'` **只用该字段作正文切块、永不读对象字节**（confirmed 优先，回落 extracted）。字段缺失/为空即正文为空，照常可发布（视频资产无必填字段闸）。

**其余正文出口同口径（第 46 刀评审 P0 收口）**：`read_version_text`（MCP `get_asset` / `export_published` / 版本正文端点共用）对 video 的真 mp4 字节解不出文本时，同样回落 `transcript` 字段。理由：只在切块处收口不够——库里有一份已发布切片，MCP 全量导出就会整体报错，这是切块修复盖不住的**出口面**。回落**只在 video 且字节解不出文本时发生**：旧路径字节本就是时间码文本、照旧返回（形态不变），别的种类非 UTF-8 仍是真错误（409），不拿字段掩盖坏字节。

**换正文对 video 关闭**：`PUT /assets/{id}/versions/{n}/bytes` 对 video 资产直接 409（前端同步不出该按钮）。理由两条：①video 的对象键按 kind 走 `.mp4`，而换正文上传的是文本——键与字节不一致；②video 机洗字段集恒空，重算会把预置的 `transcript` 覆盖成空集，检索块静默归零。要换片段就回切片页重拣。

- **两条路径都预置字段**（真 mp4 与旧文本路径统一口径）：旧路径的检索能力因此不回归，真路径也不至于拿二进制去切块。
- **不用「字段缺失就回落读字节」**：那会留一条静默乱码/409 的路（旧文本资产能读、真 mp4 资产不能读），同一 kind 两种行为更危险。宁可正文为空。
- video 的**机洗字段集仍为空**（0039）——口语转写跑规格正则会误抽。有预置字段且机洗字段集为空时，`register_asset` 跳过读字节（预置字段即这批资产的机洗成果），照常推进待人洗。

### 6. 检索正文源变化不改引用语义

切块文本变了，但引用锚不变（0007：引用 = 资产 ID + 版本号）。`transcript` 字段照常走既有字段/写回机制，无新引用形态。

## 动机

roadmap v3 第二梯队第二项。演示故事「时间码文本冒充视频」是审计刀 2 起在案的纸糊点；本刀把它落成真字节，同时把「视频资产的可检索文本从哪来」这个问题一次性定死（字段而非字节）。

## 后面

- **Out**：自动切出（按 ASR/场景边界）、真 ASR（faster-whisper）、重编码、播放器、多源录像按候选绑定、录像删除/替换、ffprobe 时长硬闸。
- **Debt**：`ffmpeg` 是外部进程依赖（Dockerfile 装、CI ubuntu-latest 自带；本地 Windows 需自备）——缺失时切段报 `ffmpeg 无法执行`，不是静默降级。
- **Debt**：操作者若确认了 video 的 `transcript` 字段（今天不可达：video 合法字段集为空），切块会同时产出正文块与 `transcript：…` 字段块，同一文本进索引两次（词法打分轻微偏置）。
- **Debt**：`preset_fields` 直接落 `{value, source}`，未过 `redact`（来源是候选转写 mock 文本；其余出口照旧打码兜底）。
- 依赖 video 正文的检索评测在字段口径下不回归（既有集成测试 `test_published_video_chunks_come_from_transcript_field` 钉死；纯单测 `test_index_chunks_video_body_comes_from_transcript_field` 用「读字节即炸」的假存储保证无 ffmpeg 环境也有回归网）。
