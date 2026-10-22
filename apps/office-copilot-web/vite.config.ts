import { defineConfig, loadEnv } from 'vite';
import { resolve } from 'node:path';
import { OFFICE_CSP } from './csp';

const cspHeaders = { 'Content-Security-Policy': OFFICE_CSP }; // 绘图页嵌入 diagrams.net

export default defineConfig(() => {
  const env = loadEnv('development', resolve(process.cwd(), '../..'), '');
  return {
    // Electron loadFile 需要相对资源路径
    base: './',
    server: {
      port: 5173,
      headers: cspHeaders,
      proxy: {
        '/backend': {
          target: `http://127.0.0.1:${env.FINANCE_MCP_PORT || '8000'}`,
          rewrite: (path: string) => path.replace(/^\/backend/, ''),
        },
        '/office': {
          target: `http://127.0.0.1:${env.OFFICE_API_PORT || '8100'}`,
          rewrite: (path: string) => path.replace(/^\/office/, ''),
          timeout: 0,
        },
      },
    },
    preview: { headers: cspHeaders },
  };
});
