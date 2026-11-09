export const DEFAULT_REMOTE = 'http://192.168.0.5:8000';

export function remoteAddress(value) {
  const raw = String(value || DEFAULT_REMOTE).trim();
  const url = new URL(raw.includes('://') ? raw : `http://${raw}`);
  if (!raw.includes('://') && !url.port) url.port = '8000';
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) {
    throw new Error('请填写有效的 MCP HTTP/HTTPS 服务地址。');
  }
  return url.href.replace(/\/$/, '');
}

export function savedRemoteAddress(config) {
  // Older versions always persisted a loopback address for the bundled MCP.
  if (!config.remoteServiceVersion && /^https?:\/\/(127\.0\.0\.1|localhost)(:8000)?\/?$/.test(config.remoteBaseUrl || '')) {
    return DEFAULT_REMOTE;
  }
  return remoteAddress(config.remoteBaseUrl);
}
