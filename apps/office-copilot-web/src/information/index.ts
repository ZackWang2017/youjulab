import { createIcons, icons } from 'lucide';
import { mountThinkingStatus } from '../ui/thinking-status';
import {
  informationApi, informationSkills,
  type InformationGroup, type InformationItem, type InformationReview, type InformationView, type TodaySummary,
} from './api';
import { confirmClear, openAnalyzeDialog, openFetchFailures, openReviewDetail, openWereadLoginDialog } from './dialogs';
import './style.css';

const esc = (value: string) => value.replace(/[&<>'"]/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[char]!));

export interface InformationController {
  show(groupId: number | null, view: InformationView): Promise<void>;
  refresh(): Promise<void>;
  destroy(): void;
}

export function mountInformation(
  section: HTMLElement,
  onError: (error: unknown) => void,
  handlers?: { onOpenFavorites?: () => void },
): InformationController {
  section.innerHTML = '<div class="info-shell" data-shell></div>';
  const shell = section.querySelector<HTMLElement>('[data-shell]')!;
  let groupId: number | null = null;
  let view: InformationView = 'today';
  let group: InformationGroup | null = null;
  let items: InformationItem[] = [];
  let reviews: InformationReview[] = [];
  let summary: TodaySummary | null = null;
  let loading = false;
  let notice = '';
  let fetchNotice: { success: number; failed: string[] } | null = null;
  let seq = 0;

  async function load() {
    if (groupId === null) { render(); return; }
    const current = ++seq;
    loading = true;
    render();
    try {
      if (view === 'today') {
        const listed = await informationApi.items(groupId, true);
        group = listed.group;
        items = listed.items;
      } else {
        const [s, listed, rows] = await Promise.all([
          informationApi.summary(groupId),
          informationApi.items(groupId, true, { review: true }),
          informationApi.reviews(groupId),
        ]);
        summary = s;
        group = listed.group;
        items = listed.items;
        reviews = rows;
      }
    } catch (error) {
      if (current === seq) onError(error);
    } finally {
      if (current === seq) { loading = false; render(); }
    }
  }

  function render() {
    if (groupId === null) {
      shell.innerHTML = `<div class="info-empty"><i data-lucide="newspaper"></i><h2>还没有资讯组</h2><p>在上方下拉菜单里新建一个资讯组。系统会默认保留「收藏夹」。</p></div>`;
      createIcons({ icons });
      return;
    }
    shell.innerHTML = view === 'today' ? todayHtml() : reviewHtml();
    createIcons({ icons });
    bind();
  }

  function todayHtml() {
    const favorites = group?.kind === 'favorites';
    const head = `<header class="info-head">
      <div>
        <h1>${esc(group?.name || (favorites ? '收藏夹' : '今日资讯'))}</h1>
        ${notice || fetchNotice ? `<p class="info-notice">${noticeMarkup()}</p>` : ''}
      </div>
      <div class="info-head-actions">
        <button type="button" data-clear ${loading ? 'disabled' : ''}><i data-lucide="eraser"></i>清理历史信息</button>
        ${group?.kind === 'wechat' ? `<button type="button" data-weread-login ${loading ? 'disabled' : ''}><i data-lucide="qr-code"></i>登录微信读书</button>` : ''}
        ${favorites ? '' : `<button type="button" class="primary" data-fetch ${loading ? 'disabled' : ''}><i data-lucide="download" class="${loading ? 'is-spinning' : ''}"></i>获取今日信息</button>`}
      </div>
    </header>`;
    const feed = items.filter(item => !item.hidden && (favorites || !item.favorited));
    if (loading && !feed.length) return `${head}<div class="info-state"><p>正在加载资讯…</p></div>`;
    if (!feed.length) return `${head}<div class="info-state"><p>${favorites ? '还没有收藏。' : '今天还没有新资讯，点击「获取今日信息」。'}</p></div>`;
    return `${head}<div class="info-body"><div class="info-feed">${feed.map(itemCard).join('')}</div></div>`;
  }

  function itemCard(item: InformationItem) {
    const source = group?.kind === 'favorites'
      ? (item.favorite_from_name || item.source_name)
      : item.source_name;
    const date = (item.published_at || item.fetched_date || '').slice(0, 16);
    return `<article class="info-card">
      <div>
        <h2 class="info-card-title">${item.url ? `<a href="${esc(item.url)}" target="_blank" rel="noreferrer">${esc(item.title)}</a>` : esc(item.title)}</h2>
        <div class="info-card-meta">
          <span class="info-source"><i data-lucide="${group?.kind === 'favorites' ? 'bookmark' : sourceIcon(item.source_kind)}"></i>【${esc(source)}】</span>
          <span>${esc(date || '日期未知')}</span>
          ${item.url ? `<a class="info-link" href="${esc(item.url)}" target="_blank" rel="noreferrer">原文链接</a>` : ''}
        </div>
        <p class="info-impact">${esc(item.impact || '从标题尚难判断对业绩或财务指标的影响')}</p>
      </div>
      <div class="info-card-actions">
        <button type="button" data-fav="${item.id}" class="${item.favorited ? 'info-favorited' : ''}"><i data-lucide="${item.favorited ? 'bookmark-check' : 'bookmark'}"></i>${item.favorited ? '已收藏' : '收藏'}</button>
        <button type="button" data-hide="${item.id}"><i data-lucide="eye-off"></i>隐藏</button>
        <button type="button" data-analyze="${item.id}"><i data-lucide="sparkles"></i>分析</button>
      </div>
    </article>`;
  }

  function reviewCiteCount() {
    const today = summary?.date;
    const current = reviews.find(row => row.review_date === today);
    if (current?.item_refs?.length) return current.item_refs.length;
    return items.length;
  }

  function reviewHtml() {
    const count = reviewCiteCount();
    const citeRow = `<p class="info-review-cites">今日点评引用 ${count} 条</p>`;
    const head = `<header class="info-head is-review">
      <div class="info-head-title"><h1>深入点评</h1>${citeRow}</div>
      <div class="info-head-actions">
        <div class="info-review-action">
          <button type="button" class="primary" data-review ${loading ? 'disabled' : ''}><i data-lucide="scan-search"></i>深入点评</button>
        </div>
      </div>
      <div data-review-thinking hidden></div>
    </header>`;
    if (loading && !summary) return `${head}<div class="info-state"><p>正在加载点评…</p></div>`;
    const list = reviews.length
      ? `<div class="info-review-list">${reviews.map(row => `<button type="button" class="info-review-row" data-review-id="${row.id}"><div><h3>${esc(row.review_date)}</h3><p>引用 ${row.item_refs?.length ?? 0} 条${row.sentiment ? ` · ${esc(row.sentiment)}` : ''}</p></div><i data-lucide="chevron-right"></i></button>`).join('')}</div>`
      : '<div class="info-state"><p>还没有点评。获取今日资讯后，点击「深入点评」。</p></div>';
    return `${head}<div class="info-body">${list}</div>`;
  }

  function noticeMarkup() {
    if (!fetchNotice) {
      if (notice.includes('微信读书')) {
        return `${esc(notice)} <button type="button" class="info-fail-link" data-weread-login>扫码登录</button>`;
      }
      return esc(notice);
    }
    const fail = fetchNotice.failed.length
      ? `<button type="button" class="info-fail-link" data-fail-detail>失败 ${fetchNotice.failed.length} 条</button>`
      : '失败 0 条';
    return `本次成功 ${fetchNotice.success} 条，${fail}`;
  }

  function bind() {
    shell.querySelector<HTMLButtonElement>('[data-fetch]')?.addEventListener('click', () => void runFetch());
    shell.querySelectorAll<HTMLButtonElement>('[data-weread-login]').forEach(button => {
      button.addEventListener('click', () => void runWereadLogin());
    });
    shell.querySelector<HTMLButtonElement>('[data-clear]')?.addEventListener('click', () => void runClear());
    shell.querySelector<HTMLButtonElement>('[data-review]')?.addEventListener('click', () => void runReview());
    shell.querySelector<HTMLButtonElement>('[data-fail-detail]')?.addEventListener('click', () => openFetchFailures(fetchNotice?.failed || []));
    shell.querySelectorAll<HTMLButtonElement>('[data-fav]').forEach(button => {
      button.addEventListener('click', () => void runFavorite(Number(button.dataset.fav)));
    });
    shell.querySelectorAll<HTMLButtonElement>('[data-hide]').forEach(button => {
      button.addEventListener('click', () => void runHide(Number(button.dataset.hide)));
    });
    shell.querySelectorAll<HTMLButtonElement>('[data-analyze]').forEach(button => {
      button.addEventListener('click', () => void runAnalyze(Number(button.dataset.analyze)));
    });
    shell.querySelectorAll<HTMLButtonElement>('[data-review-id]').forEach(button => {
      button.addEventListener('click', () => {
        const review = reviews.find(item => item.id === Number(button.dataset.reviewId));
        if (review) openReviewDetail(review);
      });
    });
  }

  async function runWereadLogin(thenFetch = false) {
    const ok = await openWereadLoginDialog();
    if (ok && thenFetch) await runFetch(true);
    else if (ok) {
      notice = '微信读书已登录，可以获取今日信息。';
      fetchNotice = null;
      render();
    }
  }

  async function runFetch(retried = false) {
    if (!groupId) return;
    loading = true;
    render();
    let needLogin = false;
    try {
      const result = await informationApi.fetchToday(groupId);
      if (result.login_required) {
        fetchNotice = null;
        notice = '微信读书未登录或已过期。';
        needLogin = !retried;
        return;
      }
      notice = '';
      fetchNotice = { success: result.inserted.length, failed: result.failed };
      if (result.inserted.length) {
        try {
          await informationSkills.impact({ group_id: groupId, item_ids: result.inserted.map(item => item.id) }, () => undefined);
        } catch { /* 初步判断失败时仍展示启发式 impact */ }
      }
    } catch (error) {
      fetchNotice = null;
      notice = error instanceof Error ? error.message : String(error);
      onError(error);
    } finally {
      await load();
    }
    if (needLogin) await runWereadLogin(true);
  }

  async function runClear() {
    if (!await confirmClear()) return;
    try {
      await informationApi.clear();
      await load();
    } catch (error) {
      onError(error);
    }
  }

  async function runFavorite(id: number) {
    const item = items.find(row => row.id === id);
    if (!item) return;
    const next = !item.favorited;
    try {
      await informationApi.favorite(id, next);
      if (next && group?.kind !== 'favorites' && handlers?.onOpenFavorites) {
        handlers.onOpenFavorites();
        return;
      }
      await load();
    } catch (error) {
      onError(error);
    }
  }

  async function runHide(id: number) {
    try {
      await informationApi.hide(id);
      await load();
    } catch (error) {
      onError(error);
    }
  }

  async function runAnalyze(id: number) {
    const item = items.find(row => row.id === id);
    if (!item) return;
    const updated = await openAnalyzeDialog(item);
    if (updated) await load();
  }

  async function runReview() {
    if (!groupId) return;
    const host = shell.querySelector<HTMLElement>('[data-review-thinking]');
    if (!host) return;
    host.hidden = false;
    const thinking = mountThinkingStatus(host);
    thinking.begin();
    try {
      await informationSkills.review({ group_id: groupId }, event => {
        if (event.type === 'thinking' && event.text) thinking.append(String(event.text));
        if (event.type === 'done') {
          thinking.finish();
          if (event.review) openReviewDetail(event.review);
        }
      });
      await load();
    } catch (error) {
      thinking.finish();
      onError(error);
    }
  }

  return {
    show: async (id, nextView) => {
      if (id !== groupId) { notice = ''; fetchNotice = null; }
      groupId = id;
      view = nextView;
      await load();
    },
    refresh: load,
    destroy: () => { section.replaceChildren(); },
  };
}

function sourceIcon(kind: string) {
  if (kind === 'wechat') return 'message-circle';
  if (kind === 'finance_api') return 'globe';
  if (kind === 'finance_rss') return 'rss';
  if (kind === 'holdings_announce') return 'landmark';
  return 'newspaper';
}
