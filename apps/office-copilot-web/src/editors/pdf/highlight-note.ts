/** 把 PDF 高亮盖住的文字抽出来，供插入笔记。 */

export type PixelBox = {
  left: number;
  right: number;
  top: number;
  bottom: number;
  width: number;
  height: number;
};

export type HighlightSpan = { text: string; box: PixelBox; rtl?: boolean };

export type HighlightInsert = Array<{ type: 'text'; text: string } | { type: 'hardBreak' }>;

function overlapSlice(text: string, box: PixelBox, rects: PixelBox[], rtl = false): string {
  if (!text || box.width < 0.5 || box.height < 0.5) return '';
  let start = text.length;
  let end = 0;
  let hit = false;
  for (const rect of rects) {
    const overlapY = Math.min(box.bottom, rect.bottom) - Math.max(box.top, rect.top);
    if (overlapY <= 0) continue;
    const left = Math.max(box.left, rect.left);
    const right = Math.min(box.right, rect.right);
    if (right - left < 0.8) continue;
    hit = true;
    if (left <= box.left + 0.8 && right >= box.right - 0.8) {
      start = 0;
      end = text.length;
      break;
    }
    let from = (left - box.left) / box.width;
    let to = (right - box.left) / box.width;
    if (rtl) {
      const flippedFrom = 1 - to;
      to = 1 - from;
      from = flippedFrom;
    }
    start = Math.min(start, Math.max(0, Math.floor(from * text.length)));
    end = Math.max(end, Math.min(text.length, Math.ceil(to * text.length)));
  }
  if (!hit || end <= start) return '';
  return text.slice(start, end);
}

export function textCoveredByHighlight(spans: HighlightSpan[], rects: PixelBox[]): string {
  if (!rects.length) return '';
  const parts: string[] = [];
  let lastBottom = -1;
  for (const span of spans) {
    const piece = overlapSlice(span.text, span.box, rects, span.rtl);
    if (!piece) continue;
    if (lastBottom >= 0 && span.box.top > lastBottom - 1) parts.push('\n');
    parts.push(piece);
    lastBottom = span.box.bottom;
  }
  return parts.join('').replace(/[ \t]+\n/g, '\n').replace(/[ \t]{2,}/g, ' ').trim();
}

export function quadsToBoxes(section: PixelBox, quads: Array<{ x: number; y: number; width: number; height: number }>): PixelBox[] {
  const boxes = quads.flatMap(quad => {
    const width = quad.width * section.width;
    const height = quad.height * section.height;
    if (width < 0.5 || height < 0.5) return [];
    const left = section.left + quad.x * section.width;
    const top = section.top + quad.y * section.height;
    return [{ left, top, width, height, right: left + width, bottom: top + height }];
  });
  return boxes.length ? boxes : [section];
}

/** PDF 用户空间里的矩形：[x0, y0, x1, y1]，不要求 x0 < x1。 */
export function pointInPdfBoxes(x: number, y: number, boxes: number[][], pad = 1.5): boolean {
  return boxes.some(box => {
    if (box.length < 4) return false;
    const x0 = Math.min(box[0], box[2]) - pad;
    const x1 = Math.max(box[0], box[2]) + pad;
    const y0 = Math.min(box[1], box[3]) - pad;
    const y1 = Math.max(box[1], box[3]) + pad;
    return x >= x0 && x <= x1 && y >= y0 && y <= y1;
  });
}

/** QuadPoints 每 8 个数是一个四边形，取外接矩形。 */
export function quadPointsToBoxes(quadPoints: ArrayLike<number> | null | undefined): number[][] {
  if (!quadPoints || quadPoints.length < 8) return [];
  const boxes: number[][] = [];
  for (let i = 0; i + 7 < quadPoints.length; i += 8) {
    const xs = [quadPoints[i], quadPoints[i + 2], quadPoints[i + 4], quadPoints[i + 6]];
    const ys = [quadPoints[i + 1], quadPoints[i + 3], quadPoints[i + 5], quadPoints[i + 7]];
    const left = Math.min(...xs);
    const bottom = Math.min(...ys);
    const right = Math.max(...xs);
    const top = Math.max(...ys);
    if (right - left < 0.4 || top - bottom < 0.4) continue;
    boxes.push([left, bottom, right, top]);
  }
  return boxes;
}

export function annotationPdfBoxes(quadPoints: ArrayLike<number> | null | undefined, rect?: ArrayLike<number> | null): number[][] {
  const quads = quadPointsToBoxes(quadPoints);
  if (quads.length) return quads;
  if (!rect || rect.length < 4) return [];
  const left = Math.min(rect[0], rect[2]);
  const bottom = Math.min(rect[1], rect[3]);
  const right = Math.max(rect[0], rect[2]);
  const top = Math.max(rect[1], rect[3]);
  if (right - left < 0.4 || top - bottom < 0.4) return [];
  return [[left, bottom, right, top]];
}

export type PdfTextItem = { str: string; x: number; y: number; width: number; height: number };

/** 用文字块中心是否落在高亮框里，按阅读顺序拼出被盖住的文字。 */
export function textItemsInBoxes(items: PdfTextItem[], boxes: number[][]): string {
  const hits = items.filter(item => item.str && pointInPdfBoxes(
    item.x + Math.max(item.width, 0.1) / 2,
    item.y + Math.max(item.height, 0.1) / 2,
    boxes,
    2,
  ));
  hits.sort((a, b) => (b.y - a.y) || (a.x - b.x));
  let out = '';
  let last: PdfTextItem | null = null;
  for (const item of hits) {
    if (last) {
      const lineBreak = last.y - item.y > Math.max(last.height, item.height, 1) * 0.6;
      const gap = item.x - (last.x + last.width);
      if (lineBreak) out += '\n';
      else if (gap > 1.5 && !out.endsWith(' ') && !item.str.startsWith(' ')) out += ' ';
    }
    out += item.str;
    last = item;
  }
  return out.replace(/[ \t]+\n/g, '\n').replace(/[ \t]{2,}/g, ' ').trim();
}

export function highlightInsertContent(raw: string): HighlightInsert | null {
  const lines = raw.replace(/\r\n?/g, '\n').split('\n').map(line => line.replace(/[ \t]+/g, ' ').trim()).filter(Boolean);
  if (!lines.length) return null;
  const content: HighlightInsert = [];
  lines.forEach((line, index) => {
    if (index) content.push({ type: 'hardBreak' });
    content.push({ type: 'text', text: line });
  });
  return content;
}
