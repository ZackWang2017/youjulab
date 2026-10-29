import { createIcons, icons } from 'lucide';
import { mountThinkingStatus } from '../ui/thinking-status';
import {
  informationApi, informationSkills, KIND_LABEL,
  type Adapter, type HoldingPick, type InformationGroup, type InformationItem, type InformationKind, type InformationReview,
} from './api';

const esc = (value: string) => value.replace(/[&<>'"]/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[char]!));
const linkify = (value: string) => esc(value).replace(/https?:\/\/[^\s<]+/g, url => `<a href="${url}" target="_blank" rel="noreferrer">${url}</a>`);

function overlay(html: string) {
  const el = document.createElement('div');
  el.className = 'dialog-overlay';
  el.innerHTML = html;
  document.body.append(el);
  createIcons({ icons });
  return el;
}

export async function openGroupDialog(existing?: InformationGroup): Promise<InformationGroup | null> {
  const kinds: InformationKind[] = ['wechat', 'finance_api', 'finance_rss', 'holdings_announce'];
  const el = overlay(`<form class="name-dialog info-dialog info-dialog-group">
    <h2>${existing ? '编辑资讯组' : '新建资讯组'}</h2>
    <label class="field-label" for="info-name">名称</label>
    <input id="info-name" required maxlength="80" value="${esc(existing?.name || '')}" placeholder="如：持仓公告、财经快讯">
    <fieldset class="info-type-grid"><legend class="field-label">类型</legend>
      ${kinds.map(kind => `<label><input type="radio" name="kind" value="${kind}" ${(!existing && kind === 'finance_api') || existing?.kind === kind ? 'checked' : ''} ${existing ? 'disabled' : ''}><span>${KIND_LABEL[kind]}</span></label>`).join('')}
    </fieldset>
    <div data-fields></div>
    <p class="info-dialog-error" data-error hidden></p>
    <div class="dialog-actions"><button type="button" data-cancel>取消</button><button class="primary" type="submit">保存</button></div>
  </form>`);
  const fields = el.querySelector<HTMLElement>('[data-fields]')!;
  const error = el.querySelector<HTMLElement>('[data-error]')!;
  const nameInput = el.querySelector<HTMLInputElement>('#info-name')!;
  let adapters: Adapter[] = [];
  let holdings: HoldingPick[] = [];
  let kind = (existing?.kind && existing.kind !== 'favorites' ? existing.kind : 'finance_api') as InformationKind;

  const showError = (text: string) => { error.hidden = !text; error.textContent = text; };

  async function renderFields() {
    if (kind === 'wechat') {
      fields.innerHTML = `<div class="info-fields-body"><div class="info-weread" data-weread><p>正在检查微信读书登录…</p></div></div>`;
      createIcons({ icons });
      await bindWeread(fields.querySelector('[data-weread]')!);
      return;
    }
    if (kind === 'holdings_announce') {
      holdings = await informationApi.holdings().catch(() => []);
      const selected = new Map((existing?.config.holdings || []).map(item => [item.ticker_code, item.adapter]));
      const body = holdings.length
        ? `<div class="info-holding-list">${holdings.map(item => {
          const options = (item.adapters || []).map(adapter => `<option value="${esc(adapter.id)}" ${ (selected.get(item.ticker_code) || item.adapter) === adapter.id ? 'selected' : ''}>${esc(adapter.name)}</option>`).join('');
          return `<div class="info-holding-row"><label class="info-holding-pick"><input type="checkbox" value="${esc(item.ticker_code)}" ${selected.size ? (selected.has(item.ticker_code) ? 'checked' : '') : 'checked'}><span class="info-holding-name">${esc(item.name)} <small>${esc(item.ticker_code)}</small></span></label><label class="info-holding-adapter"><select data-ticker="${esc(item.ticker_code)}" aria-label="${esc(item.name)} 适配器">${options}</select></label></div>`;
        }).join('')}</div>`
        : `<p class="portfolio-hint">还没有持仓股票。请先在持仓里记录买入。</p>`;
      fields.innerHTML = `<div class="info-fields-body">${body}</div>`;
      createIcons({ icons });
      return;
    }
    adapters = await informationApi.adapters(kind).catch(() => []);
    const picked = new Set(existing?.config.adapters || []);
    fields.innerHTML = `<div class="info-fields-body"><div class="info-adapter-list">${adapters.map(item => `<label><input type="checkbox" value="${esc(item.id)}" ${picked.size ? (picked.has(item.id) ? 'checked' : '') : ''}><span>${esc(item.name)}</span></label>`).join('')}</div></div>${keywordField(existing?.config.keywords || '')}`;
    bindKeyword(fields, kind);
    createIcons({ icons });
  }

  el.querySelectorAll<HTMLInputElement>('input[name="kind"]').forEach(input => {
    input.onchange = () => { kind = input.value as InformationKind; void renderFields(); };
  });
  await renderFields();

  return new Promise(resolve => {
    const finish = (value: InformationGroup | null) => { el.remove(); resolve(value); };
    el.querySelector('[data-cancel]')!.addEventListener('click', () => finish(null));
    el.addEventListener('click', e => { if (e.target === el) finish(null); });
    el.querySelector('form')!.addEventListener('submit', async e => {
      e.preventDefault();
      const name = nameInput.value.trim();
      if (!name) return;
      try {
        const config = collectConfig(kind, fields, holdings);
        const saved = existing
          ? await informationApi.update(existing.id, { name, config })
          : await informationApi.create({ name, kind, config });
        finish(saved);
      } catch (err) {
        showError(err instanceof Error ? err.message : String(err));
      }
    });
  });
}

function keywordField(value: string) {
  return `<label class="field-label" for="info-keywords">关键字</label>
    <div class="info-keyword-row"><input id="info-keywords" value="${esc(value)}" placeholder="公司名、代码或主题，空格或逗号分隔"><button type="button" class="info-ai-btn" data-ai-keywords title="用 AI 生成关键字"><i data-lucide="sparkles"></i></button></div>`;
}

function bindKeyword(root: HTMLElement, kind: string) {
  root.querySelector<HTMLButtonElement>('[data-ai-keywords]')?.addEventListener('click', async () => {
    const input = root.querySelector<HTMLInputElement>('#info-keywords')!;
    const keywords = await openKeywordDialog(kind);
    if (keywords) input.value = keywords;
  });
}

function collectConfig(kind: InformationKind, fields: HTMLElement, holdings: HoldingPick[]) {
  if (kind === 'wechat') return {};
  const keywords = fields.querySelector<HTMLInputElement>('#info-keywords')?.value.trim() || '';
  if (kind === 'holdings_announce') {
    const picked = Array.from(fields.querySelectorAll<HTMLInputElement>('.info-holding-row input:checked')).map(input => {
      const ticker = input.value;
      const holding = holdings.find(item => item.ticker_code === ticker);
      const adapter = fields.querySelector<HTMLSelectElement>(`select[data-ticker="${ticker}"]`)?.value || holding?.adapter || '';
      return { ticker_code: ticker, name: holding?.name || ticker, market_code: holding?.market_code || '', adapter };
    });
    return { holdings: picked };
  }
  const adapters = Array.from(fields.querySelectorAll<HTMLInputElement>('.info-adapter-list input:checked')).map(input => input.value);
  return { adapters, keywords };
}

export function openWereadLoginDialog(): Promise<boolean> {
  const el = overlay(`<div class="weread-login-dialog" role="dialog" aria-labelledby="weread-login-title">
    <header>
      <div>
        <h2 id="weread-login-title">登录微信读书</h2>
        <p data-hint>获取公众号资讯需要先扫码登录。</p>
      </div>
      <button type="button" data-cancel title="关闭" aria-label="关闭">×</button>
    </header>
    <div class="weread-qr-wrap" data-weread><p>正在检查登录状态…</p></div>
    <footer><button type="button" data-cancel>关闭</button></footer>
  </div>`);
  const host = el.querySelector<HTMLElement>('[data-weread]')!;
  return new Promise(resolve => {
    let settled = false;
    const finish = (ok: boolean) => {
      if (settled) return;
      settled = true;
      el.remove();
      resolve(ok);
    };
    el.querySelectorAll('[data-cancel]').forEach(button => button.addEventListener('click', () => finish(false)));
    el.addEventListener('click', e => { if (e.target === el) finish(false); });
    void bindWeread(host).then(async ok => {
      if (!ok) return;
      await new Promise(resolve => setTimeout(resolve, 600));
      finish(true);
    });
  });
}

async function bindWeread(host: HTMLElement): Promise<boolean> {
  const paint = (session: { status: string; qr_data_url: string; error: string }) => {
    if (session.status === 'authenticated') {
      host.innerHTML = '<p>微信读书已登录。获取时将拉取书架公众号的今日或最新一条资讯。</p>';
      return;
    }
    host.innerHTML = `${session.qr_data_url ? `<img alt="微信读书登录二维码" src="${session.qr_data_url}">` : '<p>正在生成二维码…</p>'}<p>${esc(session.error || '请用微信扫描二维码登录微信读书。')}</p>`;
  };
  try {
    let session = await informationApi.wereadLogin();
    paint(session);
    if (session.status === 'authenticated') return true;
    if (!session.session_id) return false;
    const started = Date.now();
    while (Date.now() - started < 5 * 60 * 1000 && host.isConnected) {
      await new Promise(resolve => setTimeout(resolve, 2000));
      session = await informationApi.wereadLoginStatus(session.session_id);
      paint(session);
      if (session.status === 'authenticated') return true;
      if (session.status === 'failed' || session.status === 'expired') return false;
    }
  } catch (err) {
    host.innerHTML = `<p>${esc(err instanceof Error ? err.message : String(err))}</p>`;
  }
  return false;
}

export function openKeywordDialog(kind: string): Promise<string | null> {
  const el = overlay(`<form class="name-dialog info-keyword-dialog">
    <h2>AI 生成关键字</h2>
    <label class="field-label" for="info-keyword-desc">描述你想跟踪的主题</label>
    <textarea id="info-keyword-desc" required placeholder="例如：关注腾讯和美团的回购、分红与中报"></textarea>
    <div data-refine hidden>
      <label class="field-label" for="info-keyword-draft">当前关键字</label>
      <textarea id="info-keyword-draft" class="info-keyword-draft" placeholder="生成后会显示在这里，也可直接改"></textarea>
      <label class="field-label" for="info-keyword-revision">继续调整</label>
      <textarea id="info-keyword-revision" class="info-keyword-revision" placeholder="例如：再加上美团，去掉回购"></textarea>
    </div>
    <div data-thinking></div>
    <p class="info-dialog-error" data-error hidden></p>
    <div class="dialog-actions">
      <button type="button" data-cancel>取消</button>
      <button type="submit" data-generate>生成</button>
      <button class="primary" type="button" data-insert disabled>填入</button>
    </div>
  </form>`);
  const thinking = mountThinkingStatus(el.querySelector<HTMLElement>('[data-thinking]')!);
  const error = el.querySelector<HTMLElement>('[data-error]')!;
  const description = el.querySelector<HTMLTextAreaElement>('#info-keyword-desc')!;
  const refine = el.querySelector<HTMLElement>('[data-refine]')!;
  const draft = el.querySelector<HTMLTextAreaElement>('#info-keyword-draft')!;
  const revision = el.querySelector<HTMLTextAreaElement>('#info-keyword-revision')!;
  const generateBtn = el.querySelector<HTMLButtonElement>('[data-generate]')!;
  const insertBtn = el.querySelector<HTMLButtonElement>('[data-insert]')!;
  let busy = false;

  const showDraft = (keywords: string) => {
    draft.value = keywords;
    refine.hidden = !keywords;
    insertBtn.disabled = !keywords.trim();
    generateBtn.textContent = keywords ? '调整' : '生成';
  };

  return new Promise(resolve => {
    const finish = (value: string | null) => { el.remove(); resolve(value); };
    el.querySelector('[data-cancel]')!.addEventListener('click', () => finish(null));
    el.addEventListener('click', e => { if (e.target === el) finish(null); });
    draft.addEventListener('input', () => { insertBtn.disabled = !draft.value.trim(); });
    insertBtn.addEventListener('click', () => {
      const keywords = draft.value.trim();
      if (keywords) finish(keywords);
    });
    el.querySelector('form')!.addEventListener('submit', async e => {
      e.preventDefault();
      const text = description.value.trim();
      if (!text || busy) return;
      const current = draft.value.trim();
      const note = revision.value.trim();
      busy = true;
      generateBtn.disabled = true;
      insertBtn.disabled = true;
      error.hidden = true;
      thinking.clear();
      thinking.begin();
      try {
        await informationSkills.keywords({
          kind,
          description: text,
          extra: current || note ? { keywords: current, current_keywords: current, revision: note } : undefined,
        }, event => {
          if (event.type === 'thinking' && event.text) thinking.append(String(event.text));
          if (event.type === 'done') {
            const keywords = String(event.keywords || '');
            thinking.finish();
            thinking.note(keywords ? `已生成：${keywords}` : '已完成');
            showDraft(keywords);
            revision.value = '';
          }
        });
      } catch (err) {
        thinking.finish();
        error.hidden = false;
        error.textContent = err instanceof Error ? err.message : String(err);
        insertBtn.disabled = !draft.value.trim();
      } finally {
        busy = false;
        generateBtn.disabled = false;
      }
    });
    description.focus();
  });
}

export function openAnalyzeDialog(item: InformationItem): Promise<InformationItem | null> {
  const el = overlay(`<div class="name-dialog info-dialog" role="dialog">
    <h2>分析 · ${esc(item.title)}</h2>
    <div data-thinking></div>
    <div class="info-analyze-body" data-body></div>
    <p class="info-dialog-error" data-error hidden></p>
    <div class="dialog-actions"><button type="button" data-cancel>关闭</button></div>
  </div>`);
  const thinking = mountThinkingStatus(el.querySelector<HTMLElement>('[data-thinking]')!);
  const body = el.querySelector<HTMLElement>('[data-body]')!;
  const error = el.querySelector<HTMLElement>('[data-error]')!;
  const paint = (analysis: NonNullable<InformationItem['analysis']>) => {
    body.innerHTML = `<section><h3>摘要</h3><p>${esc(analysis.summary || '—')}</p></section>
      <section><h3>业绩影响</h3><p>${esc(analysis.performance || '—')}</p></section>
      <section><h3>财务指标影响</h3><p>${esc(analysis.financial || '—')}</p></section>`;
  };
  if (item.analysis) paint(item.analysis);
  return new Promise(resolve => {
    let latest = item;
    const finish = () => { el.remove(); resolve(latest); };
    el.querySelector('[data-cancel]')!.addEventListener('click', finish);
    el.addEventListener('click', e => { if (e.target === el) finish(); });
    thinking.begin();
    informationSkills.analyze({ item_id: item.id }, event => {
      if (event.type === 'thinking' && event.text) thinking.append(String(event.text));
      if (event.type === 'done') {
        thinking.finish();
        const analysis = event.analysis || event.item?.analysis;
        if (analysis) {
          latest = event.item || { ...item, analysis };
          paint(analysis);
        }
      }
    }).catch(err => {
      thinking.finish();
      error.hidden = false;
      error.textContent = err instanceof Error ? err.message : String(err);
    });
  });
}

export function openFetchFailures(failed: string[]) {
  const rows = failed.length
    ? `<ul class="info-fail-list">${failed.map(item => `<li>${linkify(item)}</li>`).join('')}</ul>`
    : '<p>没有失败明细。</p>';
  const el = overlay(`<div class="name-dialog info-dialog" role="dialog">
    <h2>获取失败</h2>
    ${rows}
    <div class="dialog-actions"><button type="button" data-cancel>关闭</button></div>
  </div>`);
  const close = () => el.remove();
  el.querySelector('[data-cancel]')!.addEventListener('click', close);
  el.addEventListener('click', e => { if (e.target === el) close(); });
}

export function openReviewDetail(review: InformationReview) {
  const el = overlay(`<div class="name-dialog info-dialog" role="dialog">
    <h2>${esc(review.review_date)} 深入点评</h2>
    ${review.item_refs?.length ? `<ul class="info-fail-list info-review-cites">${review.item_refs.map(item => `<li><span class="info-cite-sent ${item.sentiment === '利好' ? 'is-up' : item.sentiment === '利空' ? 'is-down' : ''}">${esc(item.sentiment)}</span>${item.url ? `<a href="${esc(item.url)}" target="_blank" rel="noreferrer">${esc(item.title)}</a>` : esc(item.title)}</li>`).join('')}</ul>` : ''}
    <div class="info-review-detail">${esc(review.content || '暂无内容')}</div>
    <div class="dialog-actions"><button type="button" data-cancel>关闭</button></div>
  </div>`);
  const close = () => el.remove();
  el.querySelector('[data-cancel]')!.addEventListener('click', close);
  el.addEventListener('click', e => { if (e.target === el) close(); });
}

export async function confirmClear() {
  return new Promise<boolean>(resolve => {
    const el = overlay(`<form class="name-dialog"><h2>清理历史信息</h2><p>将删除非今日资讯。今日条目会保留，收藏夹中的条目也会保留。</p><div class="dialog-actions"><button type="button" data-cancel>取消</button><button class="primary" type="submit">清空</button></div></form>`);
    const finish = (ok: boolean) => { el.remove(); resolve(ok); };
    el.querySelector('[data-cancel]')!.addEventListener('click', () => finish(false));
    el.addEventListener('click', e => { if (e.target === el) finish(false); });
    el.querySelector('form')!.addEventListener('submit', e => { e.preventDefault(); finish(true); });
  });
}
