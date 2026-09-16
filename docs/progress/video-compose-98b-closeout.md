# 第 98b 刀 closeout：内容成片引擎 v1（AI 排版、人上市）

日期：2026-09-16。分支 `feat/video-compose-98b`（stacked 于 `feat/content-suite-98`）。

## 交付

1. **选材+排版纯函数**：已发布素材清单（切片含转写/图含描述/文案要点）→优先级排序+时长估算（15-60s）+不足补足（诚实 note）。
2. **plan 端点**（同步）：时间线+**ffmpeg 预览成片**（720x1280、图序列+切片拼接+字幕、**AIGC 水印无条件烙死**——红线①：filter 末段追加无开关，草稿恒含水印文本素材）+**剪映草稿 zip**（pyJianYingDraft 弃用——abspath+pymediainfo 与服务端生成冲突，自写 JSON 按 pyJianYingDraft 实产草稿逐字段复刻+相对路径+微秒时基；「媒体重链接」Debt）；产物暂存 compose/ 前缀。
3. **TTS_* env**（OpenAI 兼容 audio/speech；voice 厂商预置非克隆——红线④；无 key=无声预览+with_tts=false 诚实标注）。
4. **publish 人闸门**（红线③复用 98 刀同函数双闸；material 资产登记 source_kind=upload 服务端定值；「不传成品=认可预览」三处声明）。
5. **迁移 0033**（compose_tasks）；前端素材中心第三页签（时间线/预览播放/草稿下载/确认登记）；CJK 字体 Dockerfile+CI 双装；ADR 0056（四红线+高光模板入口）；词条「内容成片」+README 新节。

## 证据

- **Owner 门禁亲验（实修后复跑）**：1456/0/0/0（+34）+ ruff 全过 + 前端 lint/build。
- **真栈全链**：P-1 瓶装水高光模板→时间线（切片 A-264 截 8s+图 A-505+文案要点）19.0s→预览 ffprobe 18.96s→**AIGC 水印抽帧亲验**（VLM 复核切片帧与文案卡帧右上角均在）→草稿 zip（主轨 0/8s 微秒时基+3 文本含水印）→publish 真 LLM 双闸→**A-507 material 待人洗**（治理台可见）。

## 评审实修（两轴子代理：无 P0）

1. **事务纪律两处**（评审 Spec a）：plan 的 rollback 后 product.name 改用预取清单名（不重开事务贯穿打包）；publish 的 LLM 双闸与 put_bytes 前 db.commit() 收口（expire_on_commit=False 属性驻留）。
2. **tts.py 魔数偏移**：m4a 的 ftyp 盒在偏移 4——头部 startswith 永不命中（安全方向误拒）→ 补 data[4:8] 校验。
3. compose.yaml 补 TTS_VOICE 透传。

## 记债（ADR 0056 Debt）

1. 真 TTS/剪映真机打开草稿待 Owner（key+桌面剪映验证）。
2. compose/ 产物无清理（同 material/ 孤儿口径）。
3. plan 前端 240s vs 各步超时和可穿（现实秒级，边界）。
4. 成品 mp4 登记不资产化（仅任务留档）。

## 后续

第 99 刀：MCP 活状态只读工具（三工具复用客服函数+断言恰七+脱敏 ADR）——第三梯队产品刀收官。
