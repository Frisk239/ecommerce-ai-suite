# 第 93 刀 closeout：ASR 刀（云转写候选 + 本地兜底 + 人工拣选闸门）

日期：2026-09-16。分支 `feat/asr-93`（stacked 于 `feat/audit-18`；#132 为平行链另一支，无文件重叠）。

## 交付

1. **`services/asr.py`**：提音轨（ffmpeg 16k mono wav）→ >24MB 分段 + 时间戳偏移合并 → 云转写（OpenAI 兼容 `/audio/transcriptions` + verbose_json segments）→ **停顿聚合纯函数**（gap≥1.2s 断段/累计 20s 强切/上限 60 段保护）→ 落 pending 候选（带 recording_id、transcript、`transcript_source='cloud'`）。
2. **env 三件**：`ASR_API_KEY/ASR_BASE_URL/ASR_MODEL`（默认 Groq whisper-large-v3-turbo；key 空=不建客户端，端点 409 + `GET /asr/status` 供前端）。
3. **迁移 0030**：`transcript_source`（String(10) 无 CHECK，取值 services 常量收口；存量回填 manual——非 ASR 通道含 WANDS 自带；server_default 后非 ASR 插入自动落值）+ `product_id` 放开 NOT NULL（转写候选无商品归属不编造；downgrade 失败口径同 0018）。
4. **端点**：`POST /api/clips/recordings/{id}/transcribe`（操作者鉴权；409 无 key/已有未拣选 cloud 候选；422 无音轨；幂等=拣选后可再生成一批）。
5. **本地兜底** `scripts/transcribe_local.py`（funasr paraformer-zh → transcript_source='local'）+ pyproject 可选组 `asr-local`（**Dockerfile 未动，镜像不含 funasr**）。
6. **前端**：切片页「自动转写」按钮（无 key 禁用+明文提示）+ 候选「转写来源」只读徽章。
7. **文档**：ADR 0050（ASR 口径）+ CONTEXT 词条「转写来源」+ README ASR 段/env + .env.example + compose。
8. **审计 18 P2#1–4 顺手清**：confirm_fields 改 attrs∩类目 schema、candidate_rows 可行动报错、数码行独立 `--digital-limit`、fixup 边界说明。

## 证据

- **Owner 门禁复验**：`--junitxml` 机械计数 **tests=1260, failures=0, errors=0, skipped=0**（基线 1218，+42）；ruff 全过；web lint 7/0、build 绿。
- **浏览器复核**（IAB）：切片页「自动转写」禁用态+提示文案如实；候选「转写来源」徽章（存量 manual）呈现正确。
- **代理替身实跑**（无真云 key，OpenAI 兼容替身 + 真容器/真 ffmpeg/真 HTTP）：TTS 合成 mp4 上传 → 转写 200 `{candidates_created:3}` → DB 三行 pending/cloud/recording_id=4，**时间码 00:00:00-04/05-08/09-12 与 TTS 句间 1.6s 停顿对齐**；重跑 409（带现有条数）；拣选真切出 A-498~500。

## 偏差

1. **无真 ASR key**：真云准确率/耗时未实测（按施工单降级=替身集成 + 无 key 409 实跑）；ADR 已记 Debt，**真云验收待 Owner 配 Groq 免费 key 后补**（接口就绪）。
2. 演示库残留验收物：recording 4、候选 35–40、A-498~500（可按 README 复位命令清理）。

## 评审实修（两轴子代理：无 P0/无硬违规）

- `models.transcript_source` server_default 改 `text("'manual'")`（对齐仓库惯例）；
- realdata README 补 `--digital-limit` 参数行；`load_wands_clips` 两处「product_id 不可空」过时注释订正；删除临时 junitxml 工件。

## 记债

1. 转写端点**并发重跑无 CAS 占位**（两个并发可各生成一批候选）——单操作者 v1 形态低危，拣选路径有 CAS 先例，将来并发场景触发时对齐。
2. 120s 只封单次云请求、多块串行无总预算；前端 150s 先断会出现「报网络失败但候选已落」——真云长录像场景观察项。
3. `split_wav_chunks` 长录像全量驻留内存——规模触发时改流式。

## 后续

第 94a 刀：图片资产活化（图片描述治理字段 + VLM 描述草稿 + 商品素材聚合视图 + spec_schema 补 image/website——审计 18 归位项）。
