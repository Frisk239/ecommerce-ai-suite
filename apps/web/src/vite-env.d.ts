/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** 生产构建直连 API 时使用（如 https://api.example.com）；缺省走 dev proxy /api */
  readonly VITE_API_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
