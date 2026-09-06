// 端点函数表：页面只 import api.*，路径与形状集中在这一处。

import { request } from './client'
import type { AssetDetail, AssetListItem, AssetVersion, AuditEntry, Operator, Product } from './types'

export const api = {
  // 认证
  login: (username: string, password: string) =>
    request<Operator>('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    }),
  logout: () => request<{ detail: string }>('/auth/logout', { method: 'POST' }),
  me: () => request<Operator>('/auth/me'),

  // 资产
  listAssets: () => request<AssetListItem[]>('/assets'),
  getAsset: (assetId: number) => request<AssetDetail>(`/assets/${assetId}`),
  registerAsset: (form: FormData) => request<AssetDetail>('/assets/register', { method: 'POST', body: form }),
  retryMachineWash: (assetId: number) =>
    request<AssetDetail>(`/assets/${assetId}/retry-machine-wash`, { method: 'POST' }),
  confirmFields: (assetId: number, versionNo: number, fields: Record<string, string>) =>
    request<AssetVersion>(`/assets/${assetId}/versions/${versionNo}/fields`, {
      method: 'PATCH',
      body: JSON.stringify(fields),
    }),
  publishAsset: (assetId: number) =>
    request<AssetDetail>(`/assets/${assetId}/publish`, { method: 'POST' }),

  // 商品与留痕
  listProducts: () => request<Product[]>('/products'),
  getProduct: (productId: number) => request<Product>(`/products/${productId}`),
  listAudit: (assetId: number) => request<AuditEntry[]>(`/audit?assetId=${assetId}`),
}
