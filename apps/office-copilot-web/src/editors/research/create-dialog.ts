import { portfolioApi, type SecurityHit } from '../../portfolio/api';
import { ACCOUNTING_STANDARDS, createAccountsDiagram, type AccountsDiagram } from './accounts-model';
import './accounts.css';

export function askResearchCreate() {
  return new Promise<{ name: string; visibility: 'project' | 'user'; content: unknown } | null>(resolve => {
    const overlay = document.createElement('div');
    overlay.className = 'dialog-overlay';
    overlay.innerHTML = `<form class="name-dialog research-create-dialog" role="dialog" aria-modal="true" aria-labelledby="research-create-title"><h2 id="research-create-title">新建研究图</h2><div class="research-create-fields"><label class="research-create-row"><span>名称</span><input name="name" required maxlength="80" placeholder="研究图名称" autocomplete="off"></label><label class="research-create-row"><span>图类型</span><select name="type"><option value="financial_accounts">财务账户分析</option><option value="timeline">时间轴</option><option value="fishbone">鱼骨图</option></select></label><div class="research-create-row" data-financial><span>股票</span><div class="research-stock-input"><input name="stock" aria-label="搜索股票" placeholder="输入股票代码或公司名称" autocomplete="off" required><div class="research-stock-results" data-results hidden></div></div></div><label class="research-create-row" data-financial><span>会计准则</span><select name="standard">${Object.entries(ACCOUNTING_STANDARDS).map(([key, label]) => `<option value="${key}">${label}</option>`).join('')}</select></label><div class="research-create-row"><span>可见范围</span><div class="research-create-visibility"><label><input type="radio" name="visibility" value="project" checked>项目可见</label><label><input type="radio" name="visibility" value="user">我可见</label></div></div></div><p data-error role="alert" hidden></p><div class="dialog-actions"><button type="button" data-cancel>取消</button><button type="submit" class="primary">创建</button></div></form>`;
    const form = overlay.querySelector('form')!;
    const input = form.elements.namedItem('stock') as HTMLInputElement;
    const type = form.elements.namedItem('type') as HTMLSelectElement;
    const results = overlay.querySelector<HTMLElement>('[data-results]')!;
    const error = overlay.querySelector<HTMLElement>('[data-error]')!;
    let selected: SecurityHit | null = null;
    let generation = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const finish = (value: Parameters<typeof resolve>[0]) => { generation++; clearTimeout(timer); document.removeEventListener('keydown', keydown); overlay.remove(); resolve(value); };
    const keydown = (event: KeyboardEvent) => { if (event.key === 'Escape') finish(null); };
    document.addEventListener('keydown', keydown);
    overlay.querySelector('[data-cancel]')!.addEventListener('click', () => finish(null));
    overlay.addEventListener('click', e => { if (e.target === overlay) finish(null); });
    type.onchange = () => {
      const financial = type.value === 'financial_accounts';
      overlay.querySelectorAll<HTMLElement>('[data-financial]').forEach(el => el.hidden = !financial);
      input.required = financial; results.hidden = true; error.hidden = true;
    };
    input.oninput = () => {
      selected = null; error.hidden = true; clearTimeout(timer);
      const token = ++generation;
      const query = input.value.trim();
      results.replaceChildren(); results.hidden = !query;
      if (!query) return;
      results.textContent = '搜索中…';
      timer = setTimeout(async () => {
        try {
          const hits = await portfolioApi.searchSecurities(query);
          if (token !== generation || !overlay.isConnected) return;
          results.replaceChildren();
          const stocks = hits.filter(item => item.security_type !== '基金');
          if (!stocks.length) results.textContent = '未找到股票，请更换代码或名称。';
          for (const item of stocks.slice(0, 20)) {
            const button = document.createElement('button'); button.type = 'button';
            button.textContent = `${item.name} · ${item.ticker_code}`;
            button.onclick = () => { selected = item; input.value = `${item.name}（${item.ticker_code}）`; results.hidden = true; };
            results.append(button);
          }
        } catch { if (token === generation) results.textContent = '股票搜索失败，请检查远程服务连接后重试。'; }
      }, 250);
    };
    form.onsubmit = event => {
      event.preventDefault();
      const data = new FormData(form);
      const name = String(data.get('name') || '').trim();
      if (!name) return;
      if (type.value === 'financial_accounts' && !selected) { error.textContent = '请从搜索结果中选择股票。'; error.hidden = false; input.focus(); return; }
      const content = type.value === 'financial_accounts'
        ? createAccountsDiagram(name, selected!, String(data.get('standard')) as AccountsDiagram['standard'])
        : { format: 'invp-research-diagram-v1', type: type.value, title: name, ...(type.value === 'timeline' ? { events: [] } : { problem: name, categories: [] }) };
      finish({ name, visibility: data.get('visibility') === 'user' ? 'user' : 'project', content });
    };
    document.body.append(overlay);
    (form.elements.namedItem('name') as HTMLInputElement).focus();
  });
}
