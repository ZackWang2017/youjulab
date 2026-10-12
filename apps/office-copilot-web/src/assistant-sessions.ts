import { createIcons, icons } from 'lucide';
import { office } from './office-client/client';
import { askText } from './workspace/nav';
import type { Context } from './context/types';

export type SessionTurn = { role: 'user' | 'assistant'; content: string };

export type AssistantSession = {
  id: string;
  document_id: string;
  title: string;
  updated_at?: string;
  created_at?: string;
  turn_count?: number;
  anchor?: Context;
  turns?: SessionTurn[];
  summary?: string;
};

export type SessionController = {
  currentId: () => string | null;
  summary: () => string;
  refreshList: () => Promise<void>;
  startNew: (opts?: { resetUi?: boolean }) => Promise<string>;
  load: (sessionId: string, opts?: { notify?: boolean }) => Promise<AssistantSession>;
  save: (payload: {
    anchor?: Context;
    turns: SessionTurn[];
    title?: string;
    summary?: string;
  }) => Promise<void>;
  rename: (title: string) => Promise<void>;
  remove: (sessionId: string) => Promise<void>;
};

function lastSessionKey(assetId: string) {
  return `office-assistant-session:${assetId}`;
}

function rememberSession(assetId: string, sessionId: string) {
  try { localStorage.setItem(lastSessionKey(assetId), sessionId); } catch { /* ignore */ }
}

function recalledSession(assetId: string) {
  try {
    return localStorage.getItem(lastSessionKey(assetId))
      || localStorage.getItem(`office-sheet-session:${assetId}`);
  } catch { return null; }
}

