/** 远程 mcp。调试默认本机 8000；部署后在登录窗或设置里改成服务器地址。 */

export const DEFAULT_REMOTE_BASE = 'http://127.0.0.1:8000';
const URL_KEY = 'youju-remote-base';
const TOKEN_KEY = 'youju-remote-token';
const EMAIL_KEY = 'youju-remote-email';

export class RemoteAuthError extends Error {
  constructor(message = '请先登录。') { super(message); this.name = 'RemoteAuthError'; }
}

export class RemoteDownError extends Error {
  constructor(message = '服务器不可用。') { super(message); this.name = 'RemoteDownError'; }
}

export function remoteBase() {
  const saved = (localStorage.getItem(URL_KEY) || '').trim().replace(/\/$/, '');
  return saved || DEFAULT_REMOTE_BASE;
}

/** 浏览器开发态对本机默认地址走 Vite /backend 代理，避免跨域 OPTIONS 预检。 */
export function remoteFetchBase() {
  const base = remoteBase();
  // 桌面壳或本机 office-backend 托管页：直连远程，不要走 Vite /backend。
  if (typeof window !== 'undefined' && window.youju?.isDesktop) return base;
  if (typeof window !== 'undefined' && window.youju?.officeApiBase) return base;
  if (base === DEFAULT_REMOTE_BASE || base === 'http://localhost:8000') return '/backend';
  return base;
}

export function setRemoteBase(url: string) {
  const next = (url || '').trim().replace(/\/$/, '');
  if (!next || next === DEFAULT_REMOTE_BASE) localStorage.removeItem(URL_KEY);
  else localStorage.setItem(URL_KEY, next);
}

export function remoteToken() {
  return localStorage.getItem(TOKEN_KEY);
}

export function setRemoteToken(token: string | null) {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(EMAIL_KEY);
    try { sessionStorage.removeItem('youju-bound-remote'); } catch { /* ignore */ }
  }
}

export function remoteEmail() {
  const saved = (localStorage.getItem(EMAIL_KEY) || '').trim();
  if (saved) return saved;
  const token = remoteToken();
  if (!token) return '';
  try {
    const part = token.split('.')[1] || '';
    const padded = part.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - (part.length % 4)) % 4);
    const payload = JSON.parse(atob(padded));
    const email = typeof payload?.email === 'string' ? payload.email.trim() : '';
    if (email) localStorage.setItem(EMAIL_KEY, email);
    return email;
  } catch {
    return '';
  }
}

export function setRemoteEmail(email: string | null) {
  const next = (email || '').trim();
  if (next) localStorage.setItem(EMAIL_KEY, next);
  else localStorage.removeItem(EMAIL_KEY);
}

function errorMessage(data: any, status: number) {
  if (typeof data?.error === 'string') return data.error;
  if (typeof data?.detail === 'string') return data.detail;
  return `HTTP ${status}`;
}

export async function remoteFetch(path: string, init: RequestInit = {}) {
  const headers = new Headers(init.headers);
  const token = remoteToken();
  if (token && !headers.has('Authorization')) headers.set('Authorization', `Bearer ${token}`);
  if (init.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  let response: Response;
  try {
    response = await fetch(`${remoteFetchBase()}${path}`, { ...init, headers });
  } catch {
    throw new RemoteDownError();
  }
  // 登录/注册的 401 是账号业务错误，交给 remoteJson 读响应体。
  if (response.status === 401 && path !== '/api/account/login' && path !== '/api/account/register') {
    throw new RemoteAuthError();
  }
  return response;
}

export async function remoteJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await remoteFetch(path, init);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(errorMessage(data, response.status));
  return data as T;
}

export async function remoteHealthy() {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 2500);
  try {
    const response = await fetch(`${remoteFetchBase()}/api/account/health`, { signal: ctrl.signal });
    return response.ok;
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
  }
}

export async function loadCaptcha() {
  return remoteJson<{ captcha_id: string; image_base64: string; image_type: string }>('/api/account/captcha');
}

export async function remoteLogin(body: Record<string, string>) {
  const data = await remoteJson<{ token: string; user: { id: string; email: string } }>('/api/account/login', {
    method: 'POST', body: JSON.stringify(body),
  });
  setRemoteToken(data.token);
  setRemoteEmail(data.user?.email || null);
  return data;
}

export async function remoteRegister(body: Record<string, string>) {
  const data = await remoteJson<{ token: string; user: { id: string; email: string } }>('/api/account/register', {
    method: 'POST', body: JSON.stringify(body),
  });
  setRemoteToken(data.token);
  setRemoteEmail(data.user?.email || null);
  return data;
}

export async function changeRemotePassword(currentPassword: string, newPassword: string) {
  if (!remoteToken()) throw new RemoteAuthError('请先登录远程账号后再修改密码。');
  await remoteJson('/api/account/password', {
    method: 'PUT',
    body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
  });
}

declare global {
  interface Window {
    youju?: {
      isDesktop?: boolean;
      platform?: string;
      appVersion?: string;
      /** Electron 注入的本机 office-backend 根地址，例如 http://127.0.0.1:54321 */
      officeApiBase?: string;
      getConfig?: () => Promise<{
        remoteBaseUrl: string;
        pdfDir: string;
        dataMode?: 'repo' | 'userData';
        dataRoot?: string;
        sqlitePath?: string;
        filesPath?: string;
        repoDataAvailable?: boolean;
      }>;
      setConfig?: (config: {
        remoteBaseUrl?: string;
        pdfDir?: string;
        dataMode?: 'repo' | 'userData';
      }) => Promise<Record<string, unknown>>;
    };
  }
}
