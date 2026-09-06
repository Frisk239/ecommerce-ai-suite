import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// 开发代理：业务路由原样透传（后端 auth/assets/products/audit 本就挂在 /api 下）；
// 唯一例外 /api/health —— 脚手架期探针路径，后端 health 挂在 /health，剥前缀转发。
// 本地裸跑时目标为 localhost:8000；compose 里用 API_PROXY_TARGET 指向 api 服务。
const proxyTarget = process.env.API_PROXY_TARGET ?? 'http://localhost:8000'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      '^/api/health': {
        target: proxyTarget,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
      '/api': {
        target: proxyTarget,
        changeOrigin: true,
      },
    },
  },
})
