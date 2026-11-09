import { createHash } from 'node:crypto';

export function backupUserKey(identity) {
  return createHash('sha256').update(`${identity.server}\n${identity.id}\n${identity.email}`).digest('hex').slice(0, 32);
}

export class BackupSession {
  constructor(base, request = fetch) { this.base = base; this.request = request; this.token = ''; }
  async verify(token) {
    if (!token) throw new Error('请先登录后再操作备份。');
    const server = this.base();
    const response = await this.request(`${server}/api/account/me`, {
      headers: { Authorization: `Bearer ${token}` }, signal: AbortSignal.timeout(15000),
    });
    if (!response.ok) throw new Error('登录已失效，请重新登录后操作备份。');
    const user = await response.json();
    if (!user.id || !user.email) throw new Error('账号服务未返回有效身份。');
    return { id: String(user.id), email: String(user.email).trim().toLowerCase(), server };
  }
  set(token) {
    this.token = String(token || '');
    this.server = this.base();
    this.identity = null;
  }
  async current() {
    if (this.server !== this.base()) throw new Error('服务器已切换，请重新登录。');
    const token = this.token;
    const identity = await this.verify(token);
    if (this.token !== token || this.base() !== identity.server) throw new Error('登录账号已切换，请重试。');
    this.identity = identity;
    return identity;
  }
}
