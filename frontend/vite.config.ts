import react from '@vitejs/plugin-react'
import type { Plugin } from 'vite'
import { defineConfig as defineVitestConfig } from 'vitest/config'

const uiPort = 5173
const uiOrigin = `http://127.0.0.1:${uiPort}`
const apiOrigin = process.env.DEV_API_ORIGIN ?? 'http://127.0.0.1:8000'
const target = new URL(apiOrigin)

if (
  target.protocol !== 'http:' ||
  target.hostname !== '127.0.0.1' ||
  target.pathname !== '/' ||
  target.origin !== apiOrigin ||
  target.username ||
  target.password ||
  target.search ||
  target.hash
) {
  throw new Error('DEV_API_ORIGIN must be an HTTP origin on 127.0.0.1')
}

// The backend requires its own exact Origin and Host. This proxy is local-only,
// and rejects requests from other browser origins before forwarding them.
export function isAllowedDevApiRequest(method: string, host: string | undefined, origin: string | undefined): boolean {
  const changesState = !['GET', 'HEAD', 'OPTIONS'].includes(method)
  return host === `127.0.0.1:${uiPort}` && (!changesState || origin === uiOrigin)
}

function localApiGuard(): Plugin {
  return {
    name: 'oss-local-api-guard',
    configureServer(server) {
      server.middlewares.use('/api/v1', (request, response, next) => {
        if (!isAllowedDevApiRequest(request.method ?? 'GET', request.headers.host, request.headers.origin)) {
          response.statusCode = 403
          response.setHeader('Cache-Control', 'no-store')
          response.end('Forbidden')
          return
        }
        next()
      })
    },
  }
}

export default defineVitestConfig({
  plugins: [localApiGuard(), react()],
  server: {
    host: '127.0.0.1',
    port: uiPort,
    strictPort: true,
    proxy: {
      '/api/v1': {
        target: apiOrigin,
        changeOrigin: true,
        configure(proxy) {
          proxy.on('proxyReq', (request) => {
            request.setHeader('origin', apiOrigin)
            request.setHeader('host', target.host)
          })
        },
      },
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: './src/test/setup.ts',
    clearMocks: true,
  },
})
