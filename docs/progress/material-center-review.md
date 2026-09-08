# 工程第 17 刀两轴评审：素材中心（feat/material-center）

日期：2026-09-08。Fixed point：`origin/main...HEAD`（评审时含 `23bfed7`/`43b60f7`/`3817099` 闸门修复/超时修复，处置提交在后）。Spec：`.scratch/material-center/spec.md` + ADR 0038。

## Standards

**零硬违规。** 任务守住「不是中台对象」（检索/发布/血缘/资产侧零 join，asset_id 单向回执锚）；质检过线才登记（register_asset 唯一调用点在 approve，failed 不碰字节）；「可重试」属性读法自洽；source_kind 服务端定值无注入面；running 态 LLM 前 commit（P1#2 同款）；redact 环视正则正确不吞正文数字；五态徽章复用既有灰阶 token。

实修（评审处置提交）：AI 客服归回「业务能力」组（Spec 轴点名的越位项回滚——素材中心改以 group:null 跟随，同「中台·商品」先例）；「版本字节不动」补钉断言（版本 text 仍含裸号）。

记债务（judgement）：`run_generation_task`/`retry_task` 的 storage 死参数（保留作「生成不碰字节」的文档性存在）；`reject_task` 路由层 commit 与其余转移不一致；retry 置 QUEUED 被 RUNNING 覆盖（无效行写）；`qc_check` 审截断前 title；闸门 kind 条件两处复制可下沉；list_tasks N+1 无分页；retry 409 文案未提示打回路径。

## Spec

**六条 Must 全实证，Out 零渗越。** 五态转移矩阵全覆盖（含非法转移 409 双层）；空 key=failed 无降级（「素材通道名下一个字节都没多」）；redact 三接入点+掩码口径与 ADR 一致；Owner 两笔修复语义逐字对上词条（闸门 `kind=="document" and product` 对应「仅当种类=文档且挂了商品时……素材没有规格必填」；前端 30s=LLM 20s+缓冲）；评审子代理本机带 DB 全量 375 exit=0。轻微：MaterialTaskOut 无 updated_at（列有视图未暴露，记债）。

## Owner 追加修复（评审前 Owner 验收揪出，两笔）

1. **必填闸门 kind 条件**（`routes/assets.py`+`services/asset_view.py` 同口径）：素材刀激活的潜伏偏离——第 2 刀以来闸门不分种类，词条锁死「素材没有规格必填」。集成断言更新（素材未确认直接发布 200）。
2. **前端 30s 超时**：默认 15s 早于 LLM 20s 上限，建任务/重试真实调用必 ERR_ABORTED（浏览器实测揪出）；`request` 加可选 timeoutMs 默认不变。

## 计数（处置后 Owner 复跑，junitxml 机械摘取）

- `uv run ruff check apps packages`：All checks passed
- 无 DB：`tests=375 passed=279 failed/errored=0 skipped=96`
- 带 DB（5433）全量：`tests=375 passed=375 failed/errored=0 skipped=0`（基线 327 → 375 只增）
- web：`npm run build` 通过
- Owner 浏览器点穿：生成（真 LLM）→待抽检→详情抽屉→抽检通过→A-0010→直接发布（闸门修复兑现）→「316不锈钢饮水随行」问句引用 `A-0010 · v1`（素材进检索闭环）
