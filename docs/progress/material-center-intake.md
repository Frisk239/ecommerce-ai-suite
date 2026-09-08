# Intake · 第 17 刀素材中心（feat/material-center）

- 日期：2026-09-08（Slice Owner 接手第 18 刀）
- Prev slug：`material-center`；实现两笔+闸门修复+超时修复+评审处置+文档三笔，合入 `aa6ea88`（PR #22）
- Merge 状态：**已合入 default**。第 18 刀从 `origin/main` 起 `feat/clip-picking`。
- 特殊性：同会话 Owner 一手验收（含两笔 Owner 前置修复），下表为当时原始记录。

## Evidence（Owner 一手，junitxml 机械计数）

| 项 | Closeout 声明 | Intake 复核（同会话原始输出） |
|---|---|---|
| 无 DB | `tests=375 passed=279 failed/errored=0 skipped=96` | 同（评审处置后复跑一致） |
| 带库全量（5433） | `tests=375 passed=375 failed/errored=0 skipped=0` | 同（基线 327 → 375 只增） |
| ruff / web build | All checks passed / 通过 | 同 |
| Owner 浏览器点穿 | 生成→抽检→A-0010→发布→检索引用 | 亲历：M-0002 真 LLM 生成→待抽检→登记 A-0010→未确认直接发布（闸门修复）→「316不锈钢饮水随行」引用 `A-0010 · v1` |

## Spec vs claim（抽查 3 项）

1. **任务非中台对象** — PASS。material_tasks 检索/发布/血缘零 join（评审全扫）；failed 不写字节（0029）。
2. **打码三接入点** — PASS。prompt 输入/qa_pairs 值/文档字段值；版本字节不动钉断言（版本 text 仍含裸号）。
3. **闸门 kind 条件** — PASS。发布端点+publishability 同口径；素材未确认直接发布（词条兑现）；文档 422 对照用例不动。

## Safety

无秘密入库。

## 债务（轻，进 slices.md「更后面」候看）

17 刀评审七条（storage 死参数/reject commit 层次/retry 覆盖行写/qc 截断口径/闸门条件两处复制/list N+1/updated_at 视图）+ 打回路径 UX。

## Verdict

**通过** — 进第 18 刀直播切片（0014/0015+源录像词条+切片汇入视图）。刀计数：第 18 刀；审计刀 4 于第 20 刀后。
