import { createIcons, icons } from 'lucide';
import * as echarts from 'echarts';
import { office } from '../../office-client/client';
import { download, type EditorAdapter } from '../../context/types';
import type { Asset } from '../../workspace/session';
import { mountThinkingStatus } from '../../ui/thinking-status';
import './style.css';

type Plan = { tool: string; summary: string; code: string; parameters: Record<string, unknown>; assumptions: string[] };
type Content = { format: 'youju-strategy-v1'; objective: string; code: string; plan: Plan | null; messages: { role: string; content: string }[]; dataset: { name: string; symbols: string[]; rows: (string | number)[][]; basis: string; source: string; sha256: string } | null };
type Run = { id: string; status: string; revision: number; created_at: string; error: string; tool: string; events: { time: string; message: string }[]; plan?: Plan; code?: string; data_hash?: string; result?: { metrics: Record<string, number>; series: [string, number][]; basis: string; validation: string; engine_version: string; execution: string } };
const empty = (): Content => ({ format: 'youju-strategy-v1', objective: '', code: '', plan: null, messages: [], dataset: null });
const escape = (text: string) => text.replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]!);
const statusName: Record<string, string> = { queued: '排队中', running: '运行中', validating: '校验中', completed: '已完成', failed: '失败', cancelled: '已取消' };
const percent = (v: number) => `${(v * 100).toFixed(2)}%`;

