// 后端契约类型（apps/api routes 的响应模型对照，改动需与 API 票同步）。

export interface Operator {
  id: number
  username: string
}

export interface ProductRef {
  id: number
  name: string
  category: string
}

export type AssetStatus = 'ingested' | 'pending_review' | 'published'

export interface AssetListItem {
  id: number
  title: string | null
  kind: string
  status: AssetStatus
  product: ProductRef | null
  last_error: string | null
  current_published_version_no: number | null
}

/** 字段值三形状：机洗 {value,source:"machine"} / 弃权 {abstained:true} / 人洗 {value,source:"human"} */
export type FieldEntry = { value: string; source: 'machine' | 'human' } | { abstained: true }

export interface AssetVersion {
  version_no: number
  object_key: string
  extracted_fields: Record<string, FieldEntry>
  confirmed_fields: Record<string, FieldEntry>
  published_at: string | null
}

export interface Publishability {
  publishable: boolean
  missing: string[]
  unconfirmed: string[]
}

export interface AssetDetail extends AssetListItem {
  versions: AssetVersion[]
  publishability: Publishability
}

export interface SpecRule {
  required?: boolean
}

export interface SpecValueEntry {
  value: string
  source: { asset_id: number; version: number }
}

export interface Product {
  id: number
  name: string
  category: string
  spec_schema: Record<string, SpecRule>
  spec_values: Record<string, SpecValueEntry>
}

export interface AuditEntry {
  id: number
  operator_id: number
  asset_id: number
  version_no: number
  action: string
  created_at: string
}
