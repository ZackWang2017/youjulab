/** Univer 公式引擎等依赖 new Function()，需允许 unsafe-eval。 */
export const OFFICE_CSP = [
  "default-src 'self'",
  "script-src 'self' 'unsafe-eval' 'wasm-unsafe-eval' blob:",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self' data:",
  "connect-src 'self' ws: wss: http://127.0.0.1:* http://localhost:* https:",
  "worker-src 'self' blob:",
  "media-src 'self' blob:",
  "frame-src https://embed.diagrams.net https://app.diagrams.net",
  "object-src 'none'",
  "base-uri 'self'",
].join('; ');
