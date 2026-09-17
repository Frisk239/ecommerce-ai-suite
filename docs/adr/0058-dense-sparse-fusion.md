# ADR 0058：稠密稀疏融合形态定案（加权线性 + cosine 闸 + 词法空手闸）

- 日期：2026-09-17（第 106 刀）
- 状态：接受
- 相关：ADR 0018（无证据不答——lexgate 是它在融合层的延伸）、0023（词法检索
  工程标定）、0048（实体亲和重排——affinity_before 保持乘数原位）、迁移 0034
  （pgvector vector(1024) + HNSW，105 刀备料）；roadmap 第 106 节；数字证据
  docs/research/rag-eval-report.md 第 106 刀节（矩阵工件
  scripts/eval/out/106-fusion-matrix.txt）

## 背景

105 刀给 598 个当前指针版切块全量回填 bge-m3 向量（检索读路径零改动）。106 刀
要在 236 条六分布评测集上对照三种稠密稀疏混合形态，矩阵数字定案后把胜者实现进
`retrieve()`。硬红线：正例 recall@1 98.8（79/80）与 refusal 拒答率 86.7（26/30）
任一回归即回滚。

## 决定

### 1. 三形态矩阵（661 配置）与胜者

实验脚本 `scripts/eval/fusion_matrix.py`（融合纯函数在脚本内，胜者才进服务）：

- **A 级联兜底**：词法 top1 ≥ T 纯词法，否则向量组前置补召回（T ∈ {1,2,3,5}）；
- **B 并联 RRF**：词法 top-10 + 向量 top-10 排名融合，score = 1/(k+rank_lex) +
  w/(k+rank_vec)，k=60（w ∈ {0.3,0.5,0.7}）；
- **C 加权线性**：词法分 per-query min-max 归一 + w×cosine（w ∈ {0.2,0.5,1,2}）；
- 各 × ef_search ∈ {40,80,200} × 亲和两型（before/after）× TAU × 闸。

**胜者：C 加权线性 w=0.5 + TAU=0.60 + lexgate + affinity_before**（ef 用 pgvector
默认 40——三档实测零行为差异，598 行小表 HNSW 近全扫，语料破万再调）。矩阵 33/660（评审订正：原报 34 系脚本分子含 baseline 的 off-by-one）
保红线配置全部是 C-before-lexgate；胜者 overall@1 89.8（基线 88.8）为红线内最高。
形态规律：A 全面最差（向量组前置破坏词法主路）、B 中游（排名融合丢分数信息）、
C 唯一全族保红线；affinity_after 全线差 2-6pp（丢失「亲和进词法分」的耦合）。

### 2. 两条纪律闸（两轮实测教训的产物，缺一拒答率崩）

- **TAU cosine 下限（0.60）**：无闸时全形态拒答率崩 0——向量近邻永远存在，
  词法零命中的 refusal 问句被强行灌进证据。但全局阈值封顶只救回 56.7%
  （TAU=0.65）：实测 refusal 组向量 top1 cos（0.501-0.754）与 cite 组期望资产
  （0.402+）完全重叠，bge-m3 在本多语言杂语料上**无完美可分阈值**——TAU 只
  负责「低分向量块不干扰融合排序」，不负责拒答。
- **lexgate 词法空手闸**：拒答崩 0 的唯一机制是「词法零命中时向量无中生有」
  （refusal 26/30 词法零命中；cite 组词法 recall@3 96.6）。词法命中非空才让
  向量路参与——拒答语义**完全由词法路决定**（0018 宁缺勿滥在融合层的延伸），
  融合收益全保留（词法命中的问句向量照常加分/补召回）。

### 3. NULL 块 fail-open（roadmap 显式要求）

向量路只加分/补召回，**不排除任何块**：embedding NULL（未配置期发布/回填失败）
的块在词法路照常参与（cos 项为 0）；NULL 块从「向量补召回」缺席但绝不因无向量
被剔除。词法命中非空时整库 NULL 也不改变「有证据可答」的判定。

### 4. 实现面（services/retrieval.py，四出口单点）

- `retrieve()` 尾部融合：词法管线（打分/stale/79 刀亲和乘数——affinity_before
  原位不动）命中集 min-max 归一 + `VECTOR_WEIGHT(0.5) × cosine`；向量近邻
  `dense_candidates()`（pgvector `<=>` cosine 距离，`ORDER BY embedding <=> :qv
  LIMIT 10`，TAU 下推 SQL WHERE，评论适用域闸同口径过滤）；词法空手闸在
  retrieve 单点收口。引擎双通道/MCP search_published/缺口验证/评测四出口同语义
  （全部经 retrieve 单点）。
- **查询向量缓存**：`(库 URL, embed 模型, 问句)` 进程内 LRU（512 条，维度自检
  ——换 EMBED_MODEL 旧缓存作废重算）。同问句不重复调云 API；语料变化不影响
  查询向量（纯函数于问句文本），LRU 淘汰即可。
- **EMBED key 未配置/嵌入失败/向量查询失败 = 纯词法零变化**（fail-open）：
  归一化单调保序，无 key 路径名次与 79 刀管线逐位一致（空凭证 run_eval 236 条
  全格=107a 基线的实测钉）。

## 后果

- 全量 236 条：overall@1 88.8→89.8 / @3 96.6→97.1 / MRR 0.9248→0.9320；混淆@1
  64.0→72.0（+2）；sem_neg@3 85.7→92.9（近邻 @3 miss 4 条救 2：nb-005/006；
  @1 近邻 6 miss 救 0——nb-003/004 仍是词法+向量都救不动的深 miss）；正例
  98.8@1 与拒答 86.7 **双双保持**。代价：混淆@3 96.0→92.0（conf-007 被向量块
  挤出 top3，全矩阵唯一退步格，小于收益）。
- sem_neg 近邻 nb-003/004 的残余 miss 留给 107b 消融终表观察（本刀 Out：不修
  检索语义之外的东西）；TAU/权重为常量，语料或换嵌入模型后重跑矩阵校准。
- 测试：`test_fusion_matrix_tools.py`（脚本纯函数三形态：级联阈值边界/RRF 排名
  融合/线性归一/亲和后置）+ `test_fusion_retrieval.py`（服务融合=实验同名次/
  向量补召回形状/NULL 块保留/lexgate/评论闸向量路/无 key 零变化钉测/查询向量
  缓存与失败退化）。


## 泛化边界（评审 P2 补记）

TAU=0.60 与 w=0.5 为**同集（236 条）定参**——+1.0pp overall 属样本内收益，无留出集验证。换语料/换 embedding 模型须重跑 `fusion_matrix.py` 校准（拒答红线由无参数的 lexgate 语义闸保护、与 TAU 无关，换语料仍站得住；TAU 只管排序质量）。归一几何：min-max 使词法 top1 恒 1.0、向量补召块上限 ~0.43——conf +8pp 来自期望资产 rank2 归一分+cos 越过 1.0 的真信号，非阈值效应。
