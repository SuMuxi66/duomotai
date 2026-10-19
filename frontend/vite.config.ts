import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import fs from 'fs'
import path from 'path'
import { networkInterfaces } from 'os'

/**
 * 获取本机局域网 IP 地址
 */
function getLocalIpAddress(): string {
  const nets = networkInterfaces()
  for (const name of Object.keys(nets)) {
    for (const net of nets[name] || []) {
      // 优先返回 IPv4 局域网地址
      if (net.family === 'IPv4' && !net.internal) {
        if (net.address.startsWith('192.168.') || 
            net.address.startsWith('10.') ||
            /^172\.(1[6-9]|2[0-9]|3[01])\./.test(net.address)) {
          return net.address
        }
      }
    }
  }
  return 'localhost'
}

const localIp = getLocalIpAddress()
console.log(`[Vite Config] 自动获取本机 IP: ${localIp}`)

function readEnvFile(envPath: string): Record<string, string> {
  if (!fs.existsSync(envPath)) {
    return {}
  }

  const result: Record<string, string> = {}
  const content = fs.readFileSync(envPath, 'utf-8')

  for (const rawLine of content.split(/\r?\n/)) {
    const line = rawLine.trim()
    if (!line || line.startsWith('#')) {
      continue
    }

    const separatorIndex = line.indexOf('=')
    if (separatorIndex === -1) {
      continue
    }

    const key = line.slice(0, separatorIndex).trim()
    const value = line.slice(separatorIndex + 1).trim()
    result[key] = value
  }

  return result
}

function readMergedEnv(...envPaths: string[]): Record<string, string> {
  return envPaths.reduce<Record<string, string>>((acc, envPath) => {
    return { ...acc, ...readEnvFile(envPath) }
  }, {})
}

const frontendEnv = readMergedEnv(
  path.resolve(__dirname, '.env'),
  path.resolve(__dirname, '.env.local'),
)

const backendEnv = readMergedEnv(
  path.resolve(__dirname, '..', 'backend', '.env'),
  path.resolve(__dirname, '..', 'backend', '.env.local'),
)
// 将 VITE_FRONTEND_PORT 默认值从 5173 更新为 5179
const frontendPort = Number.parseInt(frontendEnv.VITE_FRONTEND_PORT || '5179', 10)
const backendPort = Number.parseInt(backendEnv.BACKEND_PORT || '8010', 10)

// 后端始终使用 localhost，避免代理 IP 不匹配问题
const backendHost = '127.0.0.1'
const frontendCertPath = path.resolve(__dirname, 'certs/cert.pem')
const frontendKeyPath = path.resolve(__dirname, 'certs/key.pem')
const hasFrontendHttpsCerts = fs.existsSync(frontendCertPath) && fs.existsSync(frontendKeyPath)

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'src'),
    },
  },
  server: {
    host: '0.0.0.0',
    port: frontendPort,
    strictPort: true,
    https: hasFrontendHttpsCerts
      ? {
          key: fs.readFileSync(frontendKeyPath),
          cert: fs.readFileSync(frontendCertPath),
        }
      : undefined,
    proxy: {
      '/api': {
        target: `http://${backendHost}:${backendPort}`,
        changeOrigin: true,
        secure: false,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
      '/ws': {
        target: `ws://${backendHost}:${backendPort}`,
        changeOrigin: true,
        secure: false,
        ws: true,
      },
      '/temperature': {
        target: `http://${backendHost}:${backendPort}`,
        changeOrigin: true,
        secure: false,
      },
      '/weight': {
        target: `http://${backendHost}:${backendPort}`,
        changeOrigin: true,
        secure: false,
      },
      '/config': {
        target: `http://${backendHost}:${backendPort}`,
        changeOrigin: true,
        secure: false,
      },
      // 端侧语音服务（KWS/ASR）地址自动发现：后端扫描局域网后下发
      '/edge': {
        target: `http://${backendHost}:${backendPort}`,
        changeOrigin: true,
        secure: false,
      },
      '/chat': {
        target: `http://${backendHost}:${backendPort}`,
        changeOrigin: true,
        secure: false,
      },
      '/fan': {
        target: `http://${backendHost}:${backendPort}`,
        changeOrigin: true,
        secure: false,
      },
      '/env': {
        target: `http://${backendHost}:${backendPort}`,
        changeOrigin: true,
        secure: false,
      },
      '/log': {
        target: `http://${backendHost}:${backendPort}`,
        changeOrigin: true,
        secure: false,
      },
      '/recipe': {
        target: `http://${backendHost}:${backendPort}`,
        changeOrigin: true,
        secure: false,
      },
      '/models': {
        target: `http://${backendHost}:${backendPort}`,
        changeOrigin: true,
        secure: false,
      },
      '/providers': {
        target: `http://${backendHost}:${backendPort}`,
        changeOrigin: true,
        secure: false,
      },
      '/glasses-frame': {
        target: `http://${backendHost}:${backendPort}`,
        changeOrigin: true,
        secure: false,
      },
      '/glasses-audio': {
        target: `http://${backendHost}:${backendPort}`,
        changeOrigin: true,
        secure: false,
      },
      '/dispense': {
        target: `http://${backendHost}:${backendPort}`,
        changeOrigin: true,
        secure: false,
      },
      '/relay': {
        target: `http://${backendHost}:${backendPort}`,
        changeOrigin: true,
        secure: false,
      },
      '/usb-camera': {
        target: `http://${backendHost}:${backendPort}`,
        changeOrigin: true,
        secure: false,
      },
    }
  },
  preview: {
    port: frontendPort,
    strictPort: true,
  },
})
