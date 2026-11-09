import type { CatalogItem } from './types';

export type AssetIndexPopup = {
  element: HTMLElement;
  setItems: (items: CatalogItem[], stageLabel: string) => void;
  setSelected: (index: number) => void;
  selectedIndex: () => number;
  selectedItem: () => CatalogItem | null;
  move: (delta: number) => CatalogItem | null;
  position: (clientRect: (() => DOMRect | null) | null | undefined) => void;
  destroy: () => void;
};

export function createAssetIndexPopup(onPick: (item: CatalogItem) => void): AssetIndexPopup {
  const element = document.createElement('div');
  element.className = 'asset-index-popup';
  element.setAttribute('role', 'listbox');
  element.hidden = true;
  document.body.append(element);

  let items: CatalogItem[] = [];
  let selected = 0;

  const paint = () => {
    if (!items.length) {
      element.innerHTML = '<div class="asset-index-empty">无匹配项</div>';
      return;
    }
    element.innerHTML = items.map((item, index) => (
      `<button type="button" class="asset-index-option${index === selected ? ' is-selected' : ''}" role="option" aria-selected="${index === selected}" data-index="${index}"><span class="asset-index-option-label"></span><span class="asset-index-option-meta"></span></button>`
    )).join('');
    element.querySelectorAll<HTMLButtonElement>('.asset-index-option').forEach((button, index) => {
      const item = items[index];
      button.querySelector('.asset-index-option-label')!.textContent = item.label;
      button.querySelector('.asset-index-option-meta')!.textContent = item.kind || '';
      button.onmousedown = event => {
        event.preventDefault();
        selected = index;
        onPick(item);
      };
    });
  };

  return {
    element,
    setItems(next, stageLabel) {
      items = next;
      selected = 0;
      element.dataset.stage = stageLabel;
      element.hidden = false;
      paint();
    },
    setSelected(index) {
      if (!items.length) return;
      selected = Math.max(0, Math.min(items.length - 1, index));
      paint();
    },
    selectedIndex: () => selected,
    selectedItem: () => items[selected] || null,
    move(delta) {
      if (!items.length) return null;
      selected = (selected + delta + items.length) % items.length;
      paint();
      return items[selected];
    },
    position(clientRect) {
      const rect = clientRect?.() || null;
      if (!rect || element.hidden) return;
      const place = () => {
        const gap = 6;
        const margin = 8;
        const vw = window.innerWidth;
        const vh = window.innerHeight;
        const width = Math.min(Math.max(element.offsetWidth || 220, 220), vw - margin * 2);
        const spaceBelow = Math.max(0, vh - rect.bottom - gap - margin);
        const spaceAbove = Math.max(0, rect.top - gap - margin);
        const preferBelow = spaceBelow >= Math.min(160, spaceAbove + 1);
        const available = Math.max(96, preferBelow ? spaceBelow : spaceAbove);
        const maxHeight = Math.min(280, available);
        element.style.maxHeight = `${maxHeight}px`;
        element.style.width = `${width}px`;
        const height = Math.min(Math.max(element.offsetHeight, 1), maxHeight);
        let top = rect.bottom + gap;
        let placement: 'above' | 'below' = 'below';
        if (!preferBelow || top + height > vh - margin) {
          top = Math.max(margin, rect.top - gap - height);
          placement = 'above';
        }
        const left = Math.min(vw - width - margin, Math.max(margin, rect.left));
        element.style.top = `${top}px`;
        element.style.left = `${left}px`;
        element.dataset.placement = placement;
      };
      place();
      requestAnimationFrame(place);
    },
    destroy() {
      element.remove();
      items = [];
    },
  };
}