function formatWhen(iso?: string) {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function mountAssistantSessions(
  root: HTMLElement,
  getAssetId: () => string | null,
  hooks: {
    onLoad: (session: AssistantSession) => void;
    onNew: () => void;
    onRefreshAnchor: () => Promise<Context>;
    currentTurns: () => SessionTurn[];
    isStreaming?: () => boolean;
  },
): SessionController {
  const select = root.querySelector<HTMLSelectElement>('#assistant-session-select')!;
  const renameBtn = root.querySelector<HTMLButtonElement>('#rename-assistant-session')!;
  const refreshBtn = root.querySelector<HTMLButtonElement>('#refresh-sheet-context')!;
  const deleteBtn = root.querySelector<HTMLButtonElement>('#delete-assistant-session')!;
  let currentId: string | null = null;
  let currentSummary = '';
  let sessions: AssistantSession[] = [];
  let activeAssetId: string | null = null;
  let suppressSelectChange = false;

  const renderOptions = () => {
    suppressSelectChange = true;
    try {
      select.replaceChildren();
      const placeholder = document.createElement('option');
      placeholder.value = '';
      placeholder.textContent = sessions.length ? '选择历史会话…' : '暂无历史会话';
      select.append(placeholder);
      for (const item of sessions) {
        const opt = document.createElement('option');
        opt.value = item.id;
        const when = formatWhen(item.updated_at);
        opt.textContent = when ? `${item.title} · ${when}` : item.title;
        select.append(opt);
      }
      if (currentId) select.value = currentId;
      renameBtn.disabled = !currentId;
      deleteBtn.disabled = !currentId;
    } finally {
      suppressSelectChange = false;
    }
  };

  const refreshList = async () => {
    const assetId = getAssetId();
    if (!assetId) {
      sessions = [];
      currentId = null;
      currentSummary = '';
      activeAssetId = null;
      renderOptions();
      return;
    }
    if (assetId !== activeAssetId) {
      // 会话严格归属当前资产；不能让上一张表或另一份导图的 currentId 继续参与保存。
      activeAssetId = assetId;
      sessions = [];
      currentId = null;
      currentSummary = '';
      renderOptions();
    }
    const data = await office.json<{ sessions: AssistantSession[] }>(`/assets/${assetId}/assistant-sessions`);
    if (getAssetId() !== assetId || activeAssetId !== assetId) return;
    sessions = data.sessions || [];
    renderOptions();
  };

  const load = async (sessionId: string, opts?: { notify?: boolean }) => {
    const assetId = getAssetId();
    if (!assetId) throw new Error('当前工作区尚未打开。');
    if (assetId !== activeAssetId) throw new Error('工作区已切换，请重新选择会话。');
    if (sessionId === currentId && opts?.notify === false) return sessions.find(item => item.id === sessionId) as AssistantSession;
    const data = await office.json<{ session: AssistantSession }>(
      `/assets/${assetId}/assistant-sessions/${sessionId}`,
    );
    if (getAssetId() !== assetId || activeAssetId !== assetId) {
      throw new Error('工作区已切换，请重新选择会话。');
    }
    currentId = data.session.id;
    currentSummary = data.session.summary || '';
    rememberSession(assetId, currentId);
    if (!sessions.some(item => item.id === currentId)) {
      sessions = [data.session, ...sessions];
    }
    renderOptions();
    if (opts?.notify !== false) hooks.onLoad(data.session);
    return data.session;
  };

  const startNew = async (opts?: { resetUi?: boolean }) => {
    const assetId = getAssetId();
    if (!assetId) throw new Error('当前工作区尚未打开。');
    if (assetId !== activeAssetId) throw new Error('工作区已切换，请稍后重试。');
    const data = await office.json<{ session: AssistantSession }>(
      `/assets/${assetId}/assistant-sessions`,
      { method: 'POST', body: '{}' },
    );
    if (getAssetId() !== assetId || activeAssetId !== assetId) {
      throw new Error('工作区已切换，请稍后重试。');
    }
    currentId = data.session.id;
    currentSummary = '';
    rememberSession(assetId, currentId);
    sessions = [data.session, ...sessions.filter(item => item.id !== data.session.id)];
    renderOptions();
    if (opts?.resetUi !== false) hooks.onNew();
    return currentId;
  };

  const save = async (payload: {
    anchor?: Context;
    turns: SessionTurn[];
    title?: string;
    summary?: string;
  }) => {
    const assetId = getAssetId();
    if (!assetId || !currentId) return;
    if (assetId !== activeAssetId) return;
    const body: Record<string, unknown> = { turns: payload.turns };
    if (payload.anchor) body.anchor = payload.anchor;
    if (payload.title !== undefined) body.title = payload.title;
    if (payload.summary !== undefined) {
      body.summary = payload.summary;
      currentSummary = payload.summary;
    }
    const data = await office.json<{ session: AssistantSession }>(
      `/assets/${assetId}/assistant-sessions/${currentId}`,
      { method: 'PUT', body: JSON.stringify(body) },
    );
    const saved = data.session;
    sessions = [saved, ...sessions.filter(item => item.id !== saved.id)];
    renderOptions();
  };

  const rename = async (title: string) => {
    const assetId = getAssetId();
    if (!assetId || !currentId) throw new Error('请先选择会话。');
    if (assetId !== activeAssetId) throw new Error('工作区已切换，请重新选择会话。');
    const name = title.trim();
    if (!name) throw new Error('请输入会话名。');
    const data = await office.json<{ session: AssistantSession }>(
      `/assets/${assetId}/assistant-sessions/${currentId}`,
      { method: 'PUT', body: JSON.stringify({ title: name }) },
    );
    const saved = data.session;
    sessions = [saved, ...sessions.filter(item => item.id !== saved.id)];
    renderOptions();
  };

  const remove = async (sessionId: string) => {
    const assetId = getAssetId();
    if (!assetId) return;
    if (assetId !== activeAssetId) return;
    await office.json(`/assets/${assetId}/assistant-sessions/${sessionId}`, { method: 'DELETE' });
    if (currentId === sessionId) {
      currentId = null;
      currentSummary = '';
      hooks.onNew();
    }
    await refreshList();
  };

  select.onchange = () => {
    if (suppressSelectChange || hooks.isStreaming?.()) return;
    const id = select.value;
    if (!id || id === currentId) return;
    void load(id).catch(err => {
      window.dispatchEvent(new CustomEvent('office-error', {
        detail: err instanceof Error ? err.message : String(err),
      }));
    });
  };

  refreshBtn.onclick = () => {
    void (async () => {
      try {
        const anchor = await hooks.onRefreshAnchor();
        if (!currentId) return;
        await save({ anchor, turns: hooks.currentTurns() });
        window.dispatchEvent(new CustomEvent('office-session-anchor-refreshed', { detail: anchor }));
      } catch (err) {
        window.dispatchEvent(new CustomEvent('office-error', {
          detail: err instanceof Error ? err.message : String(err),
        }));
      }
    })();
  };

  renameBtn.onclick = () => {
    if (!currentId || hooks.isStreaming?.()) return;
    const current = sessions.find(item => item.id === currentId);
    void (async () => {
      try {
        const name = await askText('修改会话名', '会话名称', current?.title || '', 80);
        if (!name || name === current?.title) return;
        await rename(name);
      } catch (err) {
        window.dispatchEvent(new CustomEvent('office-error', {
          detail: err instanceof Error ? err.message : String(err),
        }));
      }
    })();
  };

  deleteBtn.onclick = () => {
    if (!currentId) return;
    const id = currentId;
    void remove(id).catch(err => {
      window.dispatchEvent(new CustomEvent('office-error', {
        detail: err instanceof Error ? err.message : String(err),
      }));
    });
  };

  createIcons({ icons });

  return {
    currentId: () => currentId,
    summary: () => currentSummary,
    refreshList,
    startNew,
    load,
    save,
    rename,
    remove,
  };
}

export async function restoreLastSession(
  assetId: string,
  controller: SessionController,
) {
  await controller.refreshList();
  const last = recalledSession(assetId);
  if (!last) return;
  try {
    await controller.load(last);
  } catch {
    /* session may have been deleted */
  }
}
