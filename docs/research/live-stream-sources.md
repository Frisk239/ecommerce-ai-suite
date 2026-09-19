# 真实直播素材源调研（第 125 刀，2026-09-19）

> 需求：直播切片站缺真实直播素材（库里只有一条 Dell 开箱视频）。要求：**直播形态**（不是剪辑成片）、带语音讲解（可云 ASR）、≥10 分钟、有商品讲解节奏、许可可商用或至少可演示、可直接下载 mp4。
> 排除（89 刀先例）：B 站/抖音/Twitch 回放——版权+ToS 灰色，不爬。

## 定案（已采纳）

### ★ Samsung「Unbox & Discover 2023」官方发布会直播 — CC BY 3.0

- 条目：`https://archive.org/details/youtube-vUFub76fViA`（IA 镜像 Samsung 官方 YouTube 直播）
- 直链：`https://archive.org/download/youtube-vUFub76fViA/vUFub76fViA.mp4`（184.4 MB MPEG4）
- **许可**：`https://creativecommons.org/licenses/by/3.0/`（Samsung 官方在 IA 标注；BY=商用可，须署名）
- **形态**：发布会**直播录像**（livestream event），约 40 分钟，英文讲解，带官方章节——正是直播切片的用武之地：一个源录像多商品段落。
  - 00:00 Unbox & Discover 2023；00:26 CEO 开场；06:55 可持续与无障碍；18:30 SmartThings；**23:20 Neo QLED 8K；29:31 Samsung OLED；32:08 Gaming 屏**；34:04 Gaming Hub；39:08 收尾
- **类目匹配**：电视/屏 → 店内 `显示器` 类目（Wikidata 商品在库）。
- 用法（第 125 刀实测）：上传源录像 → 云 ASR 转写 → 按录像拣选 → 真切 mp4 → 发布 → 洗帧（VLM 评注对照画面）→ 客服问句引用。

## 备选（已核、未用）

| 源 | 许可 | 形态 | 备注 |
| --- | --- | --- | --- |
| IA `JuusonTurhaVideoDiary-Unbox-MicrowaveOvenUpdate` | **CC0**（公有领域） | 微波炉开箱视频日记 | 语音少（视频日记形态）；食品类目可挂；兜底备选 |
| IA `GeekBeat.TV_Reviews_*` / `Rev3_Mobile_Geeks_Reviews_*`（Revision3 存档） | CC BY-NC-SA 3.0 | 科技开箱系列（Drobo/Hisense 屏/Kindle） | NC=非商用——内部演示可、重分发不可；成片是剪辑非直播 |
| IA `ratao_oWuKyBkskDwY084Y` | CC BY 3.0 | 葡语 mini-PC 评测 | 语言不对口（ASR 转得出但客服引用面为葡语） |
| Wikimedia Commons 视频 | 多为 CC BY/CC BY-SA | 短片段（秒级-分钟级）为主 | 时长不够「直播」，适合配图不适合切片线 |

## 学术数据集（已核、门槛不符）

| 数据集 | 内容 | 为什么不用 |
| --- | --- | --- |
| LPR4M / RICE（快手，arXiv 2308.04912） | 4M 直播片段+店铺图+ASR | 需签协议邮件申请（jiajian@kuaishou.com），等审批不可控 |
| COPE（快手） | 商品页+短视频+直播三域 | 同上（协议申请） |
| LiveLongBench（ACL 2026，抖音） | 11 大类目直播**转写文本** | 只有文本无视频——喂考核题库可以，切片线无视频可切 |
| LiveSeg/MultiLive（Adobe Behance，WACV 2023） | 11,285 条创意直播（15,038 小时） | 数据集按论文请求发放，非开放直链 |
| HF `Reecorder/video-pipeline` | TikTok Live 录制 1.31TB | 体量过大+仓库访问条款仅研究用 |

## 结论

公网「可直接下载的直播形态商品内容」主要靠 **Internet Archive 的许可过滤检索**（`licenseurl:*creativecommons*`）；学术直播数据集体量大但协议墙高。定案 Samsung 发布会直播（CC BY 3.0、直播形态、多商品章节、类目对口），CC0 微波炉开箱留作兜底。
