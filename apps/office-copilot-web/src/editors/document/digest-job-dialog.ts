/** 文档「定时任务 / 每日资讯」设置弹窗 */

import { mountThinkingStatus } from '../../ui/thinking-status';

export type DigestSource = {
  kind: 'rss' | 'url' | 'rsshub';
  url?: string;
  route?: string;
  label?: string;
};

export type DigestTopic = {
  name: string;
  sources: DigestSource[];
};

export type DigestJob = {
  document_id?: string;
  enabled: boolean;
  hour: number;
  minute: number;
  title_prefix?: string;
  output_order: 'newest_first' | 'newest_last';
  topics: DigestTopic[];
  last_run_at?: string | null;
  last_status?: string | null;
  last_error?: string;
};

type Hooks = {
  load: () => Promise<DigestJob>;
  save: (job: DigestJob) => Promise<DigestJob>;
  run: (onEvent: (event: Record<string, any>) => void) => Promise<void>;
  reloadDocument?: (documentId?: string) => Promise<void>;
};

const HINT = `资讯源说明：优先填写网站官方 RSS。微博等可用 RSSHub 路由（需在服务端配置 DIGEST_RSSHUB_BASE）。微信公众号 / 小红书无稳定官方接口，请粘贴你已有的第三方转 RSS 地址（如 WeRSS、Feeddd 等），本产品不内置抓取微信客户端。`;

function emptySource(): DigestSource {
  return { kind: 'rss', url: '', label: '' };
}

function emptyTopic(): DigestTopic {
  return { name: '', sources: [emptySource()] };
}

function cloneJob(job: DigestJob): DigestJob {
  return {
    enabled: Boolean(job.enabled),
    hour: Number.isFinite(job.hour) ? job.hour : 8,
    minute: Number.isFinite(job.minute) ? job.minute : 0,
    output_order: job.output_order === 'newest_last' ? 'newest_last' : 'newest_first',
    topics: (job.topics || []).map(topic => ({
      name: topic.name || '',
      sources: (topic.sources || []).map(src => ({
        kind: src.kind === 'url' || src.kind === 'rsshub' ? src.kind : 'rss',
        url: src.url || '',
        route: src.route || '',
        label: src.label || '',
      })),
    })),
    last_run_at: job.last_run_at,
    last_status: job.last_status,
    last_error: job.last_error || '',
  };
}

