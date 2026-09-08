# 工程第 9 刀 closeout：数据接入 CSV 批量导入（feat/data-ingest）

日期：2026-09-08。上游：`docs/research/demo-to-product-gaps.md` §1/§6 候选 1（Zendesk CSV title+content 最小形状）+ ADR 0025/0013。短对齐：`.scratch/data-ingest/spec.md`。基线=main（PR #11 视觉刀合并后）。

## 交付（一条厚路径）

操作者资产页「登记资产」抽屉切「批量导入」→ 选 CSV（title,content 两列）→ 预览计数（以后端为准）→ 提交 → 逐行独立登记（复用 `register_asset`：字节落对象存储 0013、kind=document、不挂商品、source_kind=**upload**——CSV=上传通道的批量形态，不新增 0025 枚举）→ 报告 `{created:[{row,asset_id,title}], skipped:[{row,reason}]}` → 人洗发布 → **客服/MCP 立即可答/可引**。冷启动死结（空库→全拒答→无回流→无新资产）有了批量入口。

- 后端：`services/csv_import.py` 解析纯函数（csv 标准库+utf-8-sig；200 行/200KB 单行上限；表头缺列 422 点名）；`POST /api/assets/import-csv` 逐行 commit（部分成功不回滚——登记不是发布，0005 单事务是发布语义）；2MB 文件前置于解析（413，评审处置）。
- 前端：抽屉批量页签（单份/批量 seg 切换；补缺口流程仍单份不受扰）；FileReader 极简预览（计数提示明示引号偏差与后端为准）；结果区 A-XXXX 链接+跳过原因。

## Owner 浏览器验收（全过）

upload 4 行 CSV（3 好 1 空 title）→ 预览「4 行数据，约 3 条将创建、1 条将跳过」→ 导入 → 报告 A-0006/0007/0008（链接）+ 第 4 行「title 为空」→ UI 发布 A-0007（POST 200，确认框全流程）→ 客服新会话问「买了东西想退货，政策是什么？」→ 真模型回答「签收后 7 天内可申请退货。」+ **引用 `A-0006 · v1`**（正是 CSV 第一行）。验收插曲：A-0006 的浏览器确认点击两次落空系 playwright 时序（API 直发 200、A-0007 重走 UI 全链成功），非前端缺陷。

## 两轴评审与处置

- Standards：**零硬违规**（0013 字节/0025 定值/0003 键形全部合规）。实修：报告文案「已进入待人洗」是无条件断言（机洗失败停在已接入仍算 created）→ 改「已登记：机洗成功者进入待人洗，失败者留在已接入可就地重试」；预览文案明示引号包裹计数偏差。
- Spec：Must 1-3 齐全。实修：2MB 前置防线（评审指出无上限时巨型文件在被行数拒绝前已整读，合法上限 200×200KB≈40MB）；集成测试补 `retrieve` 真命中断言（Must 4 原句此前只直查切块表）。接受并记录：前端空文件拦截严于后端「空批次报告即答案」（善意 UX，后端契约不变）；登记失败并入 skipped（「尽力而为」兜底）。

## 计数（摘自命令输出）

- 仓库根无 DB：`143 passed, 49 skipped in 6.37s`
- 带 `SUITE_TEST_DATABASE_URL` 全量：`192 passed in 20.19s`（+1：413 防线）
- `ruff check apps packages`：All checks passed
- web：`npm run build` ✓（310ms）；`npm run lint` 0 errors

## 遗留（债务接既有排期）

- RegisterAssetDrawer 重构批：mode 四处分支拆子组件、FilePickField/ReportList 同形提取、csv 六 state 收拢（与 UiMessage 工厂同批）
- 孤儿字节（逐行 commit 失败留无主对象键）——单份通道同构，审计刀 P1 既有项
- URL 导入/网站同步、同步-断链语义（=「同步新版本/断链脱离源」与版本指针咬合，调研在案）——数据接入后续刀候选

## 下一刀

安全面收口（第 8 刀遗留：XFF 伪造/限流先于鉴权/404-401 探测，小刀）或按计划者调研继续（回流增强=LLM 抽 QA 草稿、订单工具、顾客 widget、Shopify）；审计刀 2 按计数线（约第十一刀）临近。
