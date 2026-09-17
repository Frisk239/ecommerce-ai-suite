# 第 100 刀 closeout：OFF 数据订正（审计刀 16 遗留债清偿）

日期：2026-09-17。分支 `feat/off-correct-100`（stacked 于 `feat/audit-19`）。

## 交付

1. **订正脚本** `scripts/realdata/correct_off_mismatch.py`：判据纯函数（token 相等/LCS≥4 词干互证/3 字符碰撞保护——Erdbeeren/BeerenBrüder 共 beeren 判 match、Graines/Nestle 的 nes 判 mismatch 的阈值定标实证）；dry-run 诊断 **14 错配/1 无锚/5 匹配**（20 份已发布 OFF）——错配根源=OFF 众包 dump 同行 name/brands 矛盾（TSV 缓存实证，导入脚本无 bug）。
2. **订正动作**：14 份标题改「{正文品牌} 规格（OFF）」——title 纯资产列不进版本快照、块不含标题、79 刀亲和实时读 title+85 刀快照键含 title 摘要（改列即改亲和+缓存自失效）——**无需重发布**（关键论断经评审代码级复核）；audit 14 行 `title_correct`；幂等。
3. **基线三段对账**：before 逐位=审计 19 → 订正直跑 75.0/56.0（问句锚过期）→ golden 维护 22 条问句商品名（期望锚不动，60 刀先例）→ **终表 positive 97.5/@3 100.0、overall 90.0/97.5**（+7.5pp=6 漂移回位+3 条 #90-P1 存量 miss 被更强亲和顺带治好——归属抽验吻合）；报告节含 cite_asset_title 键不进分母的核账发现。
4. **README 避开段改写**：具名 OFF 规格问句可演示（实测 Fitpiggy/Erdbeeren 各 2/2 同答带引用）；M&M white 全库确无对应资产（原「稳定作答」实为冒充——订正后诚实归宿二态）。
5. **评审实修**：pos-001 同义反复问句（「1MD Nutrition 的品牌是 1MD Nutrition 吗」→「这款 1MD Nutrition 的产品是什么牌子的」，期望锚不动，基线逐位不变）；行为面计数订正（3/3→2/2、3拒3澄清→4拒2澄清）。

## 证据

- Owner 门禁 **1476/0/0/0**（+8）+ ruff；**新基线 Owner 亲跑逐位复现**（97.5/100.0/90.0/97.5）。
- 稳定性：M&M white 6 次（不再引用错配值）；Fitpiggy/Erdbeeren 各 2/2 同答。

## 记债

1. **#100-P1**：M&M white 归宿二态（4 拒/2 澄清）根因=OOV 闸在中英混库的碎片断链漏判（ABCD 英文块 wh/hi/it/te 碎片）——改判据属检索代码，进 101 样本池。
2. title 截 200 可能截掉「规格（OFF）」后缀漏圈（当前库无此形态，观察）。

## 后续

第 101 刀：评测扩容+embedding 评估门（96→~200、表外探针第五分布、显式承接 #90-P1 收窄+#100-P1+库存口语变体；同义/表外 <70% 则向量立项）。
