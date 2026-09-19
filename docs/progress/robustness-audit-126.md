# 第 126 刀：健壮性审计（静默失败同族三路，只读+顺手实修）

日期：2026-09-19。范围：第 124 刀实证的「静默跳过」族——脚本面（异常吞掉/条件跳过无声/盲序取值）、服务面（except 降级无观测）、端到端面（日志声明 vs 库内真相）。方法：模式扫描（except 体无 print/log/raise 判静默）+ 定向复核 + 重灌全链对照。

## 结论

**P0×0，P1×0，P2×2（已顺手实修），P3×3（留档）**。静默失败族在脚本与服务面没有第二个「真相错位」级问题；124 刀修根后重灌全链的日志声明与库内真相逐项对上。

## 脚本面（scripts/ + scripts/realdata/，17 文件扫描）

except 块静默判据扫描 → 7 处命中，其中 5 处有明确意图注释且**有观测面**（notes.append / verdict 记录 / last_error），2 处纯 pass 但均为清理/审计精度场景：

- `demo_reset.py:239` `except Exception: pass`（存储侧失败不挡删行）——删行报告不反映孤儿字节。P3。
- `data_health_check.py:820` 别名表缺位 pass（只影响分类精度）——有注释 rationale。P3。

`next(..., None)` 查找后静默跳过形态 → 2 处（reseed 的商品查找/资产锚），**124 刀已修为有声**。`[:N]` 盲序取值 → reseed 拣选 1 处，**124 刀已修为按录像过滤**。

## 服务面（services/ 18 文件扫描）

except 体无 logger/raise/note → 5 处命中，逐一复核：

| 位置 | 形态 | 定级 | 处置 |
| --- | --- | --- | --- |
| `registration.py:237` | 机洗失败 → status=INGESTED + last_error | 误报（last_error 即观测面，UI 可见） | — |
| `retrieval.py:929` | 替身 db 无 bind → 缓存键退通用值 | 误报（有注释，测试替身场景） | — |
| `asr.py:309` | 段时间戳解析失败 → continue 丢段 | 有全局兜底（零段+有文本→raise），段级丢失无痕 | P3 留档 |
| `coach_roleplay.py:186` | 整改建议 JSON 解析失败 → remediation 置空 | 操作者只见空白不知因 | **P2 已修**（logger.warning） |
| `video_compose.py:1427` | 图片尺寸探测失败 → (0,0) 进草稿 | 草稿排版被压扁且无痕 | **P2 已修**（logger.warning） |

## 端到端面（重灌链日志 vs 库内真相对照）

124 刀修根后全链重灌，逐项对账：日志声明的候选数（Dell 12/Samsung 60）= 库内行数；拣选 id（C-5/6/7、C-17..20）= registered 行；发布数（3+4）= published 视频资产；对话发布 5 = 库内 5 published；种子候选未被劫持（recording_id 全空）= 收窄语义生效。**零声明-真相偏差。**

上一轮发现的声明-真相偏差（对话步骤参数错静默「完成」、洗帧无声跳过、✓ 打在 0 候选帧上）全部已修；demo_prepare 在生产库下的 23 项 MISS 已在 reseed verify 步加口径说明（演示库 vs 生产库两套期望）。

## 测试面顺手件

`test_retrieve_entity_affinity_overrides_shorter_rival` 的 baseline 自检隐含「干净语料」假设——集成环境整模块跑时早前用例资产插队打红（与 124 刀改动无关，纯净 main 复现）。加固为**两资产配对断言**（语料无关），意图不变。

## 留档（P3，不排刀）

- asr 段级解析丢失加 debug 级观测（收益低）；
- demo_reset 删行报告补孤儿字节计数（清理脚本观感）;
- Wikidata 类目采样把概念条目（「视频图形阵列」VGA）当商品灌入——fetch 侧黑名单或人洗过滤，生产库数据质量项。
