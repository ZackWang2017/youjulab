import { defineConfig, loadEnv } from 'vite';
import { resolve } from 'node:path';
import { OFFICE_CSP } from './csp';

const cspHeaders = { 'Content-Security-Policy': OFFICE_CSP };

export default defineConfig(() => {
  const env = loadEnv('development', resolve(process.cwd(), '../..'), '');
  return {
    server: {
      port: 5173,
      headers: cspHeaders,
      proxy: {
        '/backend': {
          target: `http://127.0.0.1:${env.FINANCE_MCP_PORT || '8000'}`,
          rewrite: (path: string) => path.replace(/^\/backend/, ''),
          headers: { Authorization: `Bearer ${env.MCP_ACCESS_TOKEN || env.FINANCE_MCP_TOKEN || 'invpdevstatic32tokenpleasechange'}` },
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
