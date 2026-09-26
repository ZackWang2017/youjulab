const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const { resolve, join } = require('node:path');
const { mkdtempSync, rmSync } = require('node:fs');
const { tmpdir } = require('node:os');

(async () => {
  const fixtureDir = mkdtempSync(join(tmpdir(), 'office-test-'));
  const fixture = join(fixtureDir, 'office-fixture.pdf');
  execFileSync(process.env.OFFICE_PYTHON || resolve(__dirname, '../../../.venv/bin/python'), ['-c',
    'import pymupdf,sys; d=pymupdf.open(); p=d.new_page(); p.insert_text((72,72),"Revenue increased in 2025."); p=d.new_page(); p.insert_text((72,72),"Operating cash flow was positive."); d.save(sys.argv[1])', fixture]);
  const browser = await chromium.launch({ channel: 'chrome', headless: true, timeout: 20000 });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 980 } });
    const errors = [];
    page.on('pageerror', error => { errors.push(error.message); console.log('PAGE ERROR', error.stack); });
    page.on('console', message => { if (message.type() === 'error') console.log('CONSOLE', message.text()); });
    page.on('requestfailed', request => console.log('REQUEST FAILED', request.url(), request.failure()));
    page.setDefaultTimeout(15000);
    await page.goto('http://127.0.0.1:5173', { waitUntil: 'domcontentloaded' });
    const email = `smoke-${Date.now()}@example.com`;
    await page.locator('#auth-email').fill(email);
    await page.locator('#auth-password').fill('secret1');
    await page.locator('#auth-register').click();
    await page.locator('#sheet[data-ready="true"]').waitFor();
    assert.equal(await page.locator('#mode').count(), 0);
    assert.match(await page.locator('#model-name').innerText(), /^模型：/);
    const modelReady = await page.evaluate(async () => {
      const token = localStorage.getItem('office-token');
      try {
        const res = await fetch('/office/assistant/status', { headers: { Authorization: 'Bearer ' + token } });
        const data = await res.json();
        return Boolean(res.ok && data.configured && data.has_api_key);
      } catch { return false; }
    });
    assert.equal(await page.locator('#app').evaluate(el => el.classList.contains('sheet-assistant-mode')), true);
    assert.equal(await page.locator('.task-modes').isHidden(), true);
    await page.locator('#intent').fill('清理选区首尾空白');
    await page.locator('#selection-toggle').click();
    assert.equal(await page.locator('#selection-content').isHidden(), true);
    assert.equal(await page.locator('#selection-toggle').innerText(), '显示');
    await page.locator('#selection-toggle').click();
    assert.equal(await page.locator('#selection-content').isHidden(), false);
    assert.equal(await page.locator('#selection-toggle').innerText(), '隐藏');
    const nameBox = page.locator('#sheet input.univer-appearance-none').first();
    await nameBox.fill('A2'); await nameBox.press('Enter');
    await page.keyboard.type(' 营业收入 ');
    await page.keyboard.press('Enter');
    await page.keyboard.type(' 净利润 ');
    await page.keyboard.press('Enter');
    await page.keyboard.type(' 经营现金流 ');
    await page.keyboard.press('Enter');
    await nameBox.fill('A2:A4'); await nameBox.press('Enter');
    await page.waitForFunction(() => (document.querySelector('#selection-content')?.textContent || '').includes('营业收入'));
    if (modelReady) {
      await page.locator('#run').click();
      await page.locator('#apply').waitFor({ timeout: 60000 });
      assert.match(await page.locator('#patch-preview').innerText(), /营业收入/);
      await page.locator('#apply').click();
      await page.waitForFunction(() => document.querySelector('#answer').textContent.includes('已接受修改'));
      assert.equal((await page.locator('#selection-content').innerText()).includes(' 营业收入 '), false);
      await page.keyboard.press('Meta+z');
      await page.waitForFunction(() => document.querySelector('#selection-content').textContent.includes(' 营业收入 '));
      console.log('PASS sheet selection, proposal, apply, undo');
    } else {
      console.log('SKIP sheet model apply (office-api 未配置模型)');
    }
    await page.screenshot({ path: '/tmp/office-desktop.png' });

    await page.locator('[data-tab="document"]').click();
    await page.locator('.tiptap').click();
    await page.keyboard.type('  公司营业收入从 102 亿元增长至 120 亿元，净利润从 20 亿元增长至 25 亿元。  ');
    const spaced = page.locator('.tiptap p').filter({ hasText: '营业收入' });
    await spaced.click({ clickCount: 3 });
    if (modelReady) {
      await page.locator('#run').click();
      await page.locator('#apply').waitFor({ timeout: 60000 });
      await page.locator('#apply').click();
      await page.waitForFunction(() => {
        const text = [...document.querySelectorAll('.tiptap p')].find(p => p.textContent.includes('营业收入'))?.textContent || '';
        return text.startsWith('公司营业收入') && !text.startsWith('  ');
      });
      await page.locator('.tiptap').click();
      await page.keyboard.press('Meta+z');
      await page.waitForFunction(() => {
        const text = [...document.querySelectorAll('.tiptap p')].find(p => p.textContent.includes('营业收入'))?.textContent || '';
        return text.startsWith('  公司营业收入');
      });
      console.log('PASS rich text selection, apply, undo');
    } else {
      console.log('SKIP document model apply (office-api 未配置模型)');
    }

    await page.locator('[data-caret="sheet"]').click();
    await page.locator('[data-new="sheet"]').click();
    await page.locator('.name-dialog input').fill('第二张表');
    await page.locator('.name-dialog button.primary').click();
    await page.waitForFunction(() => document.querySelector('#footer-surface')?.textContent.includes('第二张表'));
    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.locator('#sheet[data-ready="true"]').waitFor();
    await page.waitForFunction(() => document.querySelector('#footer-surface')?.textContent.includes('第二张表'));
    console.log('PASS last sheet restored after refresh');
    await page.locator('[data-caret="sheet"]').click();
    await page.locator('.menu [data-asset-id]').filter({ hasText: '未命名表格' }).click();
    await page.locator('#sheet[data-ready="true"]').waitFor();

    await page.locator('[data-tab="pdf"]').click();
    assert.equal(await page.locator('#task-generate').isDisabled(), true);
    assert.equal(await page.locator('#task-explain').isChecked(), true);
    await page.locator('#pdf input[type="file"]').setInputFiles(fixture);
    await page.locator('.textLayer span').first().waitFor();
    await page.locator('.textLayer span').first().evaluate(element => {
      const range = document.createRange(); range.selectNodeContents(element);
      const selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range);
      element.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
    });
    if (modelReady) {
      await page.locator('#run').click();
      await page.waitForFunction(() => document.querySelector('#answer').textContent.includes('第 1 页'), null, { timeout: 60000 });
      assert.equal(await page.locator('#apply').isVisible(), false);
    } else {
      console.log('SKIP PDF model run (office-api 未配置模型)');
    }
    await page.locator('[data-next]').click();
    await page.waitForFunction(() => document.querySelector('[data-page-input]').value === '2');
    await page.locator('.page[data-page-number="2"] .textLayer span').first().waitFor();
    assert.equal(await page.locator('#run').isDisabled(), false);
    await page.locator('#run').click();
    await page.waitForFunction(() => (document.querySelector('#error')?.textContent || '').includes('未选择任务内容'));
    assert.equal(await page.locator('.pdfViewer .page').count(), 2);
    assert.equal(await page.locator('[data-next]').isDisabled(), true);
    await page.locator('[data-page-input]').fill('1');
    await page.locator('[data-page-input]').press('Enter');
    await page.waitForFunction(() => document.querySelector('.pdf-scroll').scrollTop < 30);
    await page.locator('.pdf-scroll').evaluate(el => { el.scrollTop = el.scrollHeight; });
    await page.waitForFunction(() => document.querySelector('[data-page-input]').value === '2');
    await page.locator('.page[data-page-number="2"] .textLayer span').first().evaluate(element => {
      const range = document.createRange(); range.selectNodeContents(element);
      const selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range);
      element.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
    });
    if (modelReady) {
      await page.locator('#run').click();
      await page.waitForFunction(() => document.querySelector('#answer').textContent.includes('第 2 页'), null, { timeout: 60000 });
      assert.match(await page.locator('#answer').innerText(), /Operating cash flow/);
    }
    await page.locator('[data-page-input]').fill('99');
    await page.locator('[data-page-input]').press('Enter');
    assert.equal(await page.locator('[data-page-input]').inputValue(), '2');
    await page.locator('[data-prev]').click();
    await page.waitForFunction(() => document.querySelector('[data-page-input]').value === '1');
    assert.equal(await page.locator('[data-prev]').isDisabled(), true);
    const fitZoom = await page.locator('[data-zoom]').innerText();
    const fitWidth = await page.locator('.pdfViewer .page').first().evaluate(el => el.getBoundingClientRect().width);
    await page.locator('[data-zoom-in]').click();
    await page.waitForFunction(prev => document.querySelector('[data-zoom]').textContent !== prev, fitZoom);
    const zoomedWidth = await page.locator('.pdfViewer .page').first().evaluate(el => el.getBoundingClientRect().width);
    assert.equal(zoomedWidth > fitWidth + 4, true);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await page.locator('[data-fit-width]').click();
    await page.waitForFunction(prev => document.querySelector('[data-zoom]').textContent === prev, fitZoom);
    await page.locator('[data-zoom-out]').click();
    await page.waitForFunction(prev => document.querySelector('[data-zoom]').textContent !== prev, fitZoom);
    await page.locator('[data-page-input]').fill('2');
    await page.locator('[data-page-input]').press('Enter');
    await page.waitForFunction(() => document.querySelector('[data-page-input]').value === '2');
    console.log('PASS PDF continuous scroll, jump, bounds, navigation, zoom and page-specific citations');
    await page.screenshot({ path: '/tmp/office-pdf.png' });

    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: '/tmp/office-pdf-mobile.png', fullPage: true });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await page.locator('[data-tab="document"]').click();
    await page.screenshot({ path: '/tmp/office-mobile.png', fullPage: true });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    assert.deepEqual(errors, []);
    console.log('PASS mobile width and no page errors');
  } finally { await browser.close(); rmSync(fixtureDir, { recursive: true }); }
})().catch(error => { console.error(error); process.exitCode = 1; });
