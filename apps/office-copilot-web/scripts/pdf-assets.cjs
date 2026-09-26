const { cpSync, mkdirSync } = require('node:fs');
for (const name of ['cmaps', 'standard_fonts', 'wasm']) {
  mkdirSync(`public/pdfjs/${name}`, { recursive: true });
  cpSync(`node_modules/pdfjs-dist/${name}`, `public/pdfjs/${name}`, { recursive: true });
}
mkdirSync('public/pdfjs/images', { recursive: true });
cpSync('node_modules/pdfjs-dist/web/images', 'public/pdfjs/images', { recursive: true });
