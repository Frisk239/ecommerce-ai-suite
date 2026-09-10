# 第 46 刀 closeout：直播切片真链路（`feat/real-clips`）

日期：2026-09-11。规格 `docs/progress/real-clips-intake.md`（Owner 九条裁决）；依据 `docs/roadmap-product-hardening.md` 第 46 刀（第二梯队第二项）。ADR **0047**（部分取代 0039 的「登记字节不是 mp4」边界）；迁移 **0023**。

## 交付

| 件 | 交付 |
| --- | --- |
| 源录像表 | `clip_recordings`（迁移 0023）：`label`（上传文件名）/`object_key`（**`recordings/` 前缀**）/`size_bytes`/`created_at`；`clip_candidates.recording_id` nullable FK。录像是切片模块自有的输入源与溯源锚——**不进检索、不能发布、不进治理台、无 MCP 触点**（裁决 1，与候选同性质：不是中台对象） |
| 上传端点 | `POST /api/clips/recordings`（操作者 cookie 鉴权）：扩展名须 `.mp4`（否则 422）→ ≤200MB（否则 413）→ 非空（422）；落字节后一条 UPDATE 把 **`recording_id IS NULL` 且 `status='pending'`** 的候选绑到这份录像（裁决 2）。已登记候选不动（回执锚已定）、已绑过的候选不改绑（单源模型） |
| 真切 | `services/clips.cut_clip_bytes`：录像字节 → 临时文件 → `ffmpeg -ss <start> -i <in> -t <dur> -c copy -movflags +faststart -y <out>` → 读输出 → 临时目录随 `with` 清理。`-ss` 在 `-i` 之前（输入侧 seek）；120s 超时防畸形输入挂死；失败一律 `ClipCutError` → 路由 422 且**该候选保持 pending 可重拣**（裁决 7，不落半个资产） |
| 对象键扩展名 | `make_object_key(kind, bytes, *, suffix=None)`：显式 `suffix` 优先（真切 mp4 路径 `.mp4`、**无源录像的旧文本路径 `.txt`**），不给则按 kind 兜底（video→mp4、其余→txt）。旧路径**不再**顶着 `.mp4` 键装文本字节 |
| 检索正文改由字段承载 | `register_asset(..., preset_fields=...)`：登记时把候选转写预置为 `transcript` 字段（`source=machine`，`setdefault` 不被机洗覆盖）。`index_chunks_for_version(..., extracted_fields=...)` 对 `kind='video'` **只从 `transcript` 字段取正文切块（confirmed 优先、回落 extracted），永不读对象字节**；其余 kind 照旧读字节。有预置字段且机洗字段集为空时跳过读字节（mp4 二进制不进机洗解码器） |
| 回执锚原子性 | `pick_candidates` **逐候选 commit**：下一个候选真切失败抛 422 中断时，前序候选的 `registered` + `registered_asset_id` 已落库（否则会话关闭回滚会丢锚、资产却已在，重拣即重复登记） |
| 前端 | 切片页：页头「上传源录像」按钮（隐藏 `<input type=file>`）+ 上传后回执（绑定了几条）+「当前源录像」行（取 pending 候选里 `created_at` 最新的那份，多源不同绑时如实显示最新）；拣选回执**按勾选候选的绑定状态分三支**（全真切/混合/全旧路径），不再一律说「已切出真片段」 |
| 部署 | `apps/api/Dockerfile` 装 `ffmpeg`（slim 基础镜像不带；CI 的 ubuntu-latest 自带，workflow 未改） |

## 验收（端到端，真浏览器 + 演示库）

按 intake 的七条验收逐条走（浏览器 playwright-cli 驱动 `http://localhost:5173`，后端 compose 栈）：

1. **上传**：切片页点「上传源录像」→ 文件选择器选 30 分钟真 mp4（5.1MB，`testsrc` 生成）→ 回执「已上传源录像《acceptance-30min.mp4》（5.1 MB），**已绑定 30 条待拣候选**；勾选后拣选即从源录像切出真 mp4 片段。」
2. **绑定**：上传前库里 30 条 pending 全部 `recording_id IS NULL`（为演示这条路径，先在演示库把 pending 行的绑定解掉一次）；上传后全部指向新录像；顶部「另有 N 条待拣候选尚无源录像」提示随之消失。4 条已登记候选的绑定未被动过。
3. **真切**：勾选 C-0004（00:16:48-00:17:35）与 C-0006（00:00:40-00:01:19）→「拣选登记（2 条）」→ 回执「已登记 2 条：**均已从源录像切出真 mp4 片段**，转写已预置为可检索字段（待人洗）。」库内核对：
   - 资产 **264**：键 `clips/684a547ed7c14b4180b513c767b7f621/a4a57b2af3b6fa08.mp4`、字节头 `ftypisom`、**ffprobe 时长 47.000s（= 00:16:48→00:17:35 整）**、`extracted_fields = {"transcript": {value: 转写, source: machine}}`；
   - 资产 **265**：键 `.../5ede105ebda7a97f.mp4`、`ftypisom`、**时长 39.000s（= 00:00:40→00:01:19 整）**、同样预置 transcript 字段。
