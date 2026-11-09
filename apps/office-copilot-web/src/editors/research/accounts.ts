import { createElement as h, useEffect, useRef, type ComponentProps } from 'react';
import { createRoot } from 'react-dom/client';
import { ReactFlow, ReactFlowProvider, Handle, Position, Controls, Background, useNodesState, useNodesInitialized, useReactFlow, useUpdateNodeInternals, MarkerType, type NodeProps, type Node, type Edge, type ReactFlowInstance } from '@xyflow/react';
import { createIcons, icons } from 'lucide';
import { ACCOUNT_GROUPS, ACCOUNTING_STANDARDS, accountId, type AccountsDiagram, type AccountingRule, type FinancialAccount } from './accounts-model';
import '@xyflow/react/dist/style.css';
import './accounts.css';

const esc = (text: string) => text.replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch]!);
type AccountNode = Node<{ account: FinancialAccount; rules: AccountingRule[]; focused: string; selectRule: (id: string) => void }, 'account'>;
function TAccount({ id, data }: NodeProps<AccountNode>) {
  const updateInternals = useUpdateNodeInternals();
  useEffect(() => { updateInternals(id); }, [id, data.rules, updateInternals]);
  const column = (side: 'debits' | 'credits') => h('div', { className: 'account-side' },
    h('strong', null, side === 'debits' ? '借方' : '贷方'),
    ...data.rules.filter(r => r[side].includes(data.account.id)).map(r => h('button', {
      key: r.id, className: `nodrag nopan account-entry ${data.focused === r.id ? 'is-focused' : ''}`,
      onClick: (e: { stopPropagation: () => void }) => { e.stopPropagation(); data.selectRule(r.id); },
      title: r.condition,
    }, r.name, h(Handle, { id: `${side}:${r.id}`, type: side === 'debits' ? 'source' : 'target', position: side === 'debits' ? Position.Left : Position.Right, isConnectable: false }))),
  );
  return h('div', { className: `t-account group-${data.account.group}` },
    h('header', null, h('span', null, ACCOUNT_GROUPS[data.account.group]), h('strong', null, data.account.name)),
    h('div', { className: 'account-columns' }, column('debits'), column('credits')),
    h('footer', null, `正常余额：${data.account.normal === 'debit' ? '借方' : '贷方'}`),
  );
}
const nodeTypes = { account: TAccount };
function AccountsFlow(props: ComponentProps<typeof ReactFlow>) {
  const [nodes, setNodes, onNodesChange] = useNodesState(props.nodes || []);
  const initialized = useNodesInitialized();
  const { fitView } = useReactFlow();
  const fitted = useRef(false);
  useEffect(() => { setNodes(props.nodes || []); }, [props.nodes, setNodes]);
  useEffect(() => {
    if (!initialized || fitted.current) return;
    fitted.current = true;
    const frame = requestAnimationFrame(() => { void fitView({ padding: 0.12 }); });
    return () => { cancelAnimationFrame(frame); fitted.current = false; };
  }, [initialized, fitView]);
  return h(ReactFlow, { ...props, nodes, onNodesChange }, h(Background, { gap: 24, size: 1, color: '#dce3e7' }), h(Controls, { showInteractive: false }));
}