export function openDigestJobDialog(hooks: Hooks) {
  return new Promise<'saved' | 'closed'>(async resolve => {
    let job = cloneJob(await hooks.load().catch(() => ({
      enabled: false, hour: 8, minute: 0, output_order: 'newest_first' as const, topics: [emptyTopic()],
    })));
    if (!job.topics.length) job.topics = [emptyTopic()];

    const overlay = document.createElement('div');
    overlay.className = 'dialog-overlay digest-job-overlay';
    overlay.innerHTML = `
      <div class="digest-job-dialog" role="dialog" aria-labelledby="digest-job-title">
        <h2 id="digest-job-title">定时任务 · 每日资讯</h2>
        <p class="digest-job-note">标题固定为「每日资讯」+ 执行时的年月日时分。任务绑定当前文档。</p>
        <label class="digest-row"><span>启用</span><input type="checkbox" data-enabled /></label>
        <div class="digest-row digest-time">
          <span>每日执行</span>
          <input type="number" min="0" max="23" data-hour aria-label="小时" />
          <span>:</span>
          <input type="number" min="0" max="59" data-minute aria-label="分钟" />
        </div>
        <label class="digest-row"><span>输出位置</span>
          <select data-order>
            <option value="newest_first">最新在上</option>
            <option value="newest_last">最新在下</option>
          </select>
        </label>
        <div class="digest-topics" data-topics></div>
        <button type="button" data-add-topic>添加子主题</button>
        <p class="digest-job-hint">${HINT}</p>
        <div class="digest-job-status" data-status></div>
        <div class="dialog-actions">
          <button type="button" data-run>立即执行一次</button>
          <button type="button" data-cancel>关闭</button>
          <button type="button" class="primary" data-save>保存</button>
        </div>
      </div>`;

    const topicsEl = overlay.querySelector<HTMLElement>('[data-topics]')!;
    const enabledEl = overlay.querySelector<HTMLInputElement>('[data-enabled]')!;
    const hourEl = overlay.querySelector<HTMLInputElement>('[data-hour]')!;
    const minuteEl = overlay.querySelector<HTMLInputElement>('[data-minute]')!;
    const orderEl = overlay.querySelector<HTMLSelectElement>('[data-order]')!;
    const statusEl = overlay.querySelector<HTMLElement>('[data-status]')!;
    const thinking = mountThinkingStatus(statusEl);
    let busy = false;
    let settled = false;

    const finish = (action: 'saved' | 'closed') => {
      if (settled) return;
      settled = true;
      overlay.remove();
      resolve(action);
    };

    function syncStatus() {
      const parts: string[] = [];
      if (job.last_run_at) parts.push(`上次：${job.last_run_at}`);
      if (job.last_status) parts.push(job.last_status);
      if (job.last_error) parts.push(job.last_error);
      thinking.note(parts.join(' · '));
    }

    function readForm() {
      job.enabled = enabledEl.checked;
      job.hour = Math.min(23, Math.max(0, Number(hourEl.value) || 0));
      job.minute = Math.min(59, Math.max(0, Number(minuteEl.value) || 0));
      job.output_order = orderEl.value === 'newest_last' ? 'newest_last' : 'newest_first';
      const topics: DigestTopic[] = [];
      topicsEl.querySelectorAll<HTMLElement>('[data-topic]').forEach(block => {
        const name = block.querySelector<HTMLInputElement>('[data-topic-name]')!.value.trim();
        const sources: DigestSource[] = [];
        block.querySelectorAll<HTMLElement>('[data-source]').forEach(row => {
          const kind = row.querySelector<HTMLSelectElement>('[data-kind]')!.value as DigestSource['kind'];
          const label = row.querySelector<HTMLInputElement>('[data-label]')!.value.trim();
          const value = row.querySelector<HTMLInputElement>('[data-value]')!.value.trim();
          if (kind === 'rsshub') sources.push({ kind, route: value, label });
          else sources.push({ kind, url: value, label });
        });
        if (name || sources.some(s => s.url || s.route)) topics.push({ name: name || '未命名', sources });
      });
      job.topics = topics;
    }

    function renderTopics() {
      topicsEl.replaceChildren();
      job.topics.forEach((topic, ti) => {
        const block = document.createElement('section');
        block.className = 'digest-topic';
        block.dataset.topic = String(ti);
        block.innerHTML = `
          <div class="digest-topic-head">
            <input data-topic-name placeholder="子主题，如：王者荣耀" maxlength="80" />
            <button type="button" data-remove-topic>删除主题</button>
          </div>
          <div data-sources></div>
          <button type="button" data-add-source>添加资讯来源</button>`;
        block.querySelector<HTMLInputElement>('[data-topic-name]')!.value = topic.name;
        const sourcesEl = block.querySelector<HTMLElement>('[data-sources]')!;
        const paintSources = () => {
          sourcesEl.replaceChildren();
          topic.sources.forEach((src, si) => {
            const row = document.createElement('div');
            row.className = 'digest-source';
            row.dataset.source = String(si);
            row.innerHTML = `
              <select data-kind>
                <option value="rss">RSS</option>
                <option value="url">网址</option>
                <option value="rsshub">RSSHub路由</option>
              </select>
              <input data-value placeholder="https://… 或 /weibo/user/uid" maxlength="2000" />
              <input data-label placeholder="备注（可选）" maxlength="120" />
              <button type="button" data-remove-source>删</button>`;
            row.querySelector<HTMLSelectElement>('[data-kind]')!.value = src.kind;
            row.querySelector<HTMLInputElement>('[data-value]')!.value = src.kind === 'rsshub' ? (src.route || '') : (src.url || '');
            row.querySelector<HTMLInputElement>('[data-label]')!.value = src.label || '';
            row.querySelector('[data-remove-source]')!.addEventListener('click', () => {
              topic.sources.splice(si, 1);
              if (!topic.sources.length) topic.sources.push(emptySource());
              paintSources();
            });
            sourcesEl.append(row);
          });
        };
        paintSources();
        block.querySelector('[data-add-source]')!.addEventListener('click', () => {
          readForm();
          job.topics[ti]?.sources.push(emptySource());
          renderTopics();
        });
        block.querySelector('[data-remove-topic]')!.addEventListener('click', () => {
          readForm();
          job.topics.splice(ti, 1);
          if (!job.topics.length) job.topics.push(emptyTopic());
          renderTopics();
        });
        topicsEl.append(block);
      });
    }

    enabledEl.checked = job.enabled;
    hourEl.value = String(job.hour);
    minuteEl.value = String(job.minute);
    orderEl.value = job.output_order;
    syncStatus();
    renderTopics();

    overlay.querySelector('[data-add-topic]')!.addEventListener('click', () => {
      readForm();
      job.topics.push(emptyTopic());
      renderTopics();
    });
    overlay.querySelector('[data-cancel]')!.addEventListener('click', () => finish('closed'));
    overlay.addEventListener('click', e => { if (e.target === overlay && !busy) finish('closed'); });
    overlay.querySelector('[data-save]')!.addEventListener('click', async () => {
      if (busy) return;
      readForm();
      busy = true;
      try {
        job = cloneJob(await hooks.save(job));
        syncStatus();
        window.dispatchEvent(new CustomEvent('office-saved'));
        finish('saved');
      } catch (e) {
        window.dispatchEvent(new CustomEvent('office-error', { detail: e instanceof Error ? e.message : String(e) }));
      } finally {
        busy = false;
      }
    });
    overlay.querySelector('[data-run]')!.addEventListener('click', async () => {
      if (busy) return;
      readForm();
      busy = true;
      thinking.clear();
      thinking.note('正在获取资讯…');
      let title = '每日资讯';
      let warnings: string[] = [];
      let nextDocumentId = '';
      let archivedName = '';
      try {
        await hooks.save(job);
        await hooks.run(event => {
          if (event.type === 'status' && event.text) {
            if (String(event.text).includes('整理')) thinking.begin();
            else thinking.note(String(event.text));
          }
          if (event.type === 'delta' && event.part === 'reasoning' && event.text) thinking.append(String(event.text));
          if (event.type === 'done') {
            if (event.title) title = String(event.title);
            if (Array.isArray(event.warnings)) warnings = event.warnings.map(String);
            if (event.document_id) nextDocumentId = String(event.document_id);
            if (event.archived_name) archivedName = String(event.archived_name);
          }
        });
        thinking.finish();
        thinking.note(`已生成：${title}${archivedName ? `，已归档为${archivedName}` : ''}${warnings.length ? '（部分来源失败）' : ''}`);
        await hooks.reloadDocument?.(nextDocumentId || undefined);
      } catch (e) {
        thinking.finish();
        thinking.note(e instanceof Error ? e.message : String(e));
      } finally {
        busy = false;
      }
    });

    document.body.append(overlay);
  });
}