export function mountStrategy(container: HTMLElement, onSaved: (asset: Asset) => void): EditorAdapter {
  let asset: Asset | null = null;
  let content = empty();
  let busy = false;
  let epoch = 0;
  let saving: Promise<void> = Promise.resolve();
  let dirty = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let polling: ReturnType<typeof setTimeout> | undefined;
  let tab = 'research';
  let runs: Run[] = [];
  let selectedRun: Run | null = null;
  let chart: echarts.ECharts | null = null;
  let requestKey = '';
  const comparison = new Set<string>();
  container.innerHTML = `<div class="strategy-shell"><header class="strategy-toolbar"><div class="strategy-tabs" role="tablist"><button type="button" data-tab="research">研究</button><button type="button" data-tab="code">程序与参数</button><button type="button" data-tab="results">研究结果</button><button type="button" data-tab="runs">运行记录</button><button type="button" data-tab="compare">结果对比</button></div><span data-save></span><button type="button" data-import><i data-lucide="database"></i>导入数据</button><button type="button" class="primary" data-run><i data-lucide="play"></i>运行回测</button></header><div class="strategy-summary"><strong data-name>策略研究</strong><span data-data>未导入数据</span><span data-tool></span></div><div class="strategy-alert" role="alert" data-alert hidden></div><div class="strategy-content"><section class="strategy-research" data-panel="research"><div class="strategy-conversation"><div class="strategy-messages" data-messages></div><div data-thinking></div><form data-ask><textarea rows="3" maxlength="12000" required placeholder="描述研究目标，或补充、修改已有策略…" aria-label="研究目标"></textarea><div><span data-capabilities></span><button type="submit" class="primary"><i data-lucide="sparkles"></i>生成研究方案</button></div></form></div><aside class="strategy-plan"><h3>研究方案</h3><div data-plan></div></aside></section><section class="strategy-program" data-panel="code" hidden><div class="strategy-program-bar"><strong>strategy.py</strong><span>自动保存</span><button type="button" data-download title="下载研究程序"><i data-lucide="download"></i></button></div><textarea data-code spellcheck="false" aria-label="策略 Python 程序"></textarea><details><summary>运行参数</summary><pre data-parameters></pre></details></section><section class="strategy-results" data-panel="results" hidden><div data-result></div><div class="strategy-chart" data-chart></div><div data-result-notes></div></section><section class="strategy-runs" data-panel="runs" hidden><div data-runs></div></section><section class="strategy-comparison" data-panel="compare" hidden><div data-comparison></div></section></div></div>`;
  const $ = <T extends HTMLElement = HTMLElement>(selector: string) => container.querySelector<T>(selector)!;
  const code = $<HTMLTextAreaElement>('[data-code]');
  const ask = $<HTMLFormElement>('[data-ask]');
  const prompt = ask.querySelector('textarea')!;
  const thinking = mountThinkingStatus($('[data-thinking]'));
  const error = (message: string) => { $('[data-alert]').textContent = message; $('[data-alert]').hidden = !message; };
  function saved(next: Asset) {
    asset = next; content = next.content as Content; onSaved(next);
    window.dispatchEvent(new CustomEvent('office-saved'));
  }
  function controls() {
    $<HTMLButtonElement>('[data-run]').disabled = !asset || busy || dirty || !content.dataset || !content.plan || content.plan.tool === 'needs_input' || runs.some(r => ['queued', 'running', 'validating'].includes(r.status));
    $<HTMLButtonElement>('[data-import]').disabled = !asset || busy;
    ask.querySelector('button')!.disabled = !asset || busy;
    prompt.disabled = !asset || busy;
    code.disabled = !asset || busy;
    $('[data-save]').textContent = dirty ? '保存中…' : asset ? '已保存' : '';
  }
  function render() {
    $('[data-name]').textContent = asset?.name || '尚未创建策略';
    const d = content.dataset;
    $('[data-data]').textContent = d ? `${d.symbols.length} 个标的 · ${d.rows.length} 日 · ${d.rows[0][0]} — ${d.rows.at(-1)![0]} · ${d.basis === 'total_return' ? '全收益净值' : '价格收益'}` : '未导入历史数据';
    $('[data-tool]').textContent = content.plan ? ({ bt_allocation: 'bt · 固定配置', python_research: 'Python · 自定义研究', needs_input: '待补充条件' }[content.plan.tool] || '') : '';
    $('[data-messages]').innerHTML = content.messages.length ? content.messages.map(m => `<article class="strategy-message ${m.role === 'user' ? 'is-user' : ''}"><strong>${m.role === 'user' ? '我' : '量化研究 Agent'}</strong><div>${escape(m.content)}</div></article>`).join('') : '<div class="strategy-empty"><i data-lucide="flask-conical"></i><h3>开始一项研究</h3></div>';
    const p = content.plan;
    $('[data-plan]').innerHTML = p ? `<p>${escape(p.summary)}</p>${p.assumptions.map(a => `<div class="strategy-assumption">${escape(a)}</div>`).join('')}<pre>${escape(JSON.stringify(p.parameters, null, 2))}</pre>` : '<p class="strategy-muted">尚无研究方案</p>';
    if (d) $('[data-plan]').insertAdjacentHTML('beforeend', `<div class="strategy-data-meta"><h4>研究数据</h4><p>${escape(d.symbols.join(' · '))}</p><p>来源：${escape(d.source)}</p><p>${d.basis === 'total_return' ? '含分红全收益净值' : '价格序列，不额外计入分红'}</p></div>`);
    if (document.activeElement !== code) code.value = content.code;
    $('[data-parameters]').textContent = JSON.stringify(p?.parameters || {}, null, 2);
    controls(); createIcons({ icons });
  }
  async function flush() {
    clearTimeout(timer);
    if (!dirty || !asset) return saving;
    const id = asset.id, body = structuredClone(content);
    dirty = false;
    saving = saving.catch(() => {}).then(async () => {
      if (asset?.id !== id) return;
      try {
        const next = await office.json<Asset>(`/assets/${id}`, { method: 'PUT', body: JSON.stringify({ content: body, revision: asset.revision }) });
        if (asset?.id === id) {
          const pending = dirty ? content : null;
          saved(next);
          if (pending) content = pending;
          controls();
        }
      } catch (e) { dirty = true; error(e instanceof Error ? e.message : String(e)); controls(); throw e; }
    });
    return saving;
  }
  function setTab(next: string) {
    tab = next;
    container.querySelectorAll<HTMLElement>('[data-panel]').forEach(el => el.hidden = el.dataset.panel !== tab);
    container.querySelectorAll<HTMLElement>('[data-tab]').forEach(el => { el.classList.toggle('is-active', el.dataset.tab === tab); el.setAttribute('aria-selected', String(el.dataset.tab === tab)); });
    if (tab === 'results') requestAnimationFrame(() => chart?.resize());
    if (tab === 'compare') void compareRuns().catch(e => error(e instanceof Error ? e.message : String(e)));
  }
  async function poll(token = epoch) {
    clearTimeout(polling);
    if (!asset) return;
    const id = asset.id;
    try {
      const response = await office.json<{ runs: Run[] }>(`/assets/${id}/strategy/runs`);
      if (token !== epoch || id !== asset?.id) return;
      runs = response.runs;
      renderRuns(); controls();
      const active = runs.find(r => ['queued', 'running', 'validating'].includes(r.status));
      if (active) {
        if (tab === 'results') await viewRun(active.id, token);
        polling = setTimeout(() => { void poll(token); }, 1500);
      } else if (selectedRun && runs.find(r => r.id === selectedRun!.id)?.status !== selectedRun.status) await viewRun(selectedRun.id, token);
    } catch (e) { if (token === epoch) error(e instanceof Error ? e.message : String(e)); }
  }
  function renderRuns() {
    $('[data-runs]').innerHTML = runs.length ? `<table><thead><tr><th>对比</th><th>运行时间</th><th>资产版本</th><th>工具</th><th>状态</th><th></th></tr></thead><tbody>${runs.map(r => `<tr><td><input type="checkbox" data-compare-id="${escape(r.id)}" ${comparison.has(r.id) ? 'checked' : ''} ${r.status !== 'completed' ? 'disabled' : ''}></td><td>${escape(new Date(r.created_at).toLocaleString())}</td><td>V${r.revision}</td><td>${escape(r.tool)}</td><td>${statusName[r.status] || r.status}</td><td><button type="button" data-view-run="${escape(r.id)}">查看</button>${['queued', 'running', 'validating'].includes(r.status) ? `<button type="button" data-cancel-run="${escape(r.id)}">取消</button>` : ''}</td></tr>`).join('')}</tbody></table>` : '<p class="strategy-muted">暂无运行记录</p>';
  }
  async function viewRun(id: string, token = epoch) {
    if (!asset) return;
    const run = await office.json<Run>(`/assets/${asset.id}/strategy/runs/${id}`);
    if (token !== epoch) return;
    selectedRun = run;
    const result = run.result;
    $('[data-result]').innerHTML = `<div class="strategy-result-heading"><strong>V${run.revision} · ${statusName[run.status]}</strong><span>${escape(new Date(run.created_at).toLocaleString())}</span></div>${run.error ? `<p class="strategy-error">${escape(run.error)}</p>` : ''}${result ? `<div class="strategy-metrics">${[['total_return', '累计收益'], ['annual_return', '年化收益'], ['max_drawdown', '最大回撤'], ['volatility', '年化波动']].map(([key, label]) => `<div><span>${label}</span><strong>${percent(result.metrics[key])}</strong></div>`).join('')}</div>` : ''}<details ${result ? '' : 'open'}><summary>执行记录</summary>${run.events.map(e => `<p class="strategy-log">${escape(e.message)}</p>`).join('')}</details>`;
    $('[data-result-notes]').textContent = result ? `${result.basis === 'total_return' ? '全收益净值口径' : '价格收益口径，不额外加入分红'}。${result.execution}。${result.validation}。bt ${result.engine_version}` : '';
    $('[data-chart]').hidden = !result;
    chart?.dispose(); chart = null;
    if (result) {
      chart = echarts.init($('[data-chart]'));
      chart.setOption({ animation: false, tooltip: { trigger: 'axis' }, grid: { left: 55, right: 24, top: 20, bottom: 45 }, xAxis: { type: 'category', data: result.series.map(p => p[0]), boundaryGap: false }, yAxis: { type: 'value', scale: true }, series: [{ name: '策略净值', type: 'line', data: result.series.map(p => p[1]), showSymbol: false, lineStyle: { width: 2, color: '#148473' } }] });
    }
  }
  async function compareRuns() {
    const ids = [...comparison];
    if (!asset || ids.length < 2) { $('[data-comparison]').textContent = '请在运行记录中选择至少两次已完成的研究。'; return; }
    const token = epoch, id = asset.id;
    const details = await Promise.all(ids.slice(0, 6).map(run => office.json<Run>(`/assets/${id}/strategy/runs/${run}`)));
    if (token !== epoch) return;
    const compatible = details.every(r => r.data_hash === details[0].data_hash);
    $('[data-comparison]').innerHTML = `${compatible ? '' : '<p class="strategy-error">数据快照不同，下列结果不构成严格的同口径对比。</p>'}<table><thead><tr><th>版本 / 时间</th><th>累计收益</th><th>年化收益</th><th>最大回撤</th><th>波动率</th></tr></thead><tbody>${details.map(r => `<tr><td>V${r.revision} · ${escape(new Date(r.created_at).toLocaleString())}</td>${['total_return', 'annual_return', 'max_drawdown', 'volatility'].map(k => `<td>${r.result ? percent(r.result.metrics[k]) : '—'}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
  }
  async function importData() {
    if (!asset) return;
    const overlay = document.createElement('div'); overlay.className = 'dialog-overlay';
    overlay.innerHTML = `<form class="name-dialog strategy-data-dialog" role="dialog" aria-modal="true" aria-label="导入研究数据"><h2>导入历史数据</h2><label><span>文件</span><input type="file" accept=".csv,text/csv" required></label><label><span>数据口径</span><select name="basis"><option value="total_return">含分红全收益净值</option><option value="price">价格序列（不含额外分红）</option></select></label><label><span>数据来源</span><input name="source" required maxlength="500" placeholder="数据提供方、指数或产品口径"></label><pre>date,标的A,标的B\n2020-01-02,100,100\n2020-01-03,101,99</pre><p data-error role="alert"></p><div class="dialog-actions"><button type="button" data-cancel>取消</button><button type="submit" class="primary">导入</button></div></form>`;
    overlay.querySelector('[data-cancel]')!.addEventListener('click', () => overlay.remove());
    const token = epoch;
    overlay.querySelector('form')!.onsubmit = async event => {
      event.preventDefault();
      const form = event.currentTarget as HTMLFormElement;
      const file = form.querySelector<HTMLInputElement>('input[type=file]')!.files?.[0]; if (!file || !asset) return;
      const button = form.querySelector<HTMLButtonElement>('button[type=submit]')!; button.disabled = true;
      try {
        if (file.size > 4_000_000) throw new Error('数据文件不能超过 4 MB。');
        await flush();
        if (token !== epoch || !asset) { overlay.remove(); return; }
        const targetId = asset.id, targetRevision = asset.revision;
        const csv = await file.text();
        if (token !== epoch) { overlay.remove(); return; }
        const fields = new FormData(form);
        const next = await office.json<Asset>(`/assets/${targetId}/strategy/data`, { method: 'PUT', body: JSON.stringify({ csv, name: file.name, basis: fields.get('basis'), source: fields.get('source'), revision: targetRevision }) });
        if (token === epoch) { saved(next); render(); error(''); }
        overlay.remove();
      } catch (e) { form.querySelector('[data-error]')!.textContent = e instanceof Error ? e.message : String(e); }
      finally { button.disabled = false; }
    };
    document.body.append(overlay);
  }
  ask.onsubmit = async event => {
    event.preventDefault();
    if (!asset || busy || !prompt.value.trim()) return;
    const token = epoch, id = asset.id, instruction = prompt.value.trim();
    busy = true; controls(); error(''); thinking.clear(); thinking.begin();
    try {
      await flush();
      if (token !== epoch || !asset) return;
      await office.stream(`/assets/${id}/strategy/research`, { instruction, revision: asset.revision }, event => {
        if (token !== epoch) return;
        if (event.type === 'progress') thinking.note(event.message);
        if (event.type === 'thinking') thinking.append(event.text);
        if (event.type === 'error') throw new Error(event.error);
        if (event.type === 'done' && event.asset) { saved(event.asset); prompt.value = ''; render(); }
      });
    } catch (e) { if (token === epoch) error(e instanceof Error ? e.message : String(e)); }
    finally { if (token === epoch) { busy = false; thinking.finish(); controls(); } }
  };
  code.oninput = () => {
    content.code = code.value;
    if (content.plan && content.plan.tool !== 'needs_input') content.plan = { ...content.plan, tool: 'python_research', code: code.value };
    dirty = true; clearTimeout(timer); timer = setTimeout(() => { void flush().catch(() => {}); }, 800); controls();
  };
  $<HTMLButtonElement>('[data-run]').onclick = async () => {
    if (!asset || busy) return;
    const token = epoch;
    busy = true; controls(); error('');
    try {
      await flush();
      if (token !== epoch || !asset) return;
      requestKey ||= crypto.randomUUID();
      const run = await office.json<Run>(`/assets/${asset.id}/strategy/runs`, { method: 'POST', body: JSON.stringify({ revision: asset.revision, request_key: requestKey }) });
      requestKey = '';
      if (token !== epoch) return;
      setTab('results'); await viewRun(run.id, token); await poll(token);
    } catch (e) { if (token === epoch) error(e instanceof Error ? e.message : String(e)); }
    finally { if (token === epoch) { busy = false; controls(); } }
  };
  $('[data-runs]').onclick = event => {
    const target = (event.target as HTMLElement).closest<HTMLElement>('[data-view-run], [data-cancel-run]');
    if (!target || !asset) return;
    if (target.dataset.viewRun) { setTab('results'); void viewRun(target.dataset.viewRun).catch(e => error(String(e))); }
    else void office.json(`/assets/${asset.id}/strategy/runs/${target.dataset.cancelRun}/cancel`, { method: 'POST' }).then(() => poll()).catch(e => error(String(e)));
  };
  $('[data-runs]').onchange = event => {
    const target = event.target as HTMLInputElement;
    if (target.dataset.compareId) { if (target.checked) comparison.add(target.dataset.compareId); else comparison.delete(target.dataset.compareId); }
  };
  $('[data-import]').onclick = () => { void importData(); };
  $('[data-download]').onclick = () => {
    const blob = new Blob([content.code], { type: 'text/x-python' }); const url = URL.createObjectURL(blob);
    const a = document.createElement('a'); a.href = url; a.download = 'strategy.py'; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  container.querySelectorAll<HTMLElement>('[data-tab]').forEach(button => button.onclick = () => setTab(button.dataset.tab!));
  new ResizeObserver(() => chart?.resize()).observe(container);
  window.addEventListener('keydown', event => {
    if (!container.hidden && (event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') { event.preventDefault(); void flush().catch(() => {}); }
  });
  setTab('research'); render();
  return {
    async load(value) {
      await flush(); epoch++; clearTimeout(polling); busy = false; dirty = false; thinking.clear(); error('');
      asset = value.id ? { ...value, kind: 'strategy', project_id: null, updated_at: '' } : null;
      content = value.content ? structuredClone(value.content as Content) : empty();
      runs = []; selectedRun = null; comparison.clear(); chart?.dispose(); chart = null;
      $('[data-result]').replaceChildren(); $('[data-result-notes]').textContent = ''; $('[data-chart]').hidden = true;
      setTab('research'); render(); renderRuns();
      if (asset) { void poll(); void office.json<{ tools: { id: string; ready: boolean; message?: string }[] }>('/quant/capabilities').then(r => { $('[data-capabilities]').textContent = r.tools.find(t => t.id === 'python_research')?.ready ? '自定义沙箱就绪' : '自定义 Python 沙箱未配置'; }).catch(() => {}); }
    },
    snapshot: () => structuredClone(content), context: () => null, anchor: () => null,
    apply() { throw new Error('请通过策略工作区的量化研究 Agent 修改程序。'); }, undo() {},
    export() { download('strategy.youju-strategy-v1.json', JSON.stringify(content, null, 2)); },
    persist: flush,
  };
}
