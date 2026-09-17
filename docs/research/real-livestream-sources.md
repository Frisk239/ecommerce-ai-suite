# 真实直播切片素材调研与抓取（第 112B 刀）

日期：2026-09-17。探针环境：本机代理 `127.0.0.1:7890`；Wikimedia Commons API /
Internet Archive / NASA images-api 均免 key；Pexels 无 key（网页直取 CDN 抽查）。
目的：给「上传→ASR 转写→拣选→真切→发布→洗帧」全链一段**真实产品讲解视频**
（有语音、有商品画面、许可可指），替掉演示库的测试素材
（`.slice-test.mp4` / `acceptance-30min.mp4` / `live93.mp4`=TTS 合成）。

结论先讲：**找到并已下载 1 条合格素材**——Wikimedia Commons 的 Dell 34 吋曲面
显示器「开箱 + 装配」实拍，CC BY 4.0，4:22，英文口播逐句讲解参数与安装。成品
`out/112/real-demo.mp4`（854×480 H.264/AAC，13.6MB）。本仓库 ASR 服务实测：
提音轨 0.3s、转写 6.2s、49 句级段 → **12 条 pending 候选**；VLM 复验 4 帧均是
真实商品画面。**只备文件、未灌演示库**（灌库时机由 Owner 定，§5 命令清单）。

---

## 1. 搜了什么（逐源原始结果）

### ① Wikimedia Commons（主产，免 key）

检索式与命中（`action=query&list=search&srnamespace=6`）：

| 检索式 | 命中 | 可用度 |
|---|---|---|
| `filetype:video unboxing` | 20 条抽检，11 条带音轨 | **最有效**——真人口播开箱 |
| `filetype:video product demonstration` | 20 条 | 差：Demo=游行/航天/软件演示，无商品讲解 |
| `filetype:video product review` | 15 条 | 差：动画、动物、游戏 |
| `filetype:video keyboard/mouse/headphones/monitor review` | 各 15 条 | 差：与商品无关（动物/音乐会/论文视频） |

**免下载判别法**（本刀定式，可复用）：
`prop=imageinfo&iiprop=metadata` 的 getID3 元数据里出现顶层 `audio` 键=有音轨
（webm/ogg 两种元数据形态都已兼容解析）；`iiprop=size` 拿体积；
`prop=videoinfo&viprop=derivatives` 拿时长与官方转码档（240p/360p/480p/1080p）。

候选表（全部已核到文件级：时长 + 音轨 + 许可）：

| 文件 | 时长 | 音轨 | 许可 | 原始体积 | 说明 |
|---|---|---|---|---|---|
| **Dell S3423DWC 34-Inch Curved Monitor - Unboxing + Assembly** | 262.5s | 有 | **CC BY 4.0** | 407MB（4K） | **选中**，英文口播，商品=显示器 |
| STEAM CONTROLLER UNBOXING!!! | 234.3s | 有 | CC BY 3.0 | 30MB | 备选，手柄开箱 |
| Unboxing Action Cam Apeman A66 | 173.1s | 有 | CC BY 3.0 | 46MB | 备选，意大利语 |
| Unboxing-Video einer AMD-CPU (MoreThanTech) | 74.7s | 有 | CC BY 3.0 | 68MB | 备选，德语，偏短 |
| Unboxing Ezviz S5 Action Camera | 294.8s | 有 | CC BY 3.0 | 94MB | 备选 |
| Giveaway Unboxing Mini HD action camera | 46.9s | 有 | CC BY 3.0 | 19MB | 偏短，印地语 |

### ② Internet Archive（免 key）

- `advancedsearch.php?q=collection:prelinger AND mediatype:movies`：Prelinger 合集
  **PD 明确**（`licenseurl=http://creativecommons.org/licenses/publicdomain/`），但商品
  广告都以**合集**形态存在：`ClassicT1948`（Classic Television Commercials Part I）
  536.5s / 55.8MB；`Televisi1960`（Television Commercials 1950s-1960s）1515s /
  157MB——**均超 1–5 分钟上限，需人工裁剪**；`Studebaker_Promo`（销售培训片）571s
  且无 licenseurl。
