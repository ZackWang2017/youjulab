import assert from 'node:assert/strict';
import { parseAskThread, serializeAskThread } from '../src/editors/pdf/ask-ai.ts';

const md = `## 本文搜索

第2页提到收入增长。

## 百度搜索

无

## Google搜索

无

## 模型搜索

市盈率是股价相对每股收益的倍数。

## 总结

综合来看增速尚可。`;

const thread = {
  selection: '欧洲收入',
  turns: [
    { role: 'user', speaker: 'Zack', text: '这段是什么意思？' },
    { role: 'assistant', speaker: 'deepseek', text: md },
    { role: 'user', speaker: 'Zack', text: '再展开一点' },
    { role: 'assistant', speaker: 'deepseek', text: '## 本文搜索\n\n补充页码3。\n\n## 总结\n\n如上。' },
  ],
};

const dumped = serializeAskThread(thread);
const parsed = parseAskThread(dumped);
assert.ok(parsed);
assert.equal(parsed.selection, '欧洲收入');
assert.equal(parsed.turns.length, 4);
assert.equal(parsed.turns[1].text, md);
assert.equal(parsed.turns[3].text.includes('补充页码3'), true);
assert.equal(parsed.turns[1].text.includes('## 百度搜索'), true);

const legacy = `【问AI】

选区：
欧洲收入

Zack：
这段是什么意思？

deepseek：
${md}`;

const legacyParsed = parseAskThread(legacy);
assert.ok(legacyParsed);
assert.equal(legacyParsed.turns.length, 2);
assert.equal(legacyParsed.turns[1].text.includes('## 总结'), true);
assert.equal(legacyParsed.turns[1].text.includes('综合来看增速尚可'), true);

console.log('ask-ai serialize/parse ok');
