// 字段取值规则：与后端 publishing.py 同口径（confirmed 优先，其次机洗非弃权值）。
// 写回预告 / 详情展示共用，禁止两处各写一套。
// 第 12 刀（ADR 0035）：dialogue 的 qa_pairs 值是数组，单值口径（entryValue/
// resolveFieldValue）对它返回 null（不是字符串），由 entryQaPairs 专用读取。

import type { AssetVersion, FieldEntry, QaPair } from './types'

export function entryValue(entry: FieldEntry | undefined): string | null {
  if (entry === undefined) return null
  if ('value' in entry && typeof entry.value === 'string' && entry.value.trim() !== '') {
    return entry.value
  }
  return null
}

export function isAbstained(entry: FieldEntry | undefined): boolean {
  return entry !== undefined && 'abstained' in entry
}

/** 读 qa_pairs 结构化值（数组）；非数组或缺失返回 null。空数组=确认「没有 QA」。 */
export function entryQaPairs(entry: FieldEntry | undefined): QaPair[] | null {
  if (entry === undefined || !('value' in entry) || !Array.isArray(entry.value)) return null
  return entry.value.map((pair) => ({
    q: typeof pair.q === 'string' ? pair.q : '',
    a: typeof pair.a === 'string' ? pair.a : '',
  }))
}

/** 发布取值：人洗确认值优先，其次机洗值；弃权与缺失都是 null。 */
export function resolveFieldValue(version: AssetVersion, field: string): string | null {
  return entryValue(version.confirmed_fields[field]) ?? entryValue(version.extracted_fields[field])
}

export interface FieldView {
  field: string
  required: boolean
  /** 当前生效值（confirmed 优先）；null = 弃权、未抽取或结构化值（见 qaPairs） */
  value: string | null
  /** qa_pairs 等结构化数组值（confirmed 优先）；null = 非数组字段 */
  qaPairs: QaPair[] | null
  source: 'human' | 'machine' | null
  abstained: boolean
  inherited: boolean
}

export function toFieldView(version: AssetVersion, field: string, required: boolean): FieldView {
  const confirmedEntry = version.confirmed_fields[field]
  const confirmed = entryValue(confirmedEntry)
  const confirmedPairs = entryQaPairs(confirmedEntry)
  if (confirmed !== null || confirmedPairs !== null) {
    return {
      field,
      required,
      value: confirmed,
      qaPairs: confirmedPairs,
      source: 'human',
      abstained: false,
      inherited: confirmedEntry !== undefined && 'inherited' in confirmedEntry && confirmedEntry.inherited === true,
    }
  }
  const extracted = version.extracted_fields[field]
  const machinePairs = entryQaPairs(extracted)
  const machine = isAbstained(extracted) ? null : entryValue(extracted)
  if (machine !== null || machinePairs !== null) {
    return {
      field,
      required,
      value: machine,
      qaPairs: machinePairs,
      source: 'machine',
      abstained: false,
      inherited: false,
    }
  }
  return {
    field,
    required,
    value: null,
    qaPairs: null,
    source: null,
    abstained: isAbstained(extracted),
    inherited: false,
  }
}
