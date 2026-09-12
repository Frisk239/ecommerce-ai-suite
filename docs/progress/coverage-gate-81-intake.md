# 第 81 刀 intake（对审计刀 16）+ 对齐

日期：2026-09-11。Owner 裁决：**覆盖闸误拒治本**（探针资产处置、部署面、生命周期指标小刀均让位）。

## 对审计刀 16 的 intake

| 项 | 结果 |
| --- | --- |
| Merge | `feat/audit-16` 已进 main（PR #116） |
| Evidence | 1180 passed / 0 skipped（Owner 亲跑 junitxml）；评测逐位不变 87.5/86.2；P0×2/P1×4 全实修（裸覆盖标记 fullmatch 收口、并发回流孤儿 0042 补偿收口+钉子等） |
| Spec vs claim | 三轴 closeout 对账（A 0/2/5、B 0/1/6、C 2/3/4）自洽；演示库外科清 7 空会话+2 孤儿已披露 |
| Safety | 无密钥；探针残留已清（会话 148、孤儿 0） |

**通过**。结转记债（本刀不吞，后续裁）：title 命中下 no_coverage 误拒（本刀主题）、OOV 实体拒答分支、`service_session_transitions_total` 指标、亲和 title_terms 缓存（规模触发）、end 独立限流闸。

## 本刀对齐（用户裁决主题：覆盖闸误拒）

**症状**（审计刀 16 C 轴实测）：具名 OFF 商品问句「M&M white的条码是多少」6 次 3 答 3 拒；同问检索稳定命中 A-0226（标题含 M&M white、chunk 含「条码：000000000063」）。

**待测机理（先测量后施工，75/79 刀纪律）**：
1. 检索侧（确定性）：`retrieve()` top-k 是否稳定、`coverage_ratio(question, chunks)` 数值、hits 数——覆盖闸触发条件 `coverage_ratio < 0.4 且 hits≤1`（40 刀 FIDELITY_MIN_COVERAGE）。
2. 生成侧（随机）：`fallback_reason` 分布（coverage=忠实度闸降级 / no_coverage=模型自述未覆盖收口 / None=正常）——「时答时拒」的随机源是模型措辞还是闸确定性触发。
3. 数据侧：OFF 标题/正文品牌错配（title M&M white vs 正文品牌 Fitpiggy）对覆盖度的贡献。

**假设（待测验证）**：问句的**商品名部分**（拉丁 bigram「M&/&M/wh…」）不进中文规格块 → 覆盖度分母被商品名稀释 → 阈值误触发。若成立，正解方向是「覆盖度口径排除问句中的实体部分（或 title 强命中时豁免）」，与 79 刀实体亲和信号同源。

**Must**：机理测量有数字；修法有 before/after（检索层 run_eval + **引擎级金标 golden_engine 用例**——生成闸 run_eval 覆盖不到，是本刀必须补的测法）；live 复现同一问句稳定可答；审计刀 16 的 P0（[未覆盖] 收口）不回归。
**Out**：OFF 导入数据的品牌回填（数据手术，若需要单列）；OOV 实体拒答分支（另刀）；不是「把闸调松了事」——改动必须能论证不制造新错误答案。
