import semver from 'semver';
import { createHash, timingSafeEqual } from 'node:crypto';
import fs from 'node:fs/promises';
import path from 'node:path';
import serviceUrls from './service-url.cjs';

export function updateBase(value) {
  if (!value) return '';
  return serviceUrls.serviceUrl(value);
}

export function validateRelease(release, base, platform, arch) {
  if (release === null) return null;
  if (!release || !semver.valid(release.version) || !/^\d+\.\d+\.\d+$/.test(release.version)
    || release.platform !== platform || release.arch !== arch || typeof release.mandatory !== 'boolean'
    || !Number.isSafeInteger(release.size) || release.size <= 0 || release.size > 4 * 1024 ** 3
    || typeof release.sha512 !== 'string' || !/^[A-Za-z0-9+/]{86}==$/.test(release.sha512)
    || typeof release.notes !== 'string' || release.notes.length > 20000) {
    throw new Error('更新元数据无效。');
  }
  for (const key of ['download_url', 'update_feed_url']) {
    const url = new URL(release[key]);
    serviceUrls.serviceUrl(url.href);
    if (url.origin !== new URL(base).origin) {
      throw new Error('安装包必须来自已配置的同源更新服务。');
    }
  }
  return release;
}

export function newer(release, version) {
  return Boolean(release && semver.valid(version) && semver.gt(release.version, version));
}

export async function checkRelease({ base, platform, arch, version, cacheFile, fetcher = fetch }) {
  let cached = null;
  try {
    const saved = JSON.parse(await fs.readFile(cacheFile, 'utf8'));
    if (saved.base === base) cached = validateRelease(saved.release, base, platform, arch);
  } catch { /* Missing or invalid caches do not create forced updates. */ }
  try {
    const response = await fetcher(`${base}/api/v1/releases/latest?platform=${platform}&arch=${arch}`, {
      signal: AbortSignal.timeout(8000), redirect: 'error',
    });
    if (!response.ok) throw new Error('版本服务暂时不可用。');
    const text = await response.text();
    if (text.length > 65536) throw new Error('版本响应过大。');
    const latest = validateRelease(JSON.parse(text).release, base, platform, arch);
    try {
      const temporary = cacheFile + '.tmp';
      await fs.mkdir(path.dirname(cacheFile), { recursive: true });
      await fs.writeFile(temporary, JSON.stringify({ base, release: latest }), { mode: 0o600 });
      await fs.rename(temporary, cacheFile);
    } catch { console.warn('[youju] cannot persist update policy; current online policy still applies'); }
    return { release: newer(latest, version) ? latest : null, offline: false };
  } catch (error) {
    return { release: cached?.mandatory && newer(cached, version) ? cached : null,
      offline: true, error: String(error.message || error) };
  }
}

export async function verifyFile(filename, release) {
  const hash = createHash('sha512');
  const { createReadStream } = await import('node:fs');
  let size = 0;
  for await (const chunk of createReadStream(filename)) { hash.update(chunk); size += chunk.length; }
  const actual = hash.digest(), expected = Buffer.from(release.sha512, 'base64');
  if (size !== release.size || actual.length !== expected.length || !timingSafeEqual(actual, expected)) {
    throw new Error('安装包校验失败，请重新下载。');
  }
}

export async function installVerifiedUpdate(filename, release, { stopServices, install }) {
  await verifyFile(filename, release);
  await stopServices();
  install();
}

export async function downloadMac(release, directory, { signal, progress = () => {}, fetcher = fetch } = {}) {
  await fs.mkdir(directory, { recursive: true, mode: 0o700 });
  const filename = path.join(directory, `youjulab-${release.version}-${release.arch}.dmg`);
  const partial = filename + '.part';
  try {
    const response = await fetcher(release.download_url, { signal, redirect: 'error' });
    if (!response.ok || !response.body) throw new Error('下载安装包失败，请重试。');
    const hash = createHash('sha512');
    let received = 0;
    const output = await fs.open(partial, 'w', 0o600);
    try {
      for await (const chunk of response.body) {
        if (signal?.aborted) throw new Error('已取消下载。');
        received += chunk.length;
        if (received > release.size) throw new Error('安装包大小异常。');
        hash.update(chunk);
        await output.writeFile(chunk);
        progress(received / release.size * 100);
      }
    } finally { await output.close(); }
    if (received !== release.size || hash.digest('base64') !== release.sha512) throw new Error('安装包校验失败，请重试。');
    await fs.rename(partial, filename);
    return filename;
  } finally { await fs.rm(partial, { force: true }); }
}
