import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'

function mobileDevHttpsConfig() {
  const keyPath = process.env.MOBILE_DEV_HTTPS_KEY
  const certPath = process.env.MOBILE_DEV_HTTPS_CERT
  if (!keyPath || !certPath) return undefined
  if (!existsSync(keyPath) || !existsSync(certPath)) return undefined
  return {
    key: readFileSync(keyPath),
    cert: readFileSync(certPath),
  }
}

export default defineConfig({
  base: '/mobile/',
  plugins: [
    vue(),
    {
      name: 'mobile-spa-history-fallback',
      configureServer(server) {
        server.middlewares.use((req, _res, next) => {
          const url = req.url || ''
          const method = (req.method || 'GET').toUpperCase()
          const isPageRequest = method === 'GET' || method === 'HEAD'
          const isMobileRoute = url === '/mobile' || url.startsWith('/mobile/')
          const isApi = url.startsWith('/api/')
          const isViteInternal = url.includes('/@vite') || url.includes('/@id/')
          const isSourceFile = url.includes('/src/') || url.includes('/node_modules/')
          const hasExtension = /\.[a-z0-9]+($|\?)/i.test(url)
          if (isPageRequest && isMobileRoute && !isApi && !isViteInternal && !isSourceFile && !hasExtension) {
            req.url = '/mobile/'
          }
          next()
        })
      },
    },
  ],
  resolve: {
    alias: {
      '@': resolve(__dirname, './src'),
    },
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes('/src/pages/Payroll') || id.includes('/src/pages/Payslip')) {
            return 'payroll-pages'
          }
          if (id.includes('/src/pages/Asset') || id.includes('/src/pages/Tool')) {
            return 'asset-pages'
          }
          if (id.includes('/src/pages/Message') || id.includes('/src/pages/Approval') || id.includes('/src/layouts/AppTabs')) {
            return 'workflow-pages'
          }
          if (id.includes('/src/pages/Team') || id.includes('/src/pages/People') || id.includes('/src/pages/Recruitment')) {
            return 'team-pages'
          }
          if (id.includes('node_modules/vue') || id.includes('node_modules/pinia') || id.includes('node_modules/vue-router')) {
            return 'vue-vendor'
          }
          if (id.includes('node_modules/axios') || id.includes('@capacitor/core')) {
            return 'http-vendor'
          }
          if (id.includes('node_modules/ionicons')) {
            return 'ionicons-vendor'
          }
          if (id.includes('node_modules/@ionic/vue')) {
            return 'ionic-vue-vendor'
          }
          if (id.includes('node_modules/@ionic/core')) {
            return 'ionic-core-vendor'
          }
          if (id.includes('node_modules/@stencil')) {
            return 'stencil-vendor'
          }
          return undefined
        },
      },
    },
  },
  server: {
    host: '127.0.0.1',
    port: 5187,
    https: mobileDevHttpsConfig(),
    proxy: {
      '/api': {
        target: process.env.VITE_PROXY_TARGET || 'http://127.0.0.1:8186',
        changeOrigin: true,
      },
      '/uploads': {
        target: process.env.VITE_PROXY_TARGET || 'http://127.0.0.1:8186',
        changeOrigin: true,
      },
    },
  },
})
