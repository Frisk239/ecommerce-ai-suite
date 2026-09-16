# ADR 0056：内容成片引擎 v1（AI 排版人上市 / 预览+草稿双层 / 红线四条 / 高光模板入口）

- 日期：2026-09-16（第 98b 刀）
- 状态：接受
- 相关：ADR 0014（候选不是资产）、0025（来源种类）、0033（密钥纪律）、0038（素材任务生命周期）、0039（切片真 mp4）、0050/0051（ASR/VLM env 三件与空 key 语义）、0053（洗帧：候选=请求态、人工拣选闸门）、0055（内容套件：质检双闸、配图=增值项、IMGGEN 跳过级）；词条「任务」「质检」「内容成片」；roadmap 第 98b 节（grill 修订版）

## 背景

素材中心已有文案（98）、切片（46/93）、洗帧图（94c），但把它们变成一条可投
放的短视频仍要人在剪辑软件里从零排。运营路径应是：选商品 → 系统自动选材
（该商品已发布的切片/图片/文案要点）+ 模板排时间线 → 产出**预览成片**
（ffmpeg 合成，可立即看）与**剪映草稿工程**（下载后在剪映里精修）→ 人审改
后上传成品或直接认可预览 → 登记回素材中心（material 资产，可被投放引用）。
「AI 排版、人上市」——AI 做的是排版候选，最终进中台的成品经人确认。

## 决定

### 1. AI 排版结果是候选，publish 是人闸门

- `POST /api/video-compose/plan`（body=`{product_id, template: highlight|
  product_intro}`，同步就地执行，预算=ffprobe 秒级 + TTS ≤60s + ffmpeg 合成
  ≤120s）：选材纯函数（`services/video_compose.build_timeline`）按规则挑
  「优先切片（转写含商品名/卖点词）+ 图 2-3 张 + 文案要点 3 条；不足时图+
  文案补足；总时长 15-60s（每素材 3-8s，切片按 ffprobe 实测、超 8s 截前
  8s）」，时间线 `[{type: clip|image|text, asset_id, start, dur, text?}]`
  落 `compose_tasks` 行（planned 态）——**落任务不直接成品**（0014/0053 同
  性质），字节住对象存储 `compose/` 暂存前缀（不是资产）。
- 两模板 v1 够用（roadmap 98b Out：不做多模板 DSL）：高光集锦=切片打头串
  集锦、图/文案收尾补足；商品介绍=文案要点打头、图文交替、切片殿后。
- `POST /{task_id}/publish`（人闸门）：body 可带剪映导出的成品 mp4（≤200MB
  魔数校验，不传=用预览成片）；文案正文=时间线文案要点串联（纯高光时退切
  片转写串联），**双闸复用**（见红线③）过线登记 material 资产（kind=
  material、source_kind=**upload** 服务端定值——publish 时的正文已是人确认
  的成品文本，通道形态等同人工上传；标题 `{商品} · 内容成片`、挂商品）→
  照常机洗推进待人洗、受人洗/发布治理。**不做自动 publish**。
- 成品 mp4 字节只是任务留档（`compose/{run}/final.mp4`，`GET /{task_id}/final`
  可下），**不自动资产化**（Debt）。

### 2. 预览 + 草稿双层产物

- **预览成片**（`GET /{task_id}/preview`，mp4）：ffmpeg 合成 720x1280@25——
  图/文案卡序列相邻叠化 0.4s（叠化吃前一素材的延长尾帧，时间线时序分毫不
  动）、切片段硬拼（直播画面不糊头）、文案要点字幕 drawtext、TTS 口播
  （`tts.mp3` 经 apad/atrim 对齐画面时长——**画面时长权威**）、AIGC 角标
  drawtext 常驻（红线①）。无 TTS key=无音轨，`with_tts=false` + 任务详情
  「TTS 未配置，预览无声」诚实标注（fail-closed 不 fail 任务——口播是增值
  项不是成片本体，与 0055 配图同型跳过级）。
