# 工程第 34 刀 closeout：CI 门禁（feat/ci-gate-final）

日期：2026-09-09。上刀 intake：`docs/progress/data-contract-intake.md`（交接收口）。依据：roadmap 第 34 刀行（另一会话起头、Owner 收口）。

## 交付

`.github/workflows/ci.yml` 双 job：**lint**（ruff apps/packages/scripts + npm lint + npm build，uv frozen/node 22 cache）与 **test**（pgvector/pgvector:pg16 service 容器 + `SUITE_TEST_DATABASE_URL` 必设——refuse skip-green 显式失败步 + `uv run pytest` 全量）；push main 与 PR 双触发。`apps/api/tests/test_ci_workflow.py` meta 测试钉住 workflow 关键行（URL 必设/skip-green/pgvector/ruff/npm build），防门禁被静默掏空。**双文件同 commit**（修复悬空风险）。

## Owner 验收

- 本地预演：ruff 全绿 + 带 DB 593/593（与 CI test job 同命令形状）；meta 测试过（workflow 文本断言全中）
- 合并后 CI 首跑即在本 PR 上验证（fail-fast：若 uv frozen/network 有问题当轮暴露）

## 计数

- ruff：All checks passed；无 DB `593 passed / 0 failed / 148 skipped`（含 meta 测试）；带 DB（5433）`593 / 0`

## 遗留

- CI 首跑若网络受限（uv/npm 拉包）需按环境坑排查（记忆：构建网络错先查环境）
- 后续可加：评测集 job（第 35 刀评测尺产出后挂 CI——roadmap 已排）

## 下一刀

**第 35 刀 RAG 评测尺**（goal §6.2.2 验收线：golden 四分布+recall@k+拒答率报告 `docs/research/rag-eval-report.md`）→ **审计刀 6**（第 35 刀后，三路审 26–35）→ 36 同义词 → 37 客服真 loop。CONTEXT 推进句已回写。
