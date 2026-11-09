import { Extension, mergeAttributes, Node } from '@tiptap/core';
import { PluginKey } from '@tiptap/pm/state';
import Suggestion, { type SuggestionKeyDownProps, type SuggestionOptions, type SuggestionProps } from '@tiptap/suggestion';
import { createOfficeAssetCatalog, loadCatalogItems } from './catalog';
import { createAssetIndexPopup, type AssetIndexPopup } from './popup';
import { parseAssetIndexQuery, pathAfterSelectingKind, pathAfterSelectingProject } from './query';
import type { AssetCatalog, AssetIndexDeps, CatalogItem } from './types';

export const assetIndexPluginKey = new PluginKey('assetIndexSuggestion');

type AssetReferenceOptions = {
  openAsset: AssetIndexDeps['openAsset'];
};

export const AssetReference = Node.create<AssetReferenceOptions>({
  name: 'assetReference',
  group: 'inline',
  inline: true,
  atom: true,
  selectable: true,

  addOptions() {
    return {
      openAsset: async () => undefined,
    };
  },

  addAttributes() {
    return {
      assetId: { default: null },
      projectId: { default: null },
      kind: { default: null },
      label: { default: '' },
    };
  },

  parseHTML() {
    return [{
      tag: 'span.asset-ref[data-asset-id]',
      getAttrs: element => {
        const node = element as HTMLElement;
        return {
          assetId: node.dataset.assetId || null,
          projectId: node.dataset.projectId || null,
          kind: node.dataset.assetKind || null,
          label: node.textContent || node.dataset.assetLabel || '',
        };
      },
    }];
  },

  renderHTML({ node, HTMLAttributes }) {
    return ['span', mergeAttributes(
      {
        class: 'asset-ref',
        'data-asset-id': node.attrs.assetId,
        'data-project-id': node.attrs.projectId,
        'data-asset-kind': node.attrs.kind,
        'data-asset-label': node.attrs.label,
        contenteditable: 'false',
      },
      HTMLAttributes,
    ), String(node.attrs.label || '资产')];
  },

  renderText({ node }) {
    const id = String(node.attrs.assetId || '');
    const label = String(node.attrs.label || '资产');
    return id ? `«asset:${id}|${label}»` : label;
  },

  addNodeView() {
    return ({ node }) => {
      const dom = document.createElement('span');
      dom.className = 'asset-ref';
      dom.dataset.assetId = String(node.attrs.assetId || '');
      dom.dataset.projectId = String(node.attrs.projectId || '');
      dom.dataset.assetKind = String(node.attrs.kind || '');
      dom.textContent = String(node.attrs.label || '资产');
      dom.contentEditable = 'false';
      dom.title = '⌘/Ctrl+点击打开资产';
      const open = this.options.openAsset;
      dom.addEventListener('click', event => {
        if (!(event.metaKey || event.ctrlKey)) return;
        event.preventDefault();
        event.stopPropagation();
        const id = String(dom.dataset.assetId || '');
        if (id) void Promise.resolve(open(id));
      });
      return {
        dom,
        update: updated => {
          if (updated.type.name !== 'assetReference') return false;
          dom.dataset.assetId = String(updated.attrs.assetId || '');
          dom.dataset.projectId = String(updated.attrs.projectId || '');
          dom.dataset.assetKind = String(updated.attrs.kind || '');
          dom.textContent = String(updated.attrs.label || '资产');
          return true;
        },
        ignoreMutation: () => true,
        stopEvent: event => {
          if (event.type === 'click' && ((event as MouseEvent).metaKey || (event as MouseEvent).ctrlKey)) return true;
          return false;
        },
      };
    };
  },
});

function stageLabel(stage: string) {
  if (stage === 'projects') return '项目';
  if (stage === 'kinds') return '类别';
  return '资产';
}

function createSuggestionConfig(options: {
  catalog: AssetCatalog;
}): Omit<SuggestionOptions<CatalogItem, CatalogItem>, 'editor'> {
  let popup: AssetIndexPopup | null = null;
  let latest: SuggestionProps<CatalogItem, CatalogItem> | null = null;

  const pick = (item: CatalogItem) => {
    latest?.command(item);
  };

  return {
    pluginKey: assetIndexPluginKey,
    char: '#',
    allowSpaces: false,
    allowedPrefixes: null,
    decorationClass: 'asset-index-suggestion',
    allow: ({ state, range }) => {
      const text = state.doc.textBetween(range.from, range.to, '\n', '\0');
      return text.startsWith('#/');
    },
    items: async ({ query }) => {
      const parsed = parseAssetIndexQuery(query);
      if (!parsed.active) return [];
      return loadCatalogItems(options.catalog, parsed);
    },
    command: ({ editor, range, props }) => {
      const query = editor.state.doc.textBetween(range.from + 1, range.to, '\n', '\0');
      const parsed = parseAssetIndexQuery(query);
      if (props.asset) {
        editor
          .chain()
          .focus()
          .insertContentAt(range, [
            {
              type: 'assetReference',
              attrs: {
                assetId: props.asset.assetId,
                projectId: props.asset.projectId,
                kind: props.asset.kind,
                label: props.asset.label,
              },
            },
            { type: 'text', text: ' ' },
          ])
          .run();
        return;
      }
      if (parsed.stage === 'projects') {
        editor.chain().focus().insertContentAt(range, `#${pathAfterSelectingProject(props.pathSegment)}`).run();
        return;
      }
      if (parsed.stage === 'kinds') {
        editor.chain().focus().insertContentAt(range, `#${pathAfterSelectingKind(parsed.projectKey, props.pathSegment)}`).run();
      }
    },
    render: () => ({
      onStart(props) {
        latest = props;
        popup?.destroy();
        popup = createAssetIndexPopup(pick);
        popup.setItems(props.items, stageLabel(parseAssetIndexQuery(props.query).stage));
        popup.position(props.clientRect);
      },
      onUpdate(props) {
        latest = props;
        if (!popup) popup = createAssetIndexPopup(pick);
        popup.setItems(props.items, stageLabel(parseAssetIndexQuery(props.query).stage));
        popup.position(props.clientRect);
      },
      onKeyDown(props: SuggestionKeyDownProps) {
        if (!popup) return false;
        if (props.event.key === 'Escape') {
          popup.element.hidden = true;
          return true;
        }
        if (props.event.key === 'ArrowDown') {
          props.event.preventDefault();
          popup.move(1);
          return true;
        }
        if (props.event.key === 'ArrowUp') {
          props.event.preventDefault();
          popup.move(-1);
          return true;
        }
        if (props.event.key === 'Enter') {
          const item = popup.selectedItem();
          if (!item) return true;
          props.event.preventDefault();
          pick(item);
          return true;
        }
        return false;
      },
      onExit() {
        latest = null;
        popup?.destroy();
        popup = null;
      },
    }),
  };
}

export const AssetIndexSuggestion = Extension.create<{ catalog: AssetCatalog }>({
  name: 'assetIndexSuggestion',

  addOptions() {
    return {
      catalog: createOfficeAssetCatalog(),
    };
  },

  addProseMirrorPlugins() {
    return [
      Suggestion({
        editor: this.editor,
        ...createSuggestionConfig({ catalog: this.options.catalog }),
      }),
    ];
  },
});

export function createAssetIndexExtensions(deps: AssetIndexDeps) {
  const catalog = deps.catalog || createOfficeAssetCatalog();
  return [
    AssetReference.configure({ openAsset: deps.openAsset }),
    AssetIndexSuggestion.configure({ catalog }),
  ];
}
