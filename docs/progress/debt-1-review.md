# 工程第 16 刀两轴评审：还债刀（feat/debt-1）

日期：2026-09-08。Fixed point：`origin/main...HEAD`（评审时 `ac37cbc`，处置提交在后）。Spec：`.scratch/debt-1/spec.md`（施工单=audit-3-closeout P1#1–#6/#8+P2）。

## Standards

**零硬违规。** 分层/错误契约/测试布局/注释惯例合规；词条-ADR-代码-评测集四方一致可互追溯（词表收窄、双前置、留缺口归宿）。

实修（评审处置提交）：
- retry 端点 P1#2 补钉测（`test_retry_machine_wash_releases_transaction_before_llm`——LLM 调用时刻 in_transaction=False）。
- `registration.py` 两段式事务 docstring 补崩溃窗口限定（登记行 commit 后、调用方收口前崩溃 last_error 可能未落库，retry 兜底）。
- `llm.py` 注释口径修正（一次性 loop 客户端只靠 finalizer 关 socket，短暂 fd churn 面记录在案；密钥热轮换与修复前同口径需重启）。

记债务（judgement）：机器洗分派谓词拆两处（词表+found/error 在 chat_engine、found/stock 分解在 _run_stock_ask）可提取纯函数；词表命中无商品匹配时 query_stock 全表扫后丢弃（性能备注，商品两张表规模可忽略）。

## Spec

**七条 Must 全部落地，零越界。** P1#1 混跑钉测扎实（替身模拟 loop 亲和，旧单例必炸、新实现双客户端）；P1#3 三层齐全（单测零接触哨兵+集成政策文档可命中+ADR 修订段含推翻旧归宿论证）；P1#5 QA 块前置+250 轮截断钉测；P1#6 两工具 5 处 logger.exception 行为不变；被更断言逐条核为**对称收紧非放松**（gap 计数+1、tool 零接触、政策可命中）；golden case 改名与 spectrum 自检（按形状统计）自洽。

轻微欠账（已由处置补）：retry 端点 commit-先于-LLM 原无钉测。评审子代理本机无 DB（端口坑），DB 面由 Owner 全量复跑覆盖（见下）。

## 计数（处置后 Owner 复跑，junitxml 机械摘取）

- `uv run ruff check apps packages`：All checks passed
- 无 DB：`tests=327 passed=236 failed/errored=0 skipped=91`
- 带 DB（5433）全量：`tests=327 passed=327 failed/errored=0 skipped=0`（基线 316 → 327 只增）
- Owner 浏览器抽查（顾客通道）：「钛钢保温杯有货吗」仍走工具（`get_stock` + 42 件）；「保温杯还剩多少毫升」走检索（真模型引用 A-0003+A-0009 诚实回答，不再误答有货）；「库存政策是什么」走检索拒答转人工（政策文档发布后可命中）。
