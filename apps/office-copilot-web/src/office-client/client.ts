import { getLocalPdf, putLocalPdf } from '../local-store';

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

export type PdfFileProgress = {
  loaded: number;
  total: number | null;
  source: 'cache' | 'network';
};

export type PdfFileOptions = {
  revision?: number;
  /** 列表里的 size，Content-Length 缺失时用作进度总量。 */
  expectedSize?: number | null;
  onProgress?: (progress: PdfFileProgress) => void;
  /** 本地有副本时立即回调（可能版本偏旧），便于先打开再后台校验。 */
  onLocal?: (bytes: ArrayBuffer, revision: number) => void | Promise<void>;
};

function token() {
  return localStorage.getItem(TOKEN_KEY);
}

function notifyProgress(waiters: Iterable<PdfFileOptions>, progress: PdfFileProgress) {
  for (const waiter of waiters) waiter.onProgress?.(progress);
}

async function notifyLocal(waiters: Iterable<PdfFileOptions>, bytes: ArrayBuffer, revision: number) {
  for (const waiter of waiters) {
    try { await waiter.onLocal?.(bytes, revision); } catch { /* 打开失败由调用方处理 */ }
  }
}

async function readBody(
  response: Response,
  expectedSize: number | null | undefined,
  onChunk: (loaded: number, total: number | null) => void,
): Promise<ArrayBuffer> {
  const lengthHeader = Number(response.headers.get('Content-Length'));
  const total = Number.isFinite(lengthHeader) && lengthHeader > 0
    ? lengthHeader
    : (expectedSize && expectedSize > 0 ? expectedSize : null);
  if (!response.body) {
    const bytes = await response.arrayBuffer();
    onChunk(bytes.byteLength, total ?? bytes.byteLength);
    return bytes;
  }
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let loaded = 0;
  onChunk(0, total);
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    if (value) {
      chunks.push(value);
      loaded += value.byteLength;
      onChunk(loaded, total);
    }
  }
  const bytes = new Uint8Array(loaded);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  if (total === null) onChunk(loaded, loaded);
  return bytes.buffer;
}

type PdfInflight = {
  promise: Promise<ArrayBuffer | null>;
  waiters: Set<PdfFileOptions>;
};

const pdfInflight = new Map<string, PdfInflight>();

async function fetchPdfFile(id: string, waiters: Set<PdfFileOptions>): Promise<ArrayBuffer | null> {
  const preferredRevision = [...waiters].map(item => item.revision).find(item => item !== undefined);
  const expectedSize = [...waiters].map(item => item.expectedSize).find(item => item != null && item > 0);

  let local: Awaited<ReturnType<typeof getLocalPdf>> = null;
  try { local = await getLocalPdf(id, expectedSize); } catch { local = null; }

  if (local) {
    notifyProgress(waiters, {
      loaded: local.bytes.byteLength,
      total: local.bytes.byteLength,
      source: 'cache',
    });
    // 不 await：避免 onLocal 里打开 PDF 卡住整次下载；调用方仍会用返回值再打开一次。
    void notifyLocal(waiters, local.bytes, local.revision);
    if (preferredRevision === undefined || local.revision === preferredRevision) {
      return local.bytes.slice(0);
    }
  }

  let response: Response;
  try {
    response = await office.request(`/assets/${id}/file`, { cache: 'no-store' });
  } catch (e) {
    if (e instanceof AuthError) throw e;
    if (local) return local.bytes;
    throw new Error('无法连接工作区服务，请检查 office-backend 是否启动。');
  }
  if (response.status === 404) return null;
  if (!response.ok) {
    let data: any = {};
    try { data = await response.json(); } catch { /* ignore */ }
    throw new Error(data.error || `HTTP ${response.status}`);
  }

  const bytes = await readBody(response, expectedSize, (loaded, total) => {
    notifyProgress(waiters, { loaded, total, source: 'network' });
  });
  const headerRev = Number(response.headers.get('X-Asset-Revision'));
  const storedRev = Number.isFinite(headerRev) ? headerRev : preferredRevision;
  if (storedRev !== undefined) {
    try { await putLocalPdf(id, storedRev, bytes.slice(0)); } catch { /* 配额不足时仍返回本次内容 */ }
  }
  return bytes;
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
      throw new Error('无法连接工作区服务，请检查 office-backend 是否启动。');
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
      throw new Error('无法连接工作区服务，请检查 office-backend 是否启动。');
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
  async file(id: string, revisionOrOpts?: number | PdfFileOptions, maybeOpts?: PdfFileOptions) {
    const opts: PdfFileOptions = typeof revisionOrOpts === 'number'
      ? { ...(maybeOpts || {}), revision: revisionOrOpts }
      : (revisionOrOpts || {});
    let entry = pdfInflight.get(id);
    if (!entry) {
      const waiters = new Set<PdfFileOptions>([opts]);
      const promise = fetchPdfFile(id, waiters).finally(() => {
        if (pdfInflight.get(id)?.promise === promise) pdfInflight.delete(id);
      });
      entry = { promise, waiters };
      pdfInflight.set(id, entry);
      return promise;
    }
    entry.waiters.add(opts);
    try {
      const local = await getLocalPdf(id, opts.expectedSize);
      if (local) {
        opts.onProgress?.({
          loaded: local.bytes.byteLength,
          total: local.bytes.byteLength,
          source: 'cache',
        });
        void opts.onLocal?.(local.bytes.slice(0), local.revision);
      }
    } catch { /* ignore */ }
    try {
      return await entry.promise;
    } finally {
      entry.waiters.delete(opts);
    }
  },
  /** 后台预热 PDF 本地缓存，不阻塞 UI。 */
  prefetchFile(id: string, revision?: number, expectedSize?: number | null) {
    void this.file(id, { revision, expectedSize }).catch(() => {});
  },
  async upload<T extends { id?: string; revision?: number } = { id: string; revision: number }>(id: string, file: File) {
    const bytes = await file.arrayBuffer();
    const body = new FormData();
    body.append('file', new Blob([bytes], { type: file.type || 'application/pdf' }), file.name);
    const saved = await this.json<T>(`/assets/${id}/file`, { method: 'PUT', body });
    const savedId = saved.id || id;
    if (typeof saved.revision === 'number') {
      try { await putLocalPdf(savedId, saved.revision, bytes); } catch { /* ignore */ }
    }
    return saved;
  },
  async stream(path: string, body: unknown, onEvent: (event: Record<string, any>) => void) {
    const headers = new Headers({ 'Content-Type': 'application/json' });
    let response: Response;
    try {
      response = await this.request(path, { method: 'POST', headers, body: JSON.stringify(body) });
    } catch (e) {
      if (e instanceof AuthError) throw e;
      throw new Error('无法连接工作区服务，请检查 office-backend 是否启动。');
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
