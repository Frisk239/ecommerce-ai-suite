# 工程第 23 刀 closeout：总览页+连接层演示页（feat/overview-connect）

日期：2026-09-08。上刀 intake：`docs/progress/ops-agent-intake.md`（通过）。短对齐：`.scratch/overview-connect/spec.md`（纯前端观感刀，无新 ADR）。基线=`origin/main`（77dc0cc，PR #28 合并后）。

## 交付（goal 完成标准 1「七块都有入口」观感补齐）

1. **OverviewPage `/`**（去 redirect）：三条故事线步进卡（接待闭环/内容闭环/连接层——原型冻结文案，链接工程路由 `?status=` 中文 tab 口径）+统计带五格（待人洗纯新/已接入/已发布指针/open 缺口/会话数，与列表页同源）；卡片点击跳首步页。
2. **ConnectPage `/connect`**：四 MCP 工具卡（mono 名称/参数摘要/只读·可写徽章）+红条「没有 publish——发布只在治理台（0005）」；检索试玩（已发布+关键词客户端过滤，命中 A-xxxx·vN 芯片）；取版预览（下拉→getVersionText 前 500 字 mono——操作者面豁免端点）；mcp.json 复制块（`Bearer <MCP_BEARER_TOKEN>` 占位，页面零接触真值）。
3. 侧栏「连接层」入口+面包屑+品牌区回总览；后端/MCP 零改动零新依赖。

## Owner 验收（浏览器点穿，全过）

总览三卡+统计带呈现；连接页四工具卡/无 publish 红条/检索「保温」命中芯片/取版 A-0009 v1 转写 mono 预览/mcp.json 占位符。

## 两轴评审（`docs/progress/overview-connect-review.md`）

净可合（三卡文案逐句对照原型；占位符 grep 零真值；后端零文件改动）；P2 一条：原型「其余能力」入口行未复刻（观感反馈项）。

## 计数（摘自命令输出，junitxml 机械计数）

- ruff：All checks passed
- 无 DB：`tests=485 passed=366 failed/errored=0 skipped=119`（基线不变，后端零改动）
- web：`npm run build` 通过

## 遗留

P2 观感项（其余能力入口行）；audit-4 债池（24–25 刀范围）不变。

## 下一刀

刀计数：第 23 刀（审计刀 4 后第 3 刀）。**第 24 刀=债池刀**（audit-4 P1：GIN 索引/题库单资产推导/N+1/material 四小条/前端收口——按性价比挑）或按演示缺口重排；审计刀 5 于第 25 刀后触发。CONTEXT 推进句已回写。
