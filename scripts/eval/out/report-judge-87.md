# RAG 评测尺报告（脚本骨架，数字由 run_eval.py 生成）

- 日期：2026-09-16
- 演示库：`postgresql://suite:suite@localhost:5433/suite`
- golden：`E:\code\ecommerce-ai-suite\scripts\eval\out\golden_large.json`（96 条，种子 42 生成）
- LLM judge：已启用：answered 条目 80/80 条评上

```
分布             条数  recall@1  recall@3     拒答率     误拒率    混淆@1     忠实度    评/答
positive       40     90.0%     90.0%       -    0.0%       -   10.0%  40/40 
paraphrase     25     88.0%     96.0%       -       -       -   12.0%  25/25 
confusion      15     73.3%     93.3%       -       -   73.3%    6.7%  15/15 
refusal        16         -         -  100.0%       -       -       -   0/0  
overall        96     86.2%     92.5%  100.0%    0.0%   73.3%   10.0%  80/80 
```
