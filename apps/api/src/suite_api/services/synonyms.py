"""电商同义词表 + 查询改写（第 36 刀接线：检索查询侧归一）。

第 36 刀起 ``retrieve()`` 入口对 query 先过 ``apply_synonyms`` 再进既有词法
管线（search-time 查询侧归一，ES synonym 惯例同款；块侧不动）——接线前的
评测数字（docs/research/rag-eval-report.md）即接线效果的 before 基线；多轮
记忆的代词拼接检索词走同一 retrieve 入口，同受益。35 刀改写器产同义问的
用法不变。

表形状：互为同义词的组（组内任意成员语义等价）。``apply_synonyms`` 做组内
轮转改写（保修->质保->三包->保修……）：无论顾客问句里用的是组里哪个说法，
都换成另一个——查询侧归一正是靠「同义不同词」把问句词换一位去撞回原文
证据。替换走最长匹配优先的单遍扫描：短词是长词子串时（「保温」⊂「保温瓶」）
先试长词，避免「保温瓶」被「保温->保暖」规则撕成「保暖瓶」。
"""

# ~15 组核心电商同义词（组首 = canonical，改写/归一的目标词）。
# 选取口径：演示库语料真实出现的词（保温杯/净含量/物流/退货…）+ 高频售后词
# （保修/运费/发票…），每组都是顾客口语里互换的说法。
SYNONYM_GROUPS: tuple[tuple[str, ...], ...] = (
    ("保修", "质保", "三包"),
    ("运费", "邮费"),
    ("退货", "退换"),
    ("发货", "出库"),
    ("净含量", "容量"),
    ("物流", "快递"),
    ("规格", "参数"),
    ("保质期", "保存期"),
    ("评论", "评价"),
    ("客服", "售后"),
    ("优惠", "折扣"),
    ("发票", "收据"),
    ("保温杯", "保温瓶"),
    ("味道", "口感"),
    ("秒杀", "闪购"),
    ("账号", "账户"),
)

# 附加组：与其他成员存在前缀关系的词对——它们存在的意义是钉死最长匹配优先
# 的扫描语义（「折扣券」必须整体轮转，不得被「折扣->优惠」撕成「优惠券」）。
_SUBSTRING_GROUPS: tuple[tuple[str, ...], ...] = (("折扣券", "优惠券"),)

# 改写规则表：组内成员两两轮转（a->b、b->c、c->a），按源词长度降序（最长匹配优先）。
# 轮转而非归一到组首的原因：语料原文大多用组首词（资产原文用什么词，问句就有什么
# 词），归一则恒等；轮转保证任何成员的问句都被换成同组另一个说法——35 刀改写器
# 用它产同义问，36 刀检索查询侧用同一函数把改写词再换一位、撞回原文证据。
_SYNONYM_RULES: tuple[tuple[str, str], ...] = tuple(
    sorted(
        (
            (group[idx], group[(idx + 1) % len(group)])
            for group in (*SYNONYM_GROUPS, *_SUBSTRING_GROUPS)
            for idx in range(len(group))
        ),
        key=lambda rule: len(rule[0]),
        reverse=True,
    )
)


def apply_synonyms(text: str) -> str:
    """同义词改写：任何组成员出现都轮转成同组下一个说法（双向——哪个词进都换）。

    最长匹配优先的单遍扫描（见模块 docstring）；不在任何组里的字符原样保留。
    纯函数；非幂等是轮转语义的必然（保修->质保->三包），以表数据为准。
    """
    if not text:
        return text
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        for src, dst in _SYNONYM_RULES:
            if text.startswith(src, i):
                out.append(dst)
                i += len(src)
                break
        else:
            out.append(text[i])
            i += 1
    return "".join(out)
