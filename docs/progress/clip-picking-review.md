# 工程第 18 刀两轴评审：直播切片（feat/clip-picking）

日期：2026-09-08。Fixed point：`origin/main...HEAD`（评审时 `4c67681`/`5d4e66f`/`10fd39d`，处置提交在后）。Spec：`.scratch/clip-picking/spec.md` + ADR 0039。

## Standards

**零硬违规，可合。** 候选不是中台对象（检索/发布/引用/MCP 零触点）；源录像只存 label 字符串不进中台；切片汇入=纯视图（客户端过滤，不二次登记不造任务）；不暗插素材任务（0015）；单向状态机服务层 409+UI 双兜底；CONTEXT 恢复提交干净（恰两处变化无夹带）；种子幂等键合理；迁移/回执锚/异常转 HTTPException 合惯例。

实修（评审处置提交）：`pick_candidates` docstring「批量原子性」改「拒绝原子性」——逐候选 commit 的部分落库边界（第 k 个失败前 k-1 已落、该候选可重拣，与「登记失败不挡字节」同形）如实写明。

记债务（judgement）：kind 分派散三处（make_object_key/machine_wash_field_names/KIND_LABELS——加「图片」种类时逐点动）；`list_candidates` N+1 而同文件 pick 已用批取；seed 从 services 引 PENDING 常量可挪 models。

## Spec

**六条 Must 全命中，Out 零越界。** 补审实证：ClipsPage 冻结交互逐字对上原型（N=0 禁用/已登记卡仅回执链无撤销入口/reload 失效勾选出列）；切片汇入页签纯只读；KIND_LABELS 补 video；Out 全扫描零命中（披露文案不算）；CONTEXT 恢复恰两 hunk；收集 391 只增不减、切片集成 3 passed exit=0。注：spec 原文「既有 375」为开刀时口径，实现后实为 391（+16 本刀），只增方向正确。

过程注记：Spec 轴首子代理中途被 provider 终止，其已交付的后端结论（Must1–4 全命中）+补审子代理（前端四项 PASS）合并采信。

## 计数（Owner 复跑，junitxml 机械摘取）

- `uv run ruff check apps packages`：All checks passed
- 无 DB：`tests=391 passed=292 failed/errored=0 skipped=99`
- 带 DB（5433）全量：`tests=391 passed=391 failed/errored=0 skipped=0`（基线 375 → 391 只增）
- web：`npm run build` 通过
- Owner 浏览器点穿：候选勾选 2 条→拣选登记→A-0011/A-0012→A-0011 发布（video 无字段集直接发布）→客服问「钛钢内胆一体成型」命中引用 `A-0011 · v1`（转写内容回答）→素材中心「切片汇入」页签两条可见