4. **旧路径**：解掉 C-0020 的绑定（页面顶部随即显示「另有 1 条待拣候选尚无源录像」）→ 拣选 → 回执「已登记 1 条：**本次无源录像，仍写时间码转写文本**（待人洗）」。资产 **266**：键 `.../18df53d25d97bca7.txt`（扩展名与字节一致）、字节 `[00:10:00-00:10:39] 顾客问 iittala bowl…`、transcript 字段照常预置。
5. **检索**：浏览器里把资产 264 走完发布（确认对话框：「该资产挂了商品，但当前没有可写回的规格字段值」）→ 已发布；`retrieval_chunks` 里资产 264 只有一条块、`seq=0`、内容=**转写原文**（不是 mp4 二进制、发布未因二进制报错）。
6. **门禁**：集成 **804 → 816 passed / 0 failed / 0 skipped**；ruff 全过；前端 build 绿、lint **7/0** 与 main 基线一致。
7. **检索评测**：本刀动了检索侧一处（video 正文源），按纪律复跑大集——**65.0/75.0 逐位零漂移**，且零漂是**结构性**的（大集 47 条引用资产全是 document/dialogue/material，无 video），已补进 `docs/research/rag-eval-report.md`，并把真实回归保护指到 `test_published_video_chunks_come_from_transcript_field` 与新增的纯单测 `test_index_chunks_video_body_comes_from_transcript_field`（用「读字节即炸」的假存储，无 ffmpeg 的环境也有回归网）。
8. **端到端**：见上 1–5 条（治理台详情页 A-0264 显示「已发布 / 视频 / 切片拣选 / 所挂商品 瓶装水」，发布后块表可查）。

后端钉测 `test_real_clips_integration.py` 7 例（真 mp4 fixture 由 ffmpeg 现造）：上传契约（扩展名 422 / 空文件 422 / 未登录 401 / 201 落 `recordings/` 前缀并记 size）/ 只绑「无源录像的 pending」（已登记与已绑定都不动）/ 真切 mp4（键 `.mp4` + `ftyp` 魔数 + transcript 字段 + 候选转 registered）/ 旧路径仍写时间码文本 / **切失败 422 且该候选仍 pending、前序候选已 registered** / **发布后切块来自 transcript 字段** / 种子候选仍可用。既有 `test_clips.py` 扩为「前缀 + 扩展名」双断言；`test_mcp.py` 加两条 `read_version_text` 回落钉测；`test_retrieval.py` 加三条 video 正文源纯单测。

## 评审处置（两轴独立只读子代理：规格符合性 / 工程健壮性）

评审报 **1 个 P0 + 3 个 P1**，全部实修并复核；7 条 P2 里顺手修 3 条、记债 4 条。

### P0-1 已发布切片会让 MCP 全量导出整体报错（本刀引入的真回归）

`read_version_text`（MCP `get_asset` / `export_published` / 版本正文端点共用）直接 `decode("utf-8")`——真切出的 mp4 字节必然解不出。于是**库里只要有一份已发布切片**：`export_published` 在列表推导里抛 `UnicodeDecodeError` 整体失败、`get_asset` 报错、版本正文端点 409。我在切块处做了「video 不读字节」，但没覆盖**其余正文出口**——这是本刀真正危险的那一类漏。

修法：`read_version_text` 解不出文本时**只对 video** 回落 `transcript` 字段（confirmed 优先、extracted 次之）；旧路径字节本就是文本、照常返回形态不变；别的种类非 UTF-8 仍是 409（不拿字段掩盖坏字节）。复核（改造后的 api 容器 + 演示库，库里有已发布的真 mp4 资产 264）：

- `GET /api/assets/264/versions/1/text` → **200 + 转写原文**（修前 409）；
- MCP `get_asset(264)` → 200；
- MCP `export_published()` → **200 / isError=false / 56 个已发布块**，其中资产 264 的 `content` 是转写原文（修前这一份资产会带崩整次导出）。

### P1 三条

