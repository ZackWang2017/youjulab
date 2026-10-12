// 保留给以后由模型直接出图。当前绘图页使用 diagrams.net。
import './mermaid.css';
import { download, type EditorAdapter, type Patch } from '../../context/types';

const DEFAULT_SOURCE = 'flowchart LR\n  公告 --> 解读 --> 结论\n';

function readSource(content: unknown) {
  if (!content || typeof content !== 'object' || Array.isArray(content)) return '';
  const source = (content as { source?: unknown }).source;
  return typeof source === 'string' ? source : '';
}

function payload(source: string) {
  return { format: 'mermaid-v1', source };
}

export function mountMermaidDiagram(
  container: HTMLElement,
  changed: () => void,
  onDirty: (assetId: string, content: unknown) => void,
): EditorAdapter {
  let revision = 0;
  let assetId = '';
  let loading = false;
  let source = DEFAULT_SOURCE;
  let renderToken = 0;
  let timer = 0;

  container.innerHTML = `
    <div class="diagram-stage">
      <textarea aria-label="绘图文本" spellcheck="false" placeholder="在这里写 Mermaid 文本。例如 flowchart、timeline、ishikawa-beta。"></textarea>
      <div class="diagram-preview" aria-label="绘图预览"></div>
    </div>`;
  const input = container.querySelector('textarea')!;
  const preview = container.querySelector<HTMLElement>('.diagram-preview')!;

  const emitDirty = () => {
    if (!assetId || loading) return;
    onDirty(assetId, payload(source));
  };

  const paint = async (text: string) => {
    const token = ++renderToken;
    const body = text.trim();
    if (!body) {
      preview.replaceChildren();
      return;
    }
    try {
      const mermaid = (await import('mermaid')).default;
      mermaid.initialize({
        startOnLoad: false,
        securityLevel: 'strict',
        theme: 'neutral',
        fontFamily: 'inherit',
        themeVariables: { fontSize: '22px' },
        flowchart: { padding: 28, nodeSpacing: 48, rankSpacing: 56 },
      });
      const id = `invp-diagram-${token}`;
      const { svg } = await mermaid.render(id, body);
      if (token !== renderToken) return;
      preview.innerHTML = svg;
      const drawn = preview.querySelector('svg');
      if (drawn) fitDiagram(drawn, preview);
    } catch (err) {
      if (token !== renderToken) return;
      const message = err instanceof Error ? err.message : '图形无法绘制。';
      preview.replaceChildren();
      const note = document.createElement('p');
      note.className = 'diagram-error';
      note.textContent = message.split('\n')[0] || '图形无法绘制。';
      preview.append(note);
    }
  };

  const fitDiagram = (drawn: SVGSVGElement, pane: HTMLElement) => {
    const box = drawn.viewBox.baseVal;
    const naturalW = box?.width || drawn.getBBox().width;
    const naturalH = box?.height || drawn.getBBox().height;
    if (!naturalW || !naturalH) return;
    const roomW = Math.max(pane.clientWidth - 48, 1);
    const scale = Math.max(roomW / naturalW, 1);
    drawn.style.width = `${Math.round(naturalW * scale)}px`;
    drawn.style.maxWidth = 'none';
    drawn.style.minWidth = '0';
    drawn.style.height = 'auto';
  };

  const schedule = () => {
    window.clearTimeout(timer);
    timer = window.setTimeout(() => { void paint(source); }, 200);
  };

  input.addEventListener('input', () => {
    source = input.value;
    schedule();
    emitDirty();
    changed();
  });

  return {
    async load(asset) {
      loading = true;
      assetId = asset.id;
      revision = asset.revision;
      const stored = readSource(asset.content);
      const known = Boolean(asset.content && typeof asset.content === 'object' && typeof (asset.content as { source?: unknown }).source === 'string');
      source = known ? stored : DEFAULT_SOURCE;
      input.value = source;
      loading = false;
      void paint(source);
      if (!known) emitDirty();
      else changed();
    },
    context() {
      if (!assetId) return null;
      const text = source.trim();
      if (!text) return null;
      return {
        document_id: assetId,
        surface: 'diagram',
        revision,
        selection: { text: source },
        surrounding: source.slice(0, 4000),
      };
    },
    anchor() {
      return this.context();
    },
    apply(patch: Patch) {
      if (patch.revision !== revision) throw new Error('绘图已更改，请重新生成建议。');
      source = String(patch.replacement ?? '');
      input.value = source;
      void paint(source);
      emitDirty();
    },
    snapshot() {
      return payload(source);
    },
    undo() { input.focus(); document.execCommand('undo'); },
    export() {
      download('diagram.mmd', source);
    },
  };
}
