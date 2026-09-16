# 第 98 刀 closeout：自媒体内容套件（模板+配图+质检双闸）

日期：2026-09-16。分支 `feat/content-suite-98`（stacked 于 `feat/sft-export-97`）。

## 交付

1. **三模板**：站内投放/小红书笔记体/短视频口播稿（prompt 纯函数派生；配图 prompt 与尺寸按模板分档）。
2. **IMGGEN_* 配图生成**（`services/imggen.py`，OpenAI 兼容 images/generations + b64 魔数复验；默认 SiliconFlow FLUX.1-schnell）：无 key=`skipped_no_key` 不 fail 任务；暂存 `material/` 键；**approve 双资产登记**——material 正文 + **image 资产**（source_kind=material_generated、描述预填文案首句/VLM 草稿优先、待人洗走 94a 治理，**不自动进索引**——分层红线守住）。
3. **质检双闸**：规则闸（先挡，LLM 不再调）+ LLM 事实性质检二道闸（矛盾文案→failed 不进待抽检、last_error 记 issues、可重试复位；**两闸独立记录** qc_llm_passed 单列）；人抽检保留。
4. **迁移 0032**：material_tasks 五列（template/qc_llm_passed/image_status/image_object_key/image_asset_id，server_default+存量回填）。
5. 前端：三模板单选+配图开关（无 key 禁用）+详情双闸行与暂存预览端点；ADR 0055+词条×4+README 新节+env 接线。

## 证据

- **Owner 门禁亲验**：1422/0/0/0（+32）+ ruff 全过 + 前端 lint/build。
- **真栈（真 LLM）**：小红书模板 11.3s 出 emoji 种草文案→pending_qc+qc_llm_passed=true+image skipped_no_key→approve 登记 A-506；短视频模板分镜稿；**LLM 质检闸真模型拦截**（990ml vs 规格 480ml 矛盾文案→passed=False 双 issue）；浏览器三模板表单/禁用开关/双闸行复核。

## 评审实修（两轴子代理：均无 P0）

- roadmap 98 节「生图维持 Out」随刀订正（内容配图已打开，证据面仍 Out）；
- models qc_llm_passed 注释补「LLM 失败/坏输出也记 False，last_error 可辨」；
- ADR 0055 Debt 补记（approve 非原子重复登记面+409 钉子缺失）。

## 记债

1. 真 IMGGEN 未实测（无 key；替身覆盖三态/双资产/暂存清理——待 Owner 配 SiliconFlow key）。
2. 打回/放弃路径暂存孤儿（ADR 0055 Debt；登记路径已清）。
3. approve 双资产非原子（评审 P1 级；补偿事务待触发时补）。
4. approve 带图走 VLM 的前端 15s 超时（继承形态）。

## 后续

第 98b 刀：内容成片引擎 v1（AI 选材+模板排版→剪映草稿+ffmpeg 预览成片→人审改→登记 material）。
