import { describe, expect, it } from 'vitest';
import {
  annotationPdfBoxes, highlightInsertContent, pointInPdfBoxes, quadsToBoxes, textCoveredByHighlight, textItemsInBoxes, type PixelBox,
} from './highlight-note';

function box(left: number, top: number, width: number, height: number): PixelBox {
  return { left, top, width, height, right: left + width, bottom: top + height };
}

describe('highlight text for notebook', () => {
  it('keeps a fully covered span and drops text outside the highlight', () => {
    const text = textCoveredByHighlight([
      { text: '收入确认', box: box(10, 20, 80, 16) },
      { text: '下一行', box: box(10, 48, 60, 16) },
    ], [box(8, 18, 90, 20)]);
    expect(text).toBe('收入确认');
  });

  it('slices a partially covered span and joins the next line', () => {
    const text = textCoveredByHighlight([
      { text: 'abcdefghij', box: box(0, 0, 100, 10) },
      { text: '第二行', box: box(0, 16, 40, 10) },
    ], [box(40, 0, 60, 12), box(0, 16, 40, 12)]);
    expect(text).toBe('efghij\n第二行');
  });

  it('turns one line into text and several lines into hard breaks', () => {
    expect(highlightInsertContent('  收入确认  ')).toEqual([{ type: 'text', text: '收入确认' }]);
    expect(highlightInsertContent('第一行\n第二行')).toEqual([
      { type: 'text', text: '第一行' },
      { type: 'hardBreak' },
      { type: 'text', text: '第二行' },
    ]);
    expect(highlightInsertContent('  \n  ')).toBeNull();
  });

  it('hits the quad that contains the click and reads the text items inside it', () => {
    const boxes = annotationPdfBoxes([10, 30, 70, 30, 10, 18, 70, 18], [0, 0, 1, 1]);
    expect(boxes).toEqual([[10, 18, 70, 30]]);
    expect(pointInPdfBoxes(20, 24, boxes)).toBe(true);
    expect(pointInPdfBoxes(0, 0, boxes)).toBe(false);
    expect(annotationPdfBoxes(null, [10, 18, 70, 30])).toEqual([[10, 18, 70, 30]]);
    expect(textItemsInBoxes([
      { str: '收入', x: 12, y: 20, width: 18, height: 8 },
      { str: '确认', x: 36, y: 20, width: 18, height: 8 },
      { str: '页外', x: 80, y: 20, width: 18, height: 8 },
    ], boxes)).toBe('收入 确认');
  });

  it('maps clip-path quads back onto the highlight box', () => {
    const boxes = quadsToBoxes(box(100, 200, 200, 40), [
      { x: 0, y: 0, width: 1, height: 0.4 },
      { x: 0, y: 0.6, width: 0.5, height: 0.4 },
    ]);
    expect(boxes).toEqual([
      box(100, 200, 200, 16),
      box(100, 224, 100, 16),
    ]);
  });
});
