# 第 55 刀 closeout：四份数据集在产品面逐个可见（`feat/dataset-labels-55`）

日期：2026-09-11。依据：审计刀 10 的 **P1-3**——「四份真实数据集在产品面**不能**逐个区分：Wikidata / OpenFoodFacts / WANDS 三者在界面上都叫『开放数据集』」。迁移 **0028**。

## 交付

| 件 | 交付 |
| --- | --- |
| 枚举拆细 | `SOURCE_KINDS` += `wikidata` / `openfoodfacts` / `wands`（`open_dataset` **保留为通用类**：将来又接数据集时先用它兜底；本刀后库里不再有该值的行） |
| 迁移 0028 | 按与 0026 **同源、可重复**的稳定形态，把 `open_dataset` 的行拆到具体数据集：资产 `%规格（OFF）` → `openfoodfacts`；商品「WANDS 家具（演示）」→ `wands`、六类目 + 空 `spec_schema` → `wikidata`、食品 + 净含量单字段模板 → `openfoodfacts`。**只动 `source_kind='open_dataset'` 的行**（幂等）；down 原样收回 `open_dataset` |
| 前端词 | `labels.ts` 加「Wikidata」「OpenFoodFacts」「WANDS 基准」三词——资产列表/详情与商品卡自动出新词（第 50 刀已留好展示位） |
| 导入脚本 | 三个脚本写各自的数据集词（不再一律 `open_dataset`）：重导/重置库也能逐个可见 |
| 文档 | README「数据来源」表改为逐数据集（含 WANDS 的 1 件承载商品） |

## 验收（实测）

1. **库内分布**（演示库跑完 0028）：商品 `wikidata 91 / openfoodfacts 20 / seed 2 / wands 1`；资产 `openfoodfacts 20`（OFF 规格资产，其余资产不受影响）。
2. **界面**：商品页出现「Wikidata」「OpenFoodFacts」「WANDS 基准」三种来源 chip，**不再出现「开放数据集」**；资产列表的来源筛选 chips 与来源列同步（评论导入/切片拣选/会话回流/上传 + 已发布页签里的 OpenFoodFacts）。
3. **迁移可逆**：临时库 `upgrade head → downgrade 0027 → upgrade head` 往返通过；降级后三个数据集值归零、`open_dataset` 回到 3 行（商品）。
4. **门禁**：集成 **930 → 934 passed / 0 failed / 0 skipped**（新增 1 例迁移拆细 + 3 例随枚举扩值的既有参数化用例）；ruff 全过；前端 build 绿、lint 7/0。

## 诚实披露

- **`open_dataset` 成了「有词无用」的预留值**：它保留在枚举里作为通用类（将来接新数据集时的兜底），但当前库里没有任何行使用——不删是因为它承载「开放数据集」这一类语义（ADR/词条里写着），删了下次接数据集还得再加回来。**代价**：来源词表从 8 值变 11 值，`SOURCE_KIND_LABELS` 里有一个词暂时不会出现在界面上。
- **拆细靠形态判据**（与 0026 同源）：手建同形态行会被打上数据集标签；当前 API 强制类目模板，运营造不出 `{}`（与第 50 刀同一已记债）。
- **只拆了「谁带进来的」**：数据集的许可/出处仍在 `scripts/realdata/README.md`，产品面不展示（Out）。
- 端到端证据是**本会话实测输出**（SQL 分布 + 界面文本回显），未落成截图文件。

## 后续

- **审计刀 11 于本刀后按节奏开**（第 51–55 刀：工作队列筛选 / 类目问价 / CSAT 留言可回溯 / widget origin / 数据集逐个可见）。
- 剩余待裁决/待做：**类目名别名**（「笔记本」→「笔记本电脑」）、**评分可改**、**工作队列默认视角**（首屏仍 194 行）。
