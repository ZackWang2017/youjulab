const config = require('./package.json').build;
const { serviceUrl } = require('./service-url.cjs');
const raw = String(process.env.YOUJU_RELEASE_BASE_URL || process.env.YOUJU_ADMIN_BASE_URL || '').trim();
let base = '';
if (raw) {
  base = serviceUrl(raw);
  if (base.startsWith('http:')) console.warn('[youju] 当前使用 HTTP 内网测试地址，发布正式版本前请改为 HTTPS。');
} else {
  console.warn('[youju] YOUJU_RELEASE_BASE_URL 未设置，本次安装包禁用版本检查。');
}
const adminRaw=String(process.env.YOUJU_ADMIN_BASE_URL || base).trim();
let adminBase='';
if(adminRaw){
  adminBase=serviceUrl(adminRaw);
}
module.exports = { ...config, extraMetadata: { releaseBaseUrl: base, adminBaseUrl: adminBase }, beforePack: async context => {
  if (context.electronPlatformName === 'win32') {
    const fs = require('node:fs');
    const path = require('node:path');
    const file = path.join(__dirname, 'sidecar/dist/youju-office-backend/youju-office-backend.exe');
    if (!fs.existsSync(file) || fs.readFileSync(file).subarray(0, 2).toString() !== 'MZ') {
      throw new Error('缺少 Windows Python sidecar，请先在 Windows 上执行 npm run build:windows。');
    }
  }
} };
