# 第 105 刀 closeout：向量基础设施（A1——只备料不动检索）

日期：2026-09-17。分支 `feat/vector-infra-105`（stacked 于 104）。

## 交付

1. **迁移 0034**：`retrieval_chunks.embedding vector(1024)` nullable + **HNSW cosine**（pgvector 0.8.5；空表起步即有效优于 ivfflat）+ CREATE EXTENSION 全裸 SQL；down 卸净。
2. **`services/embedding.py`**：EMBED_* 三 env fail-closed（默认 SiliconFlow bge-m3）；批量 ≤64 自动分批；维度自检（非 1024 拒）。四件套同构（asr/vlm/imggen/tts）。
3. **写端事务纪律**：块写在发布事务内→ embedding 云调用挪 **commit 后同步补写**（`embed_version_chunks` 独立小事务）；失败 rollback+日志+return 0——**发布永不被 embedding 阻塞**（钉测）。
4. **回填脚本**：NULL-only 幂等（二跑 0 待补）；替身实测 598/598 全 1024 维。
5. **「只备料」终验**：run_eval 与 104 终表**逐字一致**（90.5/86.7）+208 条 top3 逐位 diff 零差；retrieve() 读路径零改动（评审 diff 级确证）。

## 证据

- Owner 门禁 **1557/0/0/0**（+14）+ ruff；评审六项全过无 P0。

## 评审实修（三条 P2 文档债）

- roadmap 106 节补**硬前置声明**（真 EMBED key 回填依赖）+ **ef_search 调参**进矩阵 + **NULL 块策略显式化**（向量只加分不排除，fail-open 词法基线）。

## 记债

1. **106 硬前置=真 EMBED_API_KEY**（SiliconFlow 免费）——演示库全 NULL 待回填（**用户 key 清单第 5 把**）。
2. 回填幂等的 SQL 字符串断言脆（行为级二跑测试留间隙）。

## 后续

第 106 刀：融合矩阵（三形态×ef_search 档位；真 key 回填后开工）。