- `q=product review/demonstration` 命中的 `mirrortube` / `social-media-video` 条目
  （如 `funny-frisch-frit-sticks` 73.6s、`orangina-small-bottle` 79.9s）**无
  rights/licenseurl 字段**（YouTube 镜像搬运）→ 许可不可指，**不可用**。
- 结论：IA 作备选可用（PD 稳、量大），本刀不选：要裁剪 + 年代久远 + 推销腔浓。

### ③ Pexels（Pexels License；未申请 key）

网页直取 CDN 抽查 5 条（`videos.pexels.com/.../sd_640_360.mp4` + ffprobe + ASR）：

- `product review` 检索前 2 条：**无 audio 流**（只有 video/data）；
- `unboxing` 检索 2 条：有 audio 流，但时长 **6.3s / 9.8s**，ASR 只回
  「Thank you.」（音乐/环境音上的幻觉）→ 无讲解语音；
- `talking to camera` 检索 1 条：5.2s，无音轨。

结论：**本类目无「带讲解语音 + 1–5 分钟」素材**，与 89 刀结论一致；Pexels 继续
只作「无声辅轨画面」源（许可最宽但需逐条自核音轨，且未申请 key 时无法批量筛）。

### ④ NASA / Blender / 教育平台

- NASA images-api（免 key）：有讲解音轨（mp4a/48kHz）+ 官方 `.srt` 字幕，PD 安全；
  但题材是航天（如 `NHQ_2019_1015_Introducing Artemis Generation Spacesuits`，
  `~medium.mp4` 档），非商品讲解 → 列备选。
- Blender 开源电影（CC BY）：有配音，非产品讲解，未取。
- EnglishCentral / TED-Ed 等教育平台：ToS 不可下载，未取。

---

## 2. 最优候选（已下载）

| 项 | 值 |
|---|---|
| 来源 | Wikimedia Commons `File:Dell S3423DWC 34-Inch Curved Monitor - Unboxing + Assembly (rZ30VcUyqQQ).webm` |
| 原作者 | XALIRATE（原发 YouTube `watch?v=rZ30VcUyqQQ`，2025-01-26 导入 Commons） |
| 许可 | **CC BY 4.0**（AttributionRequired=true，`LicenseUrl=https://creativecommons.org/licenses/by/4.0`） |
| 时长 | **262.5s（4:22）** ✅ 1–5 分钟 |
| 语音 | ✅ 英文口播，逐句讲解（参数/接口/装配）——见 §3 ASR 实测 |
| 画面 | ✅ 包装盒、附件、显示器本体、背板接口、桌面双屏 |
| 体积 | 原始 4K 407MB → 官方 480p 转码 35.3MB → 本地 mp4 **13.6MB** |
| 成品 | `out/112/real-demo.mp4`（854×480 H.264+AAC，faststart，sha256 `a407d874f6d89694d19b9e01d62c7d3141f6710fd882c14f6d2044d5a0e027db`） |

**许可义务（对外演示/对外分发时须署名）**：

> XALIRATE，「Dell S3423DWC 34-Inch Curved Monitor – Unboxing + Assembly」，
> Wikimedia Commons，CC BY 4.0，
> https://commons.wikimedia.org/wiki/File:Dell_S3423DWC_34-Inch_Curved_Monitor_-_Unboxing_%2B_Assembly_(rZ30VcUyqQQ).webm

**复现命令**（下载 + 转 mp4，含出处归档）：

```bash
# 480p 官方转码（35.3MB）——注意不要再下 4K 原始档（407MB）
curl -L -x http://127.0.0.1:7890 -o out/112/dell-480p.webm \
  "https://upload.wikimedia.org/wikipedia/commons/transcoded/7/73/Dell_S3423DWC_34-Inch_Curved_Monitor_-_Unboxing_%2B_Assembly_%28rZ30VcUyqQQ%29.webm/Dell_S3423DWC_34-Inch_Curved_Monitor_-_Unboxing_%2B_Assembly_%28rZ30VcUyqQQ%29.webm.480p.vp9.webm"
# 转上传口径要求的 .mp4（H.264/AAC，浏览器可播，≤200MB 上限）
ffmpeg -v error -y -i out/112/dell-480p.webm \
  -c:v libx264 -preset veryfast -crf 26 -pix_fmt yuv420p \
  -c:a aac -b:a 96k -movflags +faststart out/112/real-demo.mp4
```

---

