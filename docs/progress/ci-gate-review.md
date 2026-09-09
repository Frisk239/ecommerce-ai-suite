# 工程第 34 刀两轴评审：CI 门禁（feat/ci-gate-final）——Owner 亲评（小刀）

日期：2026-09-09。Fixed point：`origin/main...HEAD`（单提交：ci.yml+meta 测试+三件套同 commit）。Spec：roadmap 第 34 刀行（GitHub Actions：ruff+无 DB 单测必绿；集成 job 起 Postgres 未设 SUITE_TEST_DATABASE_URL 则失败；npm build+lint）。

**Standards**：双文件同 commit ✓（修复另一会话的悬空半提交风险）；meta 测试防门禁被静默掏空（断言 workflow 文本含关键行——浅但对这类「配置即门禁」资产合适）；workflow 与仓库门禁一致（ruff 三目录/uv frozen/npm 22 cache lockfile/pgvector pg16 与 compose 同镜像）。P2：`uv run pytest` 在 lint job 无 DB——CI 的 lint job 会跑 148 skip（非 green 谎言，因 test job 才是权威；可接受）。
**Spec**：roadmap Must 四项全在（ruff ✓/pgvector service+必设 URL ✓ skip-green 显式失败 ✓/npm lint+build ✓）；push main+PR 双触发 ✓。

**结论：可合。** 首次上 CI 后，带 DB 全量从「本地习惯」变「合并必绿」——goal §6.2.2 的工程信用前提落地。
