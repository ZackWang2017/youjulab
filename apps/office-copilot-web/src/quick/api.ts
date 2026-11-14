/**
 * 小窗专用的 office-backend 请求：与主窗口共用 localStorage 里的本机令牌，
 * 但 401 时不清令牌（清了会把主窗口一起登出），409 抛 ConflictError 供重试。
 */
import { ConflictError, type QuickAsset } from './write';

const TOKEN_KEY = 'office-token';

export class NotSignedInError extends Error {
  constructor() { super('请先在主窗口登录。'); this.name = 'NotSignedInError'; }
}

function apiBase() {
  const desktop = window.youju?.officeApiBase;
  if (desktop) return desktop.replace(/\/$/, '');
  // Vite 开发服务器走 /office 代理；由 office-backend 同源托管时直接用根路径
  return location.port === '5173' ? '/office' : '';
}

export function signedIn() {
  return Boolean(localStorage.getItem(TOKEN_KEY));
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = localStorage.getItem(TOKEN_KEY);
  if (!token) throw new NotSignedInError();
  const headers = new Headers(init.headers);
  headers.set('Authorization', `Bearer ${token}`);
  if (init.body) headers.set('Content-Type', 'application/json');
  let response: Response;
  try {
    response = await fetch(`${apiBase()}${path}`, { ...init, headers, cache: 'no-store' });
  } catch {
    throw new Error('无法连接本机工作区服务，请确认有据正在运行。');
  }
  const text = await response.text();
  let data: any = {};
  if (text) {
    try { data = JSON.parse(text); } catch { data = {}; }
  }
  if (response.status === 401) throw new NotSignedInError();
  if (response.status === 409) throw new ConflictError(typeof data.error === 'string' ? data.error : undefined);
  if (!response.ok) throw new Error(typeof data.error === 'string' ? data.error : `HTTP ${response.status}`);
  return data as T;
}

export type UserTools = { notebook: QuickAsset | null; calendar: QuickAsset | null };

export const quickApi = {
  tools: () => request<UserTools>('/user-tools'),
  asset: (id: string) => request<QuickAsset>(`/assets/${encodeURIComponent(id)}`),
  save: (asset: QuickAsset, content: unknown) => request<QuickAsset>(`/assets/${encodeURIComponent(asset.id)}`, {
    method: 'PUT',
    body: JSON.stringify({ content, revision: asset.revision }),
  }),
};
