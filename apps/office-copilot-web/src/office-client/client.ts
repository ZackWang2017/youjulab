const TOKEN_KEY = 'office-token';

function errorMessage(data: any, status: number) {
  if (typeof data?.error === 'string') return data.error;
  if (typeof data?.detail === 'string') return data.detail;
  if (Array.isArray(data?.detail) && data.detail[0]?.msg) {
    const first = data.detail[0];
    const field = Array.isArray(first.loc) ? first.loc[first.loc.length - 1] : '';
    if (field === 'password' && first.type === 'string_too_short') return '密码至少 6 位。';
    if (field === 'email' && (first.type === 'missing' || first.type === 'string_too_short')) return '请填写邮箱。';
    if (field === 'boxes') return '选区过大（高亮块过多），请缩小选区后重试。';
    if (field === 'content' && String(first.type || '').includes('too_long')) return '批注内容过长，请缩短后再保存。';
    if (first.type === 'missing') return '请填写完整信息。';
    return first.msg;
  }
  return `HTTP ${status}`;
}

export class AuthError extends Error {
  constructor(message = '请先登录。') { super(message); this.name = 'AuthError'; }
}

function token() {
  return localStorage.getItem(TOKEN_KEY);
}

export const office = {
  token,
  signedIn: () => Boolean(token()),
  setToken(value: string | null) {
    if (value) localStorage.setItem(TOKEN_KEY, value);
    else localStorage.removeItem(TOKEN_KEY);
  },
  async request(path: string, init: RequestInit = {}) {
    const headers = new Headers(init.headers);
    const current = token();
    if (current) headers.set('Authorization', `Bearer ${current}`);
    const response = await fetch(`/office${path}`, { ...init, headers });
    if (response.status === 401) {
      this.setToken(null);
      throw new AuthError();
    }
    return response;
  },
  async json<T = any>(path: string, init: RequestInit = {}): Promise<T> {
    const headers = new Headers(init.headers);
    if (init.body && !(init.body instanceof FormData) && !headers.has('Content-Type')) {
      headers.set('Content-Type', 'application/json');
    }
    let response: Response;
    try {
      response = await this.request(path, { ...init, headers });
    } catch (e) {
      if (e instanceof AuthError) throw e;
      throw new Error('无法连接工作区服务，请检查 office-api 是否启动。');
    }
    let data: any = {};
    const text = await response.text();
    if (text) {
      try { data = JSON.parse(text); } catch { throw new Error('工作区服务返回了无法解析的响应。'); }
    }
    if (!response.ok) throw new Error(errorMessage(data, response.status));
    return data as T;
  },
  async download(path: string, fallbackName: string) {
    let response: Response;
    try { response = await this.request(path); }
    catch (e) {
      if (e instanceof AuthError) throw e;
      throw new Error('无法连接工作区服务，请检查 office-api 是否启动。');
    }
    if (!response.ok) {
      let data: any = {};
      try { data = JSON.parse(await response.text()); } catch { /* ignore */ }
      throw new Error(errorMessage(data, response.status));
    }
    const blob = await response.blob();
    const header = response.headers.get('Content-Disposition') || '';
    const match = /filename="([^"]+)"/.exec(header);
    const name = match?.[1] || fallbackName;
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = name;
    document.body.append(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  },
  async file(id: string) {
    let response: Response;
    try { response = await this.request(`/assets/${id}/file`, { cache: 'no-store' }); }
    catch (e) {
      if (e instanceof AuthError) throw e;
      throw new Error('无法连接工作区服务，请检查 office-api 是否启动。');
    }
    if (response.status === 404) return null;
    if (!response.ok) {
      let data: any = {};
      try { data = await response.json(); } catch { /* ignore */ }
      throw new Error(data.error || `HTTP ${response.status}`);
    }
    return response.arrayBuffer();
  },
  upload(id: string, file: File) {
    const body = new FormData();
    body.append('file', file);
    return this.json(`/assets/${id}/file`, { method: 'PUT', body });
  },
  async stream(path: string, body: unknown, onEvent: (event: Record<string, any>) => void) {
    const headers = new Headers({ 'Content-Type': 'application/json' });
    let response: Response;
    try {
      response = await this.request(path, { method: 'POST', headers, body: JSON.stringify(body) });
    } catch (e) {
      if (e instanceof AuthError) throw e;
      throw new Error('无法连接工作区服务，请检查 office-api 是否启动。');
    }
    if (!response.ok) {
      let data: any = {};
      const text = await response.text();
      if (text) {
        try { data = JSON.parse(text); } catch { /* ignore */ }
      }
      throw new Error(errorMessage(data, response.status));
    }
    if (!response.body) throw new Error('模型没有返回内容。');
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const chunks = buffer.split('\n\n');
      buffer = chunks.pop() || '';
      for (const chunk of chunks) {
        const line = chunk.split('\n').find(item => item.startsWith('data: '));
        if (!line) continue;
        const event = JSON.parse(line.slice(6));
        if (event.type === 'error') throw new Error(event.error || '模型请求失败。');
        onEvent(event);
      }
    }
  },
};
