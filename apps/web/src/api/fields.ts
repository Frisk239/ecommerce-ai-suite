// 字段取值规则：与后端 publishing.py 同口径（confirmed 优先，其次机洗非弃权值）。
// 写回预告 / 详情展示共用，禁止两处各写一套。

import type { AssetVersion, FieldEntry } from './types'

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

/** 发布取值：人洗确认值优先，其次机洗值；弃权与缺失都是 null。 */
export function resolveFieldValue(version: AssetVersion, field: string): string | null {
  return entryValue(version.confirmed_fields[field]) ?? entryValue(version.extracted_fields[field])
}

export interface FieldView {
  field: string
  required: boolean
  /** 当前生效值（confirmed 优先）；null = 弃权或未抽取 */
  value: string | null
  source: 'human' | 'machine' | null
  abstained: boolean
}

export function toFieldView(version: AssetVersion, field: string, required: boolean): FieldView {
  const confirmed = entryValue(version.confirmed_fields[field])
  if (confirmed !== null) {
    return { field, required, value: confirmed, source: 'human', abstained: false }
  }
  const extracted = version.extracted_fields[field]
  const machine = isAbstained(extracted) ? null : entryValue(extracted)
  if (machine !== null) {
    return { field, required, value: machine, source: 'machine', abstained: false }
  }
  return { field, required, value: null, source: null, abstained: isAbstained(extracted) }
}