- **剪映草稿**（`GET /{task_id}/draft`，zip）：draft_content.json +
  draft_meta_info.json + `materials/` 媒体字节，schema 复刻剪映 5.9 微秒
  时基（主轨 butt-joint、文本轨字幕+AIGC 常驻文本、audio 轨口播）；素材
  path 用**相对路径**指向包内 materials/——**记偏差：pyJianYingDraft 库可用
  但素材 path 硬绑生成机绝对路径（os.path.abspath）且拖 pymediainfo 原生库
  依赖**，与「服务端生成 zip、操作者下载到自己机器打开」的形态冲突，故按
  施工单的偏差条款用最小自写 JSON 形态（字段按 pyJianYingDraft 实产草稿逐
  一复刻）。操作者把 zip 解压到剪映草稿目录（com.lveditor.draft/）打开精
  修；剪映版本对相对路径不认时用草稿内「媒体重链接」指向 materials/。

### 3. 红线四条

1. **AIGC 水印不可配置关闭**：预览 drawtext 角标「AI 生成」（全程常驻、
   白字半透明黑框）与草稿常驻文本素材都在代码里钉死，**没有任何开关/参数**；
   人在剪映里可移动角标位置，但生成侧恒有（成片是 AI 排版产物必须可识别）。
2. **选材白名单**：只取**已发布 + 未废弃 + 挂本商品**的资产（0004 已发布才
   可引用），本仓资产全部自有来源（source_kind 由登记端点语义定值，无外部
   抓取通道）——白名单天然满足，不另设过滤开关；未发布素材根本不进选材
   查询。
3. **文案双闸复用**：publish 前正文过 material 的规则四条
   （`qc_check`）+ LLM 事实性质检（`run_llm_qc`，98 刀同函数同语义）——
   不过线/LLM 不可用一律 422 不登记（fail-closed），任务停 planned 可改后
   重发。
4. **数字人/声音克隆 Out**：TTS 只做口播配音，voice 用**厂商预置音色**
   （`TTS_VOICE`，请求形状 `{model, input, voice}` 不携带任何参考音频）；
   不做数字人口型、不做音色克隆输入。

### 4. TTS_* env 三件（同 ASR/VLM/IMGGEN 形态）

`TTS_API_KEY / TTS_BASE_URL / TTS_MODEL`（+`TTS_VOICE` 预置音色）：OpenAI
兼容 `POST {base}/audio/speech` `{model, input, voice}` → 音频字节（默认
硅基流动 CosyVoice2；任意兼容端点换 base 即可）。密钥纪律同 0033：空 key=
不建客户端、不发请求、不进日志与异常文案；字节按音频魔数轻复验（mp3/ID3、
帧同步、RIFF、OggS、ftyp），坏字节不冒充口播（ffmpeg 混流会带崩整次合成）。

## 动机

把「素材中心攒下的切片/图/文案」变成「能投放的成片」缺的是排版这一步：
AI 做排版（选材+时间线+粗剪）是确定性的工程活，人做终审（看预览、剪映精
修、确认登记）是治理活。预览让「不用装任何软件就能看」，草稿让「要精修的
人有工程可下」——双层产物对应两种人；publish 双闸+人闸门守住「AI 排的版、
人确认的稿才进中台」的口径，AIGC 角标守住「AI 生成内容可识别」的合规底线。

## 后面

- **Out**：批量成片；自动 publish；多模板 DSL（两模板 v1 够）；数字人/声音
  克隆；成片自动发布到分发渠道（98b roadmap 的「分发」段仍列在途）。
- **Debt**：publish 上传的成品 mp4 不自动资产化（可按 0015/0053 形态登记
  video 资产，待产品裁决）；草稿主轨在文案卡位置留空隙（人在剪映里补背景
  或缩短）；剪映对相对路径的兼容性未在真机验证（Owner 装剪映后补实录）；
  compose/ 暂存字节无生命周期清理（同 material/ 暂存孤儿口径）。
- 测试：`apps/api/tests/test_video_compose.py`（选材纯函数/时长窗/时间线
  形状/滤镜命令断言/草稿 zip 结构/真 ffmpeg 合成与水印像素）、
  `test_video_compose_integration.py`（plan→预览 ffprobe→草稿→publish 双闸
  与登记形状；TTS 三态替身；真 PG）。
