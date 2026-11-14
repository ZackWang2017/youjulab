import { expect, it } from 'vitest';
import { parseTodoMarkdown, serializeTodoMarkdown } from './todos';

it('reads legacy items as active without changing completion', () => {
  expect(parseTodoMarkdown('- [ ] A\n* [X] B')).toEqual([
    { done: false, text: 'A' }, { done: true, text: 'B' },
  ]);
});

it('round trips archive markers separately from completion and hides metadata from text', () => {
  const items = [
    { done: false, text: 'A', archived: true },
    { done: true, text: 'B', archived: true },
    { done: true, text: 'C' },
  ];
  expect(parseTodoMarkdown(serializeTodoMarkdown(items))).toEqual(items);
  expect(serializeTodoMarkdown([{ done: false, text: 'A' }])).toBe('- [ ] A');
});