export function mountAccounts(container: HTMLElement, read: () => AccountsDiagram, update: (value: AccountsDiagram) => void) {
  container.innerHTML = `<div class="accounts-workspace"><div class="accounts-bar"><strong data-company></strong><span data-standard></span><span class="accounts-bar-spacer"></span><select data-group aria-label="账户分类"><option value="">全部账户</option>${Object.entries(ACCOUNT_GROUPS).map(([k, v]) => `<option value="${k}">${v}</option>`).join('')}</select><select data-tag aria-label="业务标签"></select><button data-action="account"><i data-lucide="plus"></i>账户</button><button data-action="rule"><i data-lucide="git-pull-request"></i>分录</button><button data-action="layout" title="按类别排列"><i data-lucide="layout-grid"></i></button><button data-action="reset" title="显示全图"><i data-lucide="scan"></i></button></div><div class="accounts-main"><div class="accounts-flow"></div><aside class="accounts-details" hidden></aside></div><div class="accounts-status"><span data-count></span><span>借方 → 分录 → 贷方</span><span>基础记账模板 · 按企业政策调整</span></div></div>`;
  const root = createRoot(container.querySelector('.accounts-flow')!);
  const details = container.querySelector<HTMLElement>('.accounts-details')!;
  const group = container.querySelector<HTMLSelectElement>('[data-group]')!;
  const tags = container.querySelector<HTMLSelectElement>('[data-tag]')!;
  const relationMode = document.createElement('select');
  relationMode.setAttribute('aria-label', '关系类型');
  relationMode.innerHTML = '<option value="entries">借贷关系</option><option value="sequence">记账流程</option>';
  tags.insertAdjacentElement('afterend', relationMode);
  let flow: ReactFlowInstance | undefined;
  let selection: { type: 'account' | 'rule'; id: string } | null = null;
  let disposed = false;
  const commit = (next: AccountsDiagram) => { update(next); draw(); };
  const selectRule = (id: string) => { selection = { type: 'rule', id }; draw(); showDetails(); };
  const selectAccount = (id: string) => { selection = { type: 'account', id }; draw(); showDetails(); };
  function draw() {
    if (disposed) return;
    const model = read();
    container.querySelector('[data-company]')!.textContent = `${model.stock.name} · ${model.stock.ticker_code}`;
    container.querySelector('[data-standard]')!.textContent = ACCOUNTING_STANDARDS[model.standard];
    container.querySelector('[data-count]')!.textContent = `${model.accounts.length} 个账户 · ${model.rules.length} 条记账规则`;
    const tag = tags.value;
    tags.innerHTML = `<option value="">全部业务</option>${[...new Set(model.rules.map(r => r.tag).filter(Boolean))].map(t => `<option value="${esc(t)}">${esc(t)}</option>`).join('')}`;
    tags.value = tag;
    const selectedRule = selection?.type === 'rule' ? model.rules.find(r => r.id === selection!.id) : null;
    const focusedRules = selectedRule ? model.rules.filter(r => r.id === selectedRule.id || (relationMode.value === 'sequence' && (selectedRule.next.includes(r.id) || r.next.includes(selectedRule.id)))) : selection?.type === 'account' ? model.rules.filter(r => [...r.debits, ...r.credits].includes(selection!.id)) : [];
    const relevant = new Set(focusedRules.flatMap(r => [...r.debits, ...r.credits]));
    if (selection?.type === 'account') relevant.add(selection.id);
    const filteredRules = model.rules.filter(r => !tags.value || r.tag === tags.value);
    const visible = model.accounts.filter(a => (!group.value || a.group === group.value) && (!tags.value || filteredRules.some(r => [...r.debits, ...r.credits].includes(a.id))));
    const visibleIds = new Set(visible.map(a => a.id));
    const nodes: Node[] = visible.map(account => ({
      id: account.id, type: 'account', position: account.position,
      selected: selection?.type === 'account' && selection.id === account.id,
      style: { opacity: selection && !relevant.has(account.id) ? 0.25 : 1 },
      data: { account, rules: filteredRules, focused: selectedRule?.id || '', selectRule },
    }));
    const edges: Edge[] = [];
    const displayedRules = selection ? filteredRules.filter(r => focusedRules.some(f => f.id === r.id)) : filteredRules;
    for (const r of displayedRules) {
      const debits = r.debits.filter(id => visibleIds.has(id));
      const credits = r.credits.filter(id => visibleIds.has(id));
      if ((!debits.length || !credits.length) && relationMode.value !== 'sequence') continue;
      const connected = [...debits, ...credits].map(id => model.accounts.find(a => a.id === id)!);
      const x = connected.reduce((n, a) => n + a.position.x + 145, 0) / Math.max(1, connected.length);
      const y = connected.reduce((n, a) => n + a.position.y + 90, 0) / Math.max(1, connected.length);
      const ruleId = `rule:${r.id}`;
      nodes.push({ id: ruleId, position: { x: x + 100, y: y + 100 + displayedRules.indexOf(r) * 22 }, data: { label: r.name }, className: 'account-rule-node', style: { width: 130 }, draggable: false });
      if (relationMode.value === 'entries') {
        for (const id of debits) edges.push({ id: `${r.id}:d:${id}`, source: id, sourceHandle: `debits:${r.id}`, target: ruleId, style: { stroke: '#30847c' }, markerEnd: { type: MarkerType.ArrowClosed } });
        for (const id of credits) edges.push({ id: `${r.id}:c:${id}`, source: ruleId, target: id, targetHandle: `credits:${r.id}`, style: { stroke: '#557eaf' }, markerEnd: { type: MarkerType.ArrowClosed } });
      }
    }
    if (relationMode.value === 'sequence') {
      const ruleNodes = new Set(nodes.map(n => n.id));
      for (const r of displayedRules) for (const next of r.next) {
        if (ruleNodes.has(`rule:${r.id}`) && ruleNodes.has(`rule:${next}`)) edges.push({ id: `${r.id}:next:${next}`, source: `rule:${r.id}`, target: `rule:${next}`, label: '后续', style: { stroke: '#8b7aa4', strokeDasharray: '5 4' }, markerEnd: { type: MarkerType.ArrowClosed } });
      }
    }
    root.render(h(ReactFlowProvider, null, h(AccountsFlow, {
      nodes, edges, nodeTypes, fitView: true, minZoom: 0.15, maxZoom: 1.8, nodesConnectable: false, deleteKeyCode: null,
      onInit: (instance: ReactFlowInstance) => { flow = instance; },
      onNodeClick: (_event, node) => node.id.startsWith('rule:') ? selectRule(node.id.slice(5)) : selectAccount(node.id),
      onPaneClick: () => { selection = null; details.hidden = true; draw(); },
      onNodeDragStop: (_event, node) => { if (node.id.startsWith('rule:')) return; commit({ ...read(), accounts: read().accounts.map(a => a.id === node.id ? { ...a, position: node.position } : a) }); },
      onEdgeClick: (_event, edge) => { const r = displayedRules.find(rule => edge.id.startsWith(`${rule.id}:`)); if (r) selectRule(r.id); },
    })));
  }
  function showDetails() {
    if (!selection) { details.hidden = true; return; }
    const model = read();
    const account = selection.type === 'account' ? model.accounts.find(a => a.id === selection!.id) : null;
    const rule = selection.type === 'rule' ? model.rules.find(r => r.id === selection!.id) : null;
    if (!account && !rule) { details.hidden = true; return; }
    details.hidden = false;
    const field = (label: string, key: string, value: string, textarea = false) => `<label><span>${label}</span>${textarea ? `<textarea name="${key}" rows="3">${esc(value)}</textarea>` : `<input name="${key}" value="${esc(value)}" ${key === 'name' ? 'required' : ''} maxlength="100">`}</label>`;
    const side = (key: 'debits' | 'credits', label: string) => `<fieldset><legend>${label}</legend>${model.accounts.map(a => `<label class="accounts-check"><input type="checkbox" name="${key}" value="${esc(a.id)}" ${rule![key].includes(a.id) ? 'checked' : ''}><span>${esc(a.name)}</span></label>`).join('')}</fieldset>`;
    details.innerHTML = `<header><strong>${account ? '账户定义' : '记账规则'}</strong><button type="button" data-close title="关闭"><i data-lucide="x"></i></button></header><form>${account ? `${field('名称', 'name', account.name)}<label><span>类别</span><select name="group">${Object.entries(ACCOUNT_GROUPS).map(([key, name]) => `<option value="${key}" ${key === account.group ? 'selected' : ''}>${name}</option>`).join('')}</select></label><label><span>正常余额</span><select name="normal"><option value="debit" ${account.normal === 'debit' ? 'selected' : ''}>借方</option><option value="credit" ${account.normal === 'credit' ? 'selected' : ''}>贷方</option></select></label>${field('说明', 'note', account.note, true)}<div class="accounts-related">${model.rules.filter(r => [...r.debits, ...r.credits].includes(account.id)).map(r => `<button type="button" data-rule="${esc(r.id)}">${esc(r.name)}</button>`).join('')}</div>` : `${field('名称', 'name', rule!.name)}${field('业务标签', 'tag', rule!.tag)}${field('确认条件', 'condition', rule!.condition, true)}${field('来源 / 依据', 'source', rule!.source, true)}${side('debits', '借方账户')}${side('credits', '贷方账户')}<fieldset><legend>后续记账规则</legend>${model.rules.filter(r => r.id !== rule!.id).map(r => `<label class="accounts-check"><input type="checkbox" name="next" value="${esc(r.id)}" ${rule!.next.includes(r.id) ? 'checked' : ''}><span>${esc(r.name)}</span></label>`).join('')}</fieldset>`}<p data-error role="alert"></p><div class="accounts-actions"><button type="button" data-delete><i data-lucide="trash-2"></i>删除</button><button class="primary" type="submit">应用修改</button></div></form>`;
    createIcons({ icons });
    details.querySelector('[data-close]')!.addEventListener('click', () => { selection = null; details.hidden = true; draw(); });
    details.querySelectorAll<HTMLElement>('[data-rule]').forEach(b => b.onclick = () => selectRule(b.dataset.rule!));
    details.querySelector('form')!.onsubmit = event => {
      event.preventDefault();
      const data = new FormData(event.currentTarget as HTMLFormElement);
      const text = (key: string) => String(data.get(key) || '').trim();
      if (!text('name')) return;
      const current = read();
      if (account) commit({ ...current, accounts: current.accounts.map(a => a.id === account.id ? { ...a, name: text('name'), group: text('group') as FinancialAccount['group'], normal: text('normal') as FinancialAccount['normal'], note: text('note') } : a) });
      else {
        const debits = data.getAll('debits').map(String), credits = data.getAll('credits').map(String);
        if (!debits.length || !credits.length) { details.querySelector('[data-error]')!.textContent = '借方和贷方各至少选择一个账户。'; return; }
        commit({ ...current, rules: current.rules.map(r => r.id === rule!.id ? { ...r, name: text('name'), condition: text('condition'), tag: text('tag'), source: text('source'), debits, credits, next: data.getAll('next').map(String) } : r) });
      }
      showDetails();
    };
    details.querySelector('[data-delete]')!.addEventListener('click', () => {
      if (!window.confirm(account ? '删除该账户？关联记账规则也会一并删除。' : '删除此记账规则？')) return;
      const current = read();
      const removed = new Set(account ? current.rules.filter(r => [...r.debits, ...r.credits].includes(account.id)).map(r => r.id) : [rule!.id]);
      selection = null; details.hidden = true;
      commit({ ...current, accounts: current.accounts.filter(a => a.id !== account?.id), rules: current.rules.filter(r => !removed.has(r.id)).map(r => ({ ...r, next: r.next.filter(id => !removed.has(id)) })) });
    });
  }
  group.onchange = tags.onchange = relationMode.onchange = () => { selection = null; details.hidden = true; draw(); requestAnimationFrame(() => flow?.fitView({ padding: 0.15 })); };
  container.querySelectorAll<HTMLElement>('[data-action]').forEach(button => button.onclick = () => {
    const model = read();
    const action = button.dataset.action;
    if (action === 'reset') { selection = null; details.hidden = true; group.value = ''; tags.value = ''; draw(); requestAnimationFrame(() => flow?.fitView({ padding: 0.15 })); }
    if (action === 'layout') {
      const rows: Record<string, number> = {};
      commit({ ...model, accounts: model.accounts.map(a => ({ ...a, position: { x: Object.keys(ACCOUNT_GROUPS).indexOf(a.group) * 340, y: (rows[a.group] = (rows[a.group] || 0) + 1) * 300 - 300 } })) });
      requestAnimationFrame(() => flow?.fitView({ padding: 0.15 }));
    }
    if (action === 'account') {
      const id = accountId();
      const center = flow?.screenToFlowPosition({ x: container.getBoundingClientRect().left + container.clientWidth / 2, y: container.getBoundingClientRect().top + 180 }) || { x: 0, y: 0 };
      commit({ ...model, accounts: [...model.accounts, { id, name: '新账户', group: 'asset', normal: 'debit', note: '', position: center }] });
      selectAccount(id);
    }
    if (action === 'rule') {
      const id = accountId();
      commit({ ...model, rules: [...model.rules, { id, name: '新记账规则', condition: '', tag: '', source: '', debits: [], credits: [], next: [] }] });
      selectRule(id);
    }
  });
  createIcons({ icons });
  draw();
  return { refresh: () => { draw(); showDetails(); }, destroy: () => { disposed = true; root.unmount(); container.replaceChildren(); } };
}
