import './style.css';
import { download, type EditorAdapter, type Patch } from '../../context/types';

const ORIGIN = 'https://embed.diagrams.net';
const FRAME_SRC = `${ORIGIN}/?embed=1&ui=atlas&proto=json&spin=1&libraries=1&lang=zh&saveAndExit=0&noSaveBtn=1&noExitBtn=1`;

const DEFAULT_XML = `<mxGraphModel dx="1000" dy="600" grid="1" gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" pageWidth="827" pageHeight="1169" math="0" shadow="0"><root><mxCell id="0"/><mxCell id="1" parent="0"/><mxCell id="2" value="公告" style="rounded=1;whiteSpace=wrap;html=1;" vertex="1" parent="1"><mxGeometry x="120" y="220" width="140" height="70" as="geometry"/></mxCell><mxCell id="3" value="解读" style="rounded=1;whiteSpace=wrap;html=1;" vertex="1" parent="1"><mxGeometry x="360" y="220" width="140" height="70" as="geometry"/></mxCell><mxCell id="4" value="结论" style="rounded=1;whiteSpace=wrap;html=1;" vertex="1" parent="1"><mxGeometry x="600" y="220" width="140" height="70" as="geometry"/></mxCell><mxCell id="5" style="edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;html=1;endArrow=classic;" edge="1" parent="1" source="2" target="3"><mxGeometry relative="1" as="geometry"/></mxCell><mxCell id="6" style="edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;html=1;endArrow=classic;" edge="1" parent="1" source="3" target="4"><mxGeometry relative="1" as="geometry"/></mxCell></root></mxGraphModel>`;

function readXml(content: unknown) {
  if (!content || typeof content !== 'object' || Array.isArray(content)) return '';
  const body = content as { format?: unknown; xml?: unknown };
  return body.format === 'drawio-v1' && typeof body.xml === 'string' ? body.xml : '';
}

function payload(xml: string) {
  return { format: 'drawio-v1', xml };
}

function readMessage(data: unknown) {
  if (typeof data === 'string') {
    try {
      const parsed = JSON.parse(data) as unknown;
      return parsed && typeof parsed === 'object' ? parsed as Record<string, unknown> : null;
    } catch {
      return null;
    }
  }
  return data && typeof data === 'object' ? data as Record<string, unknown> : null;
}

export function mountDiagram(
  container: HTMLElement,
  changed: () => void,
  onDirty: (assetId: string, content: unknown) => void,
): EditorAdapter {
  let revision = 0;
  let assetId = '';
  let loading = false;
  let xml = DEFAULT_XML;
  let title = '绘图';
  let ready = false;
  let acceptSave = false;
  let fitted = false;

  container.innerHTML = `<iframe class="diagram-frame" title="绘图" referrerpolicy="no-referrer"></iframe>`;
  const frame = container.querySelector<HTMLIFrameElement>('iframe')!;

  const post = (message: unknown) => {
    frame.contentWindow?.postMessage(JSON.stringify(message), ORIGIN);
  };

  const emitDirty = () => {
    if (!assetId || loading) return;
    onDirty(assetId, payload(xml));
  };

  const sendLoad = () => {
    acceptSave = false;
    fitted = false;
    post({
      action: 'load',
      xml,
      autosave: 1,
      fit: 1,
      maxFitScale: 1,
      title,
    });
  };

  const fitOnce = () => {
    if (!ready || fitted || container.clientWidth < 40 || container.clientHeight < 40) return;
    fitted = true;
    post({ action: 'fit', border: 40, maxScale: 1 });
  };

  const remember = (next: unknown) => {
    if (typeof next !== 'string' || !next.trim() || next === xml) return;
    xml = next;
    emitDirty();
    changed();
  };

  window.addEventListener('message', (event) => {
    if (event.origin !== ORIGIN || event.source !== frame.contentWindow) return;
    const message = readMessage(event.data);
    if (!message) return;
    if (message.event === 'init') {
      ready = true;
      sendLoad();
      return;
    }
    if (message.event === 'load') {
      acceptSave = true;
      fitOnce();
      return;
    }
    if ((message.event === 'autosave' || message.event === 'save') && acceptSave) {
      remember(message.xml);
      if (message.event === 'save') post({ action: 'status', modified: false });
    }
  });

  new ResizeObserver(() => { fitOnce(); }).observe(container);
  frame.src = FRAME_SRC;

  return {
    async load(asset) {
      loading = true;
      assetId = asset.id;
      revision = asset.revision;
      title = asset.name || '绘图';
      const stored = readXml(asset.content);
      const known = Boolean(stored);
      xml = stored || DEFAULT_XML;
      loading = false;
      if (ready) sendLoad();
      if (!known) emitDirty();
      else changed();
    },
    context() {
      if (!assetId || !xml.trim() || xml.length > 40000) return null;
      return {
        document_id: assetId,
        surface: 'diagram',
        revision,
        selection: { text: xml },
        surrounding: xml.slice(0, 4000),
      };
    },
    anchor() {
      return this.context();
    },
    apply(patch: Patch) {
      if (patch.revision !== revision) throw new Error('绘图已更改，请重新生成建议。');
      xml = String(patch.replacement ?? '');
      if (ready) sendLoad();
      emitDirty();
    },
    snapshot() {
      return payload(xml);
    },
    undo() { post({ action: 'invokeAction', actionName: 'undo' }); },
    export() {
      download('diagram.drawio', xml);
    },
  };
}
