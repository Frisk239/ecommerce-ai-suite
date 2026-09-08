# 工程第 27 刀 closeout：演示收官刀（feat/demo-finale）——goal 完成标准 7/7

日期：2026-09-08。上刀 intake：`docs/progress/semantic-cleanup-intake.md`（通过）。短对齐：`.scratch/demo-finale/spec.md`（audit-5 收官排期；无新 ADR）。基线=`origin/main`（086df26，PR #33 合并后）。

## 交付（goal 完成标准第 5 条达成——7/7 全绿）

1. **README 3 分钟口述稿**（完成标准 5 唯一未达项）：四段时间锚——0:00 接待含缺口闭环（例句走种子通用名：净含量引用→会员积分拒答→G-0001→补文档→再问命中→回流）/0:45 内容闭环（素材质检登记→切片汇入→运营投放≠发布）/1:30 MCP（四工具无 publish，导出是数据包不是微调集）/2:20 中台为何核心（三态+A-xxxx·vN 锚定+血缘追溯）。评审实修：480ml→500ml（种子实际值）。无虚构品牌（goal:14）。
2. **总览「其余能力」四行**（对照原型冻结形状）：中台·资产/中台·商品/销售考核/连接层（MCP）。
3. **拒答交接摘要**（词条「转人工·交接带结构化摘要」在拒答面兑现）：REFUSAL_CONTENT 结构不变+追加段（问句先 redact 后截 60 字+`G-{id:04d}`）；操作者消息文本带摘要与缺口号、顾客通道只见摘要（白名单延伸到文本，钉测）。
4. **ruff format 全仓收口**（audit-5 P1）：46 文件纯格式（格式化前后收集数 510=510 证明零语义）。

## Owner 验收（浏览器实证）

总览待人洗队列统计+四行入口；客服问「会员积分怎么兑换」→拒答+已转人工+问句摘要+G-xxxx（操作者通道）；README 口述稿四段锚点 grep 在。

## 两轴评审（`docs/progress/demo-finale-review.md`）

净可合；P1×1（口述稿数字）实修；P2×2 记债。

## 计数（摘自命令输出，junitxml 机械计数）

- ruff：All checks passed
- 无 DB：`tests=510 passed=379 failed/errored=0 skipped=131`
- 带 DB（5433）：`tests=510 passed=510 failed/errored=0 skipped=0`（基线 505 → 510 只增）
- web：`npm run build` 通过

## goal 完成标准对账（7/7）

1 七块入口✓（侧栏 8 入口+总览）2 中台唯一权威✓（全锚 A-xxxx·vN）3 MCP 外连✓（Cursor+冒烟）4 路径可演示✓（25+1 刀 Owner 点穿）5 **README 口述稿✓（本刀达成）**6 死亡三问✓（血缘追溯/编排可重试/无命中拒答）7 无虚构品牌✓（种子通用名+口述稿 grep）。

## 下一刀

刀计数：第 27 刀。**第 28 刀起=部署阶段**（audit-5 收官排期：CI workflow/反代 HTTPS/备份/监控一揽子；真视频/ASR 部署语境单列）；审计刀 6 于第 30 刀后。CONTEXT 推进句已回写。
