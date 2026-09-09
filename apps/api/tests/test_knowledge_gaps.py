"""缺口归一化纯函数单测（第 30 刀 Must 5）：normalize_question 各态——

空白/全半角标点/尾部问句标点循环去；归一化只做查重键（question 原问列与
出口视图不经本函数——那两条契约由 test_service_integration 的幂等/出口掩
用例钉死）。无 DB：纯字符串进出。
"""

from suite_api.services.knowledge_gaps import normalize_question


def test_normalize_strips_surrounding_whitespace() -> None:
    assert normalize_question("  会员积分怎么兑换  ") == "会员积分怎么兑换"


def test_normalize_removes_trailing_fullwidth_question_mark() -> None:
    # 走查实证形态（G-0001「…兑换？」vs G-0003「…兑换」）：差一个尾问号同一缺口
    assert normalize_question("会员积分怎么兑换？") == "会员积分怎么兑换"


def test_normalize_removes_trailing_halfwidth_question_mark() -> None:
    assert normalize_question("会员积分怎么兑换?") == "会员积分怎么兑换"


def test_normalize_removes_trailing_punctuation_run() -> None:
    # rstrip 按集合循环去，直到第一个非集合字符（全半角混排同样去净）
    assert normalize_question("几点上班？！?。；;，、") == "几点上班"


def test_normalize_keeps_internal_punctuation_but_unifies_width() -> None:
    # 内部标点保留、只统一全角→半角（，→, 、→, （）→() ：→: ～→~）
    assert normalize_question("退货（七天）内，能换吗？") == "退货(七天)内,能换吗"
    assert normalize_question("发票、抬头怎么改：说明一下") == "发票,抬头怎么改:说明一下"
    assert normalize_question("优惠～到几号") == "优惠~到几号"


def test_normalize_does_not_touch_letters_digits_or_head_punctuation() -> None:
    # 字母数字不动；句中问号不是尾部标点，保留（只去尾不去头/中）
    assert normalize_question("A42 是什么意思") == "A42 是什么意思"
    assert normalize_question("什么意思？说说") == "什么意思?说说"


def test_normalize_all_punctuation_question_to_empty() -> None:
    # 全标点问句归一化为空串（边界：上游保证 question strip 后非空，这里只钉
    # 函数行为——空串作为查重键仍唯一，不会误并到正常问句）
    assert normalize_question("？？？") == ""
    assert normalize_question("  ？!  ") == ""
