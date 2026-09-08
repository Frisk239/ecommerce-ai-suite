# 工程第 18 刀 closeout：直播切片（feat/clip-picking）

日期：2026-09-08。上刀 intake：`docs/progress/material-center-intake.md`（通过）。短对齐：`.scratch/clip-picking/spec.md`；领域裁决：ADR 0039（候选种子 mock/登记字节=带时间码转写文本/只登记视频资产不造任务）。基线=`origin/main`（aa6ea88）+ CONTEXT 修复提交。

## 前置修复（本刀分支第一笔）

**CONTEXT.md 词条节误删事故修复**：第 17 刀 align 提交的推进句替换脚本吞掉了从推进句到文件尾的全部词条（-168 行，探索子代理揪出）；从 `5bfc7ac` 恢复全文并重新应用 0038「任务」状态句（`4c67681`，diff 恰两处）。教训已沉淀： CONTEXT 更新只走 Edit 工具精确替换。

## 交付（内容闭环第二段）

1. **迁移 0010 `clip_candidates`**：pending/registered 单向+回执锚（material_tasks 先例）；非中台对象（检索/发布/引用/MCP 零触点）。种子 4 条（保温杯 2+瓶装水 2，含可检索关键词，幂等键 timecode+transcript）。
2. **`services/clips.py`**：`pick_candidates` 拒绝原子性（整批校验先于首字节；含已登记整体 409）；逐候选 register_asset(kind=video, source_kind=clip_pick 服务端定值, 字节="[start-end] 转写")；404/409/422。
3. **对象键/机洗**：`clips/` 前缀；video 字段集恒空（口语转写不跑规格正则，弃权推进待人洗）。
4. **API `routes/clips.py`**：候选列表（含商品名/回执锚）+批量拣选，操作者鉴权。
5. **web**：ClipsPage（原型冻结交互逐字对齐：勾选/批量按钮/已登记卡 A-xxxx 跳治理台/单向不可再选；源录像 mock 口径页面披露）；侧栏「直播切片」；素材中心「切片汇入」页签（video+clip_pick 纯视图）；KIND_LABELS 补 video。
6. **测试**：单测+集成（闭环含发布→检索命中引用→汇入视图→409→401；种子幂等）。

## Owner 验收（浏览器点穿，全过）

勾选 C-0001/C-0002→「拣选登记（2 条）」→已登记 A-0011/A-0012→A-0011 发布（video 无字段集直接发布，标题/来源=切片拣选）→客服问「钛钢内胆一体成型」→真模型回答「钛钢内胆为一体成型，没有焊缝；泡柠檬水也不怕腐蚀」引用 **`A-0011 · v1`**→素材中心「切片汇入」页签两条可见。变体问句（「内胆什么材质会不会腐蚀」）未引 A-0011 属词法打分排序自然结果（A-0003/A-0009 分高），非缺陷。

## 两轴评审与处置（`docs/progress/clip-picking-review.md`）

零硬违规可合、六条 Must 全命中零越界；实修 docstring「拒绝原子性」措辞；记债三条（kind 分派散三处/list N+1/常量位置）。过程注记：Spec 轴首子代理被 provider 终止，后端结论+补审四项合并采信。

## 计数（摘自命令输出，junitxml 机械计数）

- `uv run ruff check apps packages`：All checks passed
- 仓库根无 DB：`tests=391 passed=292 failed/errored=0 skipped=99`
- 带 `SUITE_TEST_DATABASE_URL=…localhost:5433/suite_test`：`tests=391 passed=391 failed/errored=0 skipped=0`（基线 375 → 391 只增）
- web：`npm run build` 通过

## 遗留

- 评审债务三条（轻）；自动切出/ASR/真视频字节/切片撤销/源录像管理页 Out（部署刀语境）；goal 能力块 5/7（素材+切片已启动；考核/运营仍缺）。
- 第 17 刀素材评审债务七条与第 18 刀三条一并进审计刀 4 候看清单。

## 下一刀

刀计数：第 18 刀（审计后第 3 刀）。**第 19 刀=销售考核**（audit-3 排期：抽已发布对话出题+AI 扮客打分——第 12 刀 QA 资产已备原料；词条：考核=用已发布对话抽场景训练销售并打分，打的是人；_Avoid_ 客服评测集混用/上岗系统）；第 20 刀血缘视图；**审计刀 4 于第 20 刀后触发**。CONTEXT 推进句已回写。
