# 第 46 刀规格：直播切片真链路（intake + Owner 裁决）

日期：2026-09-11。分支 `feat/real-clips`（基于 main `0333e9a`）。依据：`docs/roadmap-product-hardening.md` 第 46 刀（第二梯队第二项）。

## 痛点

切片登记出的「视频资产」字节其实是**一段文本**（`[00:02:14-00:02:52] 现场实测保温…`，ADR 0039 自陈「非 mp4」）——演示故事停在「时间码文本冒充视频」。本刀让它变成**真 mp4 切出真片段**。

## 现状（实测）

- `clip_candidates`（ADR 0014/0039）：`timecode_start/end`（HH:MM:SS 串）、`transcript`、`source_video_label`（**只是名称字符串，不存录像字节**）、`registered_asset_id`。
- 拣选（`services/clips.py:pick_candidates`）→ `register_asset(kind="video", content_bytes=transcript_bytes(...))`；对象键前缀 `clips/` 但**扩展名硬编码 `.txt`**（`registration.py:46-58`）。
- **检索切块读的就是这份字节**（`retrieval.index_chunks_for_version` → `read_index_text(storage, object_key)` + `chunk_text(text, kind)`）——所以「把字节换成 mp4」不是孤立改动：**切块文本源必须同时改**，否则二进制进切块路径。

## 裁决

| # | 裁决 | 理由 |
| --- | --- | --- |
| 1 | 新增 `clip_recordings`（id/label/object_key/size_bytes/created_at）+ `clip_candidates.recording_id` nullable FK（迁移 **0023**）；**源录像不是资产、不进检索** | 词条「切片候选」不变；源录像需要身份与溯源（谁传的、多大、什么时候），但不是中台对象 |
| 2 | **上传即绑定**：上传时把「尚无源录像」（`recording_id IS NULL`）的 **pending** 候选绑到这份录像上 | 种子候选的 `source_video_label` 是 mock 字符串，真实上传把源录像补上；已登记候选不动（回执锚已在） |
| 3 | 拣选**切真字节**：`ffmpeg -ss <start> -i <源录像> -t <时长> -c copy -movflags +faststart`（临时文件 → 读字节 → 清理）；**无源录像退回既有时间码文本字节**（旧路径保留并测） | 演示从「文本冒充」升为真 mp4；旧路径不删（既有测试与历史资产继续成立） |
| 4 | 对象键扩展名**按 kind 分派**：`video` → `clips/<uuid>/<sha16>.mp4`（此前硬编码 `.txt`） | 键必须与字节一致（浏览器/CDN 靠扩展名给 Content-Type） |
| 5 | **检索文本源改由字段承载**（本刀最关键）：登记视频资产时**预置 `transcript` 字段**（`source=machine`，值=候选转写）；`index_chunks_for_version` 对 `kind='video'` **用该字段作正文切块、不读字节**；其余 kind 照旧读字节 | 字节变 mp4 后读字节切块会拿到二进制；转写是这批资产的真实可检索文本（roadmap 明写「转写字段保留」） |
| 6 | 不做 ASR、不做 ffprobe 时长校验、不重编码 | 转写仍来自既有候选（ASR 另刀）；`-c copy` 秒级、切点吸附关键帧可接受（重编码留 Out）。**订正（实现期实测）**：原写「时间码越界就是 ffmpeg 失败」**不成立**——`-ss` 越过 EOF 时 ffmpeg 会 seek 到末关键帧照样产出文件；硬闸越界需 ffprobe 时长校验，明确留 Out（closeout 如实记录） |
| 7 | 切失败 → **422 + 候选保持 pending**（可重拣），不落半个资产 | 与既有「拒绝原子性」口径一致（校验/失败在写字节之前） |
| 8 | Dockerfile 装 `ffmpeg`；CI 的 ubuntu-latest 自带，无需改 workflow | 容器内实测没有 ffmpeg；CI runner 有 |
| 9 | 前端：切片页加「上传源录像」（.mp4，≤200MB）+ 显示当前源录像；拣选回执说明「已切出真片段」 | 没有入口这个功能就不存在 |

## 验收

1. **上传**：`.mp4` → 201（落 `recordings/` 前缀、记 size/label）；非 mp4 扩展名 → 422；超 200MB → 413；未登录 → 401。
2. **绑定**：上传后，pending 候选的 `recording_id` 指向该录像；已登记候选不变。
3. **真切**：拣选落在录像时间窗内的候选 → 资产字节**是 mp4**（`ftyp` 魔数）+ 对象键 `.mp4`；`transcript` 字段在；候选 → registered。
4. **旧路径**：没有源录像时拣选 → 仍写时间码文本字节（既有行为不回归）。
5. **检索**：视频资产发布后，切块来自 `transcript` 字段（发布事务不读 mp4 字节、不因二进制报错）。
6. **门禁**：后端全量绿（含既有切片测试）、ruff 净；前端 build 绿、lint 7/0。
7. **端到端（playwright）**：切片页上传测试 mp4 → 显示源录像 → 勾选候选拣选 → 回执含「真片段」→ 治理台能看到该视频资产。

## Out

- 自动切片（按 ASR/场景边界）、真 ASR（faster-whisper）、重编码、播放器、多源录像按候选绑定（本刀是「上传即绑待拣候选」的单源模型）、录像删除/替换。
