export type PdfOutlineItem = {
  id: string;
  page: number;
  order: number;
  title: string;
  kind: 'numbered' | 'bold';
  level: number;
};

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
      { type: 'paragraph', content: [{ type: 'text', text: branch.item.title }] },
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
      nodes.push({ type: 'paragraph', content: [{ type: 'text', text: item.title }] });
    } else {
      bold.push(item);
    }
  }
  if (bold.length) nodes.push(...bulletTree(bold));
  return nodes;
}