## 3. 素材验证证据（用本仓库自己的工具链实测，无 DB 写入）

**ASR（`apps/api/src/suite_api/services/asr.py` 同一套函数）**：`extract_audio_wav`
0.3s → 16kHz 单声道 wav 8.4MB → 单块（24MB 上限内）→ Groq
`whisper-large-v3-turbo` 6.2s 返回 **49 个句级段**（带 start/end 秒）→
`aggregate_segments`（停顿 ≥1.2s 断段、20s 强切）→ **12 条候选**、无上限合并。
首条候选原文：

> `[0.5–21.5s]` "Hey what's good, I'm Sadia and in this video I'll be unboxing and
> setting up this Dell 34 inch curved USB-C monitor. I really wanted to switch from
> using two screens so I just had to get a bigger monitor."

证据工件：`out/112/asr-evidence.json`（12 条候选全文 + 计数）。
探针脚本：`out/112/asr_probe.py`（只读文件，不碰演示库）。

**视觉（VLM,`services/vlm.py`）**：抽 4 帧复验（`out/112/frame-*.jpg`）——
30s「Dell 34 Curved USB-C Monitor 零售包装盒」、95s「开箱白色包装」、
205s「手把电源线插进显示器背板」、240s「桌面双屏（LG+Dell）实拍」。
即洗帧环节有真实商品帧可挑（建议候选秒位：30 / 205 / 240）。

---

## 4. 与演示库现状核对（只读，未改库）

`docker compose exec -T db psql -U suite -d suite -c "select id,label,size_bytes from
clip_recordings order by id"` 实测 4 行：

```
 id |        label         | size_bytes
  1 | .slice-test.mp4      |      22190
  2 | acceptance-30min.mp4 |    5297868
  3 | rebind-320x240.mp4   |    7364612
  4 | live93.mp4           |      90174
```

- **无冲突**：无 `real-demo.mp4`/相似条目；灌入后新录像 id=5。
- `scripts/demo_reset.py` 的「录像探针」规则是
  `label ~* (探针|probe|test|acceptance|rebind)` 且**挂的候选里没有已登记资产**才删；
  `real-demo.mp4` 不命中探针词，且拣选后候选即 registered（证据链）→ 不会被
  `--apply` 误删。

---

## 5. 灌入命令清单（Owner 择时执行；本刀不代跑）

前置：栈起着（`docker compose up -d`），API=`http://localhost:8000`，
治理台=`http://localhost:5173`；`.env` 里 `ASR_API_KEY`、`VLM_API_KEY` 均已配置。

```bash
# 0) 登录拿操作者 cookie（密码取 .env 的 OPERATOR_PASSWORD）
curl -s -c /tmp/suite.cookies -H 'Content-Type: application/json' \
  -d '{"username":"operator","password":"<OPERATOR_PASSWORD>"}' \
  http://localhost:8000/api/auth/login

# 1) 上传源录像（只收 .mp4、≤200MB）→ 回执 id=5
curl -s -b /tmp/suite.cookies -F "file=@out/112/real-demo.mp4" \
  http://localhost:8000/api/clips/recordings

# 2) 云转写（真值：约 12 条候选落 pending；耗时 ~7s）
#    product_id 可空=候选不挂商品（演示库无「显示器」商品，见 §7）；
#    若要挂：先建商品再传其 id，POST /api/products {"name":"34 吋曲面显示器","...":...}
curl -s -b /tmp/suite.cookies -H 'Content-Type: application/json' \
  -d '{"product_id": null}' \
  -X POST http://localhost:8000/api/clips/recordings/5/transcribe

# 3) 看候选（挑 3~4 条讲参数/装配的，例如 0.5-21.5s、46.8-71.2s、191.7-214.6s）
curl -s -b /tmp/suite.cookies http://localhost:8000/api/clips/candidates

# 4) 人工拣选 → ffmpeg 真切 mp4 → 各登记为 kind=video 资产（待人洗），回执带资产 id
curl -s -b /tmp/suite.cookies -H 'Content-Type: application/json' \
  -d '{"ids":[<候选id1>,<候选id2>,<候选id3>]}' \
  -X POST http://localhost:8000/api/clips/candidates/pick

# 5) 发布视频资产（治理台详情页看机洗结果后；视频无必填字段）
curl -s -b /tmp/suite.cookies -X POST http://localhost:8000/api/assets/<A-ID>/publish

# 6) 洗帧候选（需 VLM key；均匀采样每 5s、上限 24 帧、≥6 分过线、上限 8）
curl -s -b /tmp/suite.cookies -X POST http://localhost:8000/api/assets/<A-ID>/frame-candidates

# 7) 确认登记一帧（用候选回执里的 at_second；本片建议 30 / 205 / 240）
curl -s -b /tmp/suite.cookies -H 'Content-Type: application/json' \
  -d '{"at_second": 30}' -X POST http://localhost:8000/api/assets/<A-ID>/frames

# 8) 帧资产人洗描述 → 发布（图片唯一治理字段是「图片描述」）
curl -s -b /tmp/suite.cookies -X PATCH -H 'Content-Type: application/json' \
  -d '{"图片描述":"Dell 34 吋曲面 USB-C 显示器开箱：零售包装盒正面，可见型号 S3423DWC。"}' \
  http://localhost:8000/api/assets/<A-frame-ID>/versions/1/fields
curl -s -b /tmp/suite.cookies -X POST http://localhost:8000/api/assets/<A-frame-ID>/publish

# 9) 验证：售前问句（顾客页）问「有显示器的实拍讲解吗/有开箱视频吗」
#    期望 citations 含新视频资产（+已发布帧的 media_citations 出真图）；
#    也可直接跑既有集成对：apps/api/tests/test_real_clips_integration.py
```

