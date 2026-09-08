# 工程第 23 刀两轴评审：总览页+连接层演示页（feat/overview-connect）

日期：2026-09-08。Fixed point：`origin/main...HEAD`（`cb6e2c6`）。Spec：`.scratch/overview-connect/spec.md`。两轴合并轻评审。

**净，可合。** 三卡文案与原型冻结句逐句一致、链接转工程路由；统计带口径与列表页同源（待人洗纯新/已接入/已发布指针/open 缺口/会话）；四工具卡+「没有 publish（0005）」红条；mcp.json 仅占位符（grep 全仓 web 源码无真值）；取版走操作者面豁免端点；后端零改动零新依赖；Out 零越界。P2 一条：原型「其余能力」入口行未复刻（spec 未要求，观感反馈项）。

## 计数（Owner 复跑）

- ruff：All checks passed；无 DB：`tests=485 passed=366 failed/errored=0 skipped=119`（后端零改动，基线不变）；web build 通过
- Owner 浏览器点穿：总览三卡+统计带；连接页四工具卡/无 publish 红条/检索命中芯片（A-0003/0006/0007/0009）/取版预览（A-0009 转写 mono）/mcp.json 占位符
