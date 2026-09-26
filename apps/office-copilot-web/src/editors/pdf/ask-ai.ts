/** PDF「问 AI」对话内容的序列化 / 解析（写入批注正文）。 */

export type AskTurn = { role: 'user' | 'assistant'; speaker: string; text: string; thinking?: string };
export type AskThread = { selection: string; turns: AskTurn[] };

const HEADER = '【问AI】';
const HEADER_V2 = '【问AI】v2';

/** 回合边界标记：正文可含任意空行 / Markdown，不会被误切。 */
const TURN_OPEN = (role: 'user' | 'assistant', speaker: string) =>
  `⟦ask:${role}:${encodeURIComponent(speaker || (role === 'user' ? '用户' : '模型'))}⟧`;
const TURN_CLOSE = '⟦/ask⟧';
const TURN_RE = /⟦ask:(user|assistant):([^⟧]*)⟧\n?([\s\S]*?)\n?⟦\/ask⟧/g;
const THINK_RE = /^⟦think⟧\n?([\s\S]*?)\n?⟦\/think⟧\n?/;

function splitEmbeddedThinking(text: string) {
  const think = /^##\s*思考过程\s*$/m.exec(text);
  const answer = /^##\s*回答\s*$/m.exec(text);
  if (think && answer && think.index < answer.index) {
    return {
      thinking: text.slice(think.index + think[0].length, answer.index).trim(),
      text: text.slice(answer.index + answer[0].length).replace(/^\n+/, '').trim(),
    };
  }
  return { text: text.trim(), thinking: '' };
}

export function isAiAskContent(content: string) {
  const t = (content || '').trimStart();
  return t.startsWith(HEADER_V2) || t.startsWith(HEADER);
}

export function serializeAskThread(thread: AskThread) {
  const selection = (thread.selection || '').trim();
  const parts = [`${HEADER_V2}\n\n选区：\n${selection}`];
  for (const turn of thread.turns) {
    const speaker = (turn.speaker || (turn.role === 'user' ? '用户' : '模型')).trim()
      || (turn.role === 'user' ? '用户' : '模型');
    const text = (turn.text || '').trim();
    const thinking = (turn.thinking || '').trim();
    if (!text && !thinking) continue;
    const body = thinking ? `⟦think⟧\n${thinking}\n⟦/think⟧\n${text}` : text;
    parts.push(`${TURN_OPEN(turn.role, speaker)}\n${body}\n${TURN_CLOSE}`);
  }
  return parts.join('\n\n').trim();
}

function parseV2(body: string): AskThread {
  let selection = '';
  const sel = /^选区：\n?([\s\S]*?)(?=\n\n⟦ask:|\s*$)/.exec(body);
  if (sel) selection = sel[1].trim();
  const turns: AskTurn[] = [];
  TURN_RE.lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = TURN_RE.exec(body))) {
    let speaker = '模型';
    try {
      speaker = decodeURIComponent(m[2] || '') || (m[1] === 'user' ? '用户' : '模型');
    } catch {
      speaker = m[2] || (m[1] === 'user' ? '用户' : '模型');
    }
    const rawText = (m[3] || '').trim();
    const thought = THINK_RE.exec(rawText);
    const unpacked = splitEmbeddedThinking(thought ? rawText.slice(thought[0].length) : rawText);
    const thinking = (thought?.[1] || unpacked.thinking || '').trim();
    const text = unpacked.text.trim();
    if (!text && !thinking) continue;
    turns.push({
      role: m[1] === 'user' ? 'user' : 'assistant',
      speaker,
      text,
      thinking: thinking || undefined,
    });
  }
  return { selection, turns };
}

/**
 * 旧版：用「\\n\\n说话人：\\n」作回合起点；正文允许内部空行。
 */
function parseLegacy(body: string): AskThread {
  let selection = '';
  let rest = body.trim();

  if (rest.startsWith('选区：')) {
    rest = rest.replace(/^选区：\n?/, '');
    const cut = /\n\n([^：\n\r]{1,64})：\n/.exec(rest);
    if (cut && cut.index != null) {
      selection = rest.slice(0, cut.index).trim();
      rest = rest.slice(cut.index).replace(/^\n+/, '');
    } else {
      selection = rest.trim();
      rest = '';
    }
  }

  const turns: AskTurn[] = [];
  if (!rest.trim()) return { selection, turns };

  const padded = rest.startsWith('\n\n') ? rest : `\n\n${rest}`;
  const re = /\n\n([^：\n\r]{1,64})：\n/g;
  const starts: { index: number; speaker: string }[] = [];
  let match: RegExpExecArray | null;
  while ((match = re.exec(padded))) {
    starts.push({ index: match.index, speaker: match[1] });
  }
  if (!starts.length) {
    const one = /^([^：\n]{1,64})：\s*([\s\S]+)$/.exec(rest.trim());
    if (one) {
      const unpacked = splitEmbeddedThinking(one[2]);
      turns.push({ role: 'user', speaker: one[1].trim(), text: unpacked.text, thinking: unpacked.thinking || undefined });
    }
    return { selection, turns };
  }
  for (let i = 0; i < starts.length; i++) {
    const cur = starts[i];
    const next = starts[i + 1];
    const headerLen = `\n\n${cur.speaker}：\n`.length;
    const from = cur.index + headerLen;
    const to = next ? next.index : padded.length;
    const unpacked = splitEmbeddedThinking(padded.slice(from, to));
    if (!unpacked.text && !unpacked.thinking) continue;
    turns.push({
      role: turns.length % 2 === 0 ? 'user' : 'assistant',
      speaker: cur.speaker.trim(),
      text: unpacked.text,
      thinking: unpacked.thinking || undefined,
    });
  }
  return { selection, turns };
}

export function parseAskThread(content: string): AskThread | null {
  const raw = (content || '').replace(/^\uFEFF/, '').trim();
  if (!raw.startsWith(HEADER)) return null;
  const v2 = raw.startsWith(HEADER_V2);
  const body = raw.slice(v2 ? HEADER_V2.length : HEADER.length).trim();
  if (!body) return { selection: '', turns: [] };
  if (v2 || body.includes('⟦ask:')) return parseV2(body);
  return parseLegacy(body);
}

export function historyForApi(thread: AskThread) {
  return thread.turns.map(turn => ({ role: turn.role, content: turn.text }));
}