- **P1-1 ffmpeg 跑在 DB 事务里**：真切前那次校验读已 autobegin 一个事务，`subprocess.run` 最长 120s 都在事务内（idle-in-transaction 占池连接，正是仓库 P1#2 纪律禁止的形态）。修法：循环每轮**先 `db.commit()` 再切**，ffmpeg 全程无事务；顺带把逐候选 `db.get(ClipRecording)` 收敛成一次 IN 批取（写路径 N+1）。
- **P1-2 上传先全量读入再判上限**：`await file.read()` 之后才比 200MB——上限形同虚设，超大文件先吃满内存/临时盘才被 413。修法：`await file.read(MAX_RECORDING_BYTES + 1)`，多读 1 字节即可判定超限。
- **P1-3 治理台「上传新正文」对 video 仍开放**：对真 mp4 切片换正文会（a）用 `make_object_key("video", 文本)` 产出 `.mp4` 键装文本（键与字节不一致）、（b）video 机洗字段集恒空 → `extracted_fields` 被重算成空集，**预置的 transcript 被静默抹掉**、检索块归零。修法：`PUT /assets/{id}/versions/{n}/bytes` 对 video 直接 409（文案指路切片页重拣）+ 前端同步不出该按钮。复核：video 资产 265 → 409；对照 document 资产 259 → 200（未被误伤）。

P2 顺手修三条：`latestRecording` 只看**待拣**候选（原来把已登记候选的录像也当「当前源录像」并配文「拣选将从中切出真片段」）、`test_upload_binds_only_unbound_pending` 自造前置录像（不再依赖用例执行顺序）、video 切块加「读字节即炸」的纯单测。记债四条（见 ADR 0047 Debt 段）：transcript 被确认时正文块与字段块重复、`preset_fields` 未过 `redact`、录像整份进内存、`-ss` 越界无 ffprobe 硬闸。

**订正（实现期实测）**：intake 裁决 6 原写「时间码越界 = ffmpeg 失败」**不成立**——`-ss` 越过 EOF 时 ffmpeg seek 到末关键帧照样产出文件。硬闸越界需 ffprobe 时长校验，明确留 Out，ADR 0047 与 intake 均已如实标注。当前硬闸四类：时间码形状非法 / 时长为非正 / ffmpeg 非 0 退出 / 无输出。

## 诚实披露

- **验收动过演示库**：为了让「上传即绑定」这条路径在浏览器里可见，我在演示库把 30 条 pending 候选的 `recording_id` 解绑了一次（`UPDATE ... SET recording_id=NULL WHERE status='pending'`）——这是把库恢复成「全新安装」的种子态（新库里候选本来就没有源录像），不是代码路径。4 条已登记候选的绑定全程未动。另外演示库多了 1 份源录像（`acceptance-30min.mp4`）、3 条拣选出的视频资产，其中 **A-0264 已发布**（发布是验收第 5 条的必经步骤，也是 P0-1 复核的前提——库里得有一份真 mp4 的已发布资产才测得出导出会不会炸）。
- **没有 ASR**：转写仍来自种子候选的 mock 文本（`source_video_label` 是名称字符串）。「真 mp4 + 真转写」不是本刀产物——本刀只把**字节**做实了，ADR 0047 的 Out 列明了 ASR。
- **切点吸附关键帧**：`-c copy` 不重编码，切点对齐到最近关键帧——实测这份测试片恰好逐秒对齐（47.000s/39.000s 整），但**一般素材会有半秒级偏移**，不是精度承诺。
- **ffmpeg 是外部进程依赖**：容器（Dockerfile 装）与 CI（ubuntu-latest 自带）都有；本地 Windows 裸跑 API 需自备。缺失时报 `ffmpeg 无法执行`，不静默降级。
- **源录像只进不出**：没有删除/替换/下载端点的同时，也没有列表端点——「当前源录像」是从候选的 `recording` 字段反推的（多源不同绑时会显示最新那份，这是刻意的简化，单源模型）。
- 端到端证据是**本会话实测输出**（上列数字与字节头均为命令回显），未落成截图/脚本文件。

## 后续

- **下一刀=第 47 刀 可观测最小版**（`prometheus-fastapi-instrumentator` RED 指标 + 三个自定义 + structlog JSON + correlation-id + `/metrics`），之后 48 刀 CSAT + 反馈闭环。
- 仍待 Owner 裁决（审计刀 8 记债，四条产品面缺口）：商品价只覆盖 3/115、多来源数据在产品面不可见、工作队列首屏 183 条原始灌入、widget 不落宿主 origin。**建议在第 47 刀前后插一刀清 #1/#2**（两处都是「演示库看着像没做完」的观感问题）。
