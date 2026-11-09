function serviceUrl(value) {
  const url = new URL(value);
  const host = url.hostname;
  const parts = host.split('.').map(Number);
  const ipv4 = parts.length === 4 && parts.every(part => Number.isInteger(part) && part >= 0 && part <= 255);
  const local = host === 'localhost' || host === '[::1]' || (ipv4 && (
    parts[0] === 127 || parts[0] === 10 || (parts[0] === 192 && parts[1] === 168)
    || (parts[0] === 172 && parts[1] >= 16 && parts[1] <= 31)));
  if (url.username || url.password || url.search || url.hash
    || (url.protocol !== 'https:' && !(url.protocol === 'http:' && local))) {
    throw new Error('服务地址需使用 HTTPS；本地开发可使用 localhost 或内网 IP 的 HTTP 地址。');
  }
  return url.href.replace(/\/$/, '');
}
module.exports = { serviceUrl };
