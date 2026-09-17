// 多词检索（第 114 刀 B，W9/W10 共用口径）：空格分词 + AND 匹配。
// 单词=子串（原行为不变）；多词=每个词都要命中（任一字段）。真分词/拼音留 Out
// ——这是「退货」搜不到《退换货政策》（退-货 不连续子串）的最低成本出路：
// 提示用户改搜「退 货」（两词分别命中），而不是悄悄放宽成 OR 把命中集炸开。

/** 查询词拆分：小写、去空；空查询 → []（调用方以 length===0 判「未搜索」）。 */
export function splitTerms(query: string): string[] {
  return query
    .trim()
    .toLowerCase()
    .split(/\s+/)
    .filter((term) => term !== '')
}

/** AND 语义：每个词都要在**任一** haystack 里是子串（字段集合由调用方给，
 * 如 [标题, 裸 ID, A-0000 形态]）。词 × 字段交叉命中，一词不中即整行出局。 */
export function matchesAllTerms(haystacks: string[], terms: string[]): boolean {
  return terms.every((term) => haystacks.some((text) => text.includes(term)))
}