UI 等价路径（推荐给演示当天的操作）：治理台「切片」页 → 上传录像 → 自动转写 →
勾选候选 → 拣选 → 视频资产详情「洗帧到素材库」→ 勾选帧确认登记 → 帧资产填描述发布。

**回滚**：拣选前想撤 = 直接删录像行（非中台对象）；拣选后 = 走资产「废弃」
（ADR 0042）而不是删行。

---

## 6. 若不用公网素材：Owner 自录方案（本轮**未启用**，备用）

公网素材已够用，但若演示想要**中文口播 + 店内商品**：

1. 手机横屏录 3–5 分钟，讲一件实物（例：耳机/键盘/显示器）；
2. 环境安静（关空调/风扇）、离商品 0.5–1 米、光线足；
3. 口播结构照演示需要：开场（我是谁+讲什么）→ 外观/参数 → 使用演示 → 收尾；
   句间自然停顿 ≥1.2s（ASR 靠停顿断段）；
4. 导出 mp4（1080p 即可，≤200MB），命名 `self-record.mp4` 放 `out/112/`；
5. 上传/转写/拣选/发布/洗帧步骤与 §5 完全相同（把文件名换掉即可）；
6. 来源标注=自录（无第三方许可义务，不必署名）。
   注意：**别用 `.slice-test`/`test`/`probe` 之类标签**——会命中 `demo_reset`
   的录像探针清理规则。

---

## 7. 边界与风险

- **工件位置**：`out/112/`（real-demo.mp4 13.6MB + 4 张帧 jpg + 2 个探针脚本 +
  asr-evidence.json，合计约 14MB）。`out/` 在仓库里**是被 git 跟踪的**（历史工件），
  本刀守「只动 scripts/ 和 docs/」边界未改 `.gitignore`——**请勿把 out/112/ 的
  二进制提交**；建议 Owner 顺手把 `out/112/` 加进 `.gitignore`。
- **CC BY 4.0 义务**：对外演示/分发视频画面时须带 §2 的署名串；只在本地管道里
  测试不构成分发，但对外场合（录屏发给客户等）建议在片头字幕或页脚标注。
- **语言**：英文口播。ASR（whisper 系）没问题；若要中式卖点话术需自录（§6）。
- **Commons 许可逐文件不同**：本次已核到文件级（CC BY 4.0）；以后换素材必须重核
  （`extmetadata` 的 `LicenseShortName`/`AttributionRequired`）。
- **演示库无「显示器」商品**（179 件里最接近的是 33 `Dell Axim X51v`、130
  `苹果鼠标`）→ 转写 `product_id` 建议留空；要挂商品就先建「显示器」类目商品
  （会动演示库商品面，Owner 决定）。
- **不要爬 B 站/抖音/淘宝回放**（89 刀红线）：本刀素材全部来自 CC/PD 明确源。
