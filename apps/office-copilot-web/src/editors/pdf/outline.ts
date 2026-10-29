export type PdfOutlineItem = {
  id: string;
  page: number;
  order: number;
  title: string;
  kind: 'numbered' | 'bold';
  level: number;
};

type RichNode = {
  type?: string;
  text?: string;
  attrs?: Record<string, unknown>;
  marks?: Array<{ type?: string; attrs?: Record<string, unknown> }>;
  content?: RichNode[];
};

export type OutlineTemplateMatch = {
  doc: unknown;
  matched: number;
  unmatched: number;
};

function cloneNode(node: RichNode): RichNode {
  return {
    ...node,
    attrs: node.attrs ? { ...node.attrs } : undefined,
    marks: node.marks?.map(mark => ({ ...mark, attrs: mark.attrs ? { ...mark.attrs } : undefined })),
    content: node.content?.map(cloneNode),
  };
}

/** Template documents must never retain a page link from the source PDF. */
export function stripOutlineReferences(doc: unknown): unknown {
  const strip = (node: unknown): unknown => {
    if (!node || typeof node !== 'object' || Array.isArray(node)) return node;
    const value = cloneNode(node as RichNode);
    if (value.marks) value.marks = value.marks.filter(mark => mark.type !== 'pdfOutlineReference');
    if (value.content) value.content = value.content.map(child => strip(child) as RichNode);
    return value;
  };
  return strip(doc);
}

function textOf(node: RichNode): string {
  if (typeof node.text === 'string') return node.text;
  return (node.content || []).map(textOf).join('');
}

function normaliseTitle(value: string): string {
  return value
    .normalize('NFKC')
    .toLocaleLowerCase()
    .replace(/^\s*(?:第[一二三四五六七八九十百千\d]+[章节部分篇]|[（(]?[一二三四五六七八九十百千\d]+[）).、．.]|\d+(?:[.．]\d+){0,5}[、.．)）:]?)\s*/, '')
    .replace(/[\s\p{P}\p{S}]/gu, '');
}

function diceScore(left: string, right: string): number {
  if (!left || !right) return 0;
  if (left === right) return 1;
  if (left.includes(right) || right.includes(left)) return Math.min(left.length, right.length) / Math.max(left.length, right.length) * 0.16 + 0.84;
  const pairs = (value: string) => {
    const result = new Map<string, number>();
    for (let index = 0; index < value.length - 1; index += 1) {
      const pair = value.slice(index, index + 2);
      result.set(pair, (result.get(pair) || 0) + 1);
    }
    return result;
  };
  const leftPairs = pairs(left);
  const rightPairs = pairs(right);
  let common = 0;
  for (const [pair, count] of leftPairs) common += Math.min(count, rightPairs.get(pair) || 0);
  return (2 * common) / Math.max(1, left.length + right.length - 2);
}

function candidateFor(title: string, candidates: PdfOutlineItem[], used: Set<string>) {
  const normalised = normaliseTitle(title);
  if (!normalised || normalised === '文档大纲' || normalised === '大纲') return null;
  let best: PdfOutlineItem | null = null;
  let score = 0;
  for (const candidate of candidates) {
    if (used.has(candidate.id)) continue;
    const next = diceScore(normalised, normaliseTitle(candidate.title));
    if (next > score) {
      best = candidate;
      score = next;
    }
  }
  return best && score >= 0.62 ? best : null;
}

/** Keep the template hierarchy, but use the current PDF's verified title and page. */
export function matchTemplateOutline(template: unknown, candidates: PdfOutlineItem[]): OutlineTemplateMatch {
  const source = stripOutlineReferences(template) as RichNode;
  const used = new Set<string>();
  let matched = 0;
  let unmatched = 0;
  const visit = (node: RichNode): RichNode => {
    const copy = cloneNode(node);
    const isTitle = (copy.type === 'heading' || copy.type === 'paragraph') && Boolean(textOf(copy).trim());
    if (isTitle) {
      const title = textOf(copy).trim();
      const candidate = candidateFor(title, candidates, used);
      if (candidate) {
        used.add(candidate.id);
        matched += 1;
        copy.content = [{ type: 'text', text: candidate.title, marks: [{ type: 'pdfOutlineReference', attrs: { page: candidate.page } }] }];
      } else if (normaliseTitle(title) !== '文档大纲' && normaliseTitle(title) !== '大纲') {
        unmatched += 1;
        copy.content = [{ type: 'text', text: `${title}（未定位）` }];
      }
      return copy;
    }
    if (copy.content) copy.content = copy.content.map(visit);
    return copy;
  };
  return { doc: visit(source), matched, unmatched };
}

function outlineTitle(item: PdfOutlineItem) {
  return [{
    type: 'text',
    text: item.title,
    marks: [{ type: 'pdfOutlineReference', attrs: { page: item.page } }],
  }];
}

function bulletTree(items: PdfOutlineItem[]) {
  type Branch = { item: PdfOutlineItem; children: Branch[] };
  const roots: Branch[] = [];
  const stack: Branch[] = [];
  for (const item of items) {
    while (stack.length && stack[stack.length - 1].item.level >= item.level) stack.pop();
    const branch = { item, children: [] };
    if (stack.length) stack[stack.length - 1].children.push(branch);
    else roots.push(branch);
    stack.push(branch);
  }
  const toItem = (branch: Branch): any => ({
    type: 'listItem',
    content: [
      { type: 'paragraph', content: outlineTitle(branch.item) },
      ...(branch.children.length ? [{ type: 'bulletList', content: branch.children.map(toItem) }] : []),
    ],
  });
  return roots.length ? [{ type: 'bulletList', content: roots.map(toItem) }] : [];
}

export function outlineToTiptap(outline: PdfOutlineItem[], reviewed = false): any[] {
  const nodes: any[] = [
    { type: 'heading', attrs: { level: 2 }, content: [{ type: 'text', text: reviewed ? '文档大纲（AI复核）' : '文档大纲' }] },
  ];
  const bold: PdfOutlineItem[] = [];
  for (const item of outline) {
    if (item.kind === 'numbered') {
      if (bold.length) nodes.push(...bulletTree(bold.splice(0)));
      nodes.push({ type: 'paragraph', content: outlineTitle(item) });
    } else {
      bold.push(item);
    }
  }
  if (bold.length) nodes.push(...bulletTree(bold));
  return nodes;
}
