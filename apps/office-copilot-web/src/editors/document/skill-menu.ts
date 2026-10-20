/** 文档工具栏「我的技能」：下拉列出当前文档的技能，弹窗新增或修改。 */

import { mountThinkingStatus } from '../../ui/thinking-status';
import { office } from '../../office-client/client';

export type SkillParamField = {
  key: string;
  label: string;
  input: 'text' | 'lines' | 'lookup' | 'checks';
  required?: boolean;
  placeholder?: string;
  options?: { id: string; label: string }[];
};

export type SkillType = {
  id: string;
  label: string;
  params: SkillParamField[];
};

export type DocumentSkill = {
  id: string;
  project_id: string;
  document_id: string;
  name: string;
  type: string;
  type_label: string;
  params: Record<string, string>;
};

type Hooks = {
  types: () => Promise<SkillType[]>;
  list: () => Promise<DocumentSkill[]>;
  save: (skill: { id?: string; name: string; type: string; params: Record<string, string> }) => Promise<DocumentSkill>;
  remove: (id: string) => Promise<void>;
  run: (id: string, onEvent: (event: Record<string, any>) => void) => Promise<void>;
  reloadDocument: (documentId?: string) => Promise<void>;
  searchSecurities: (query: string) => Promise<{ items: { ticker_code: string; name: string }[] }>;
};

type PickedStock = { code: string; name: string };

type WeReadLoginSession = {
  session_id: string;
  status: string;
  qr_data_url: string;
  error?: string;
};

function spreadLabel(label: HTMLElement) {
  const text = label.dataset.spread || label.textContent || '';
  label.dataset.spread = text;
  label.replaceChildren(...[...text].map(char => {
    const part = document.createElement('span');
    part.textContent = char;
    return part;
  }));
}

function parseStocks(raw: string): PickedStock[] {
  return raw.split('\n').map(line => line.trim()).filter(Boolean).map(line => {
    const [code, name] = line.split('|');
    return { code: (code || '').trim(), name: (name || code || '').trim() };
  }).filter(stock => stock.code && stock.name);
}

export function bindSkillMenu(button: HTMLButtonElement, hooks: Hooks) {
  const wrap = button.parentElement;
  const menu = wrap?.querySelector<HTMLElement>('[data-skill-menu]');
  if (!wrap || !menu) return;
  let open = false;
  let quickRunning = false;

  const close = () => {
    open = false;
    menu.hidden = true;
    button.setAttribute('aria-expanded', 'false');
  };

  const paint = (skills: DocumentSkill[]) => {
    menu.replaceChildren();
    if (!skills.length) {
      const empty = document.createElement('p');
      empty.className = 'skill-menu-empty';
      empty.textContent = '还没有技能';
      menu.append(empty);
    }
    skills.forEach(skill => {
      const row = document.createElement('div');
      row.className = 'skill-menu-row';
      const item = document.createElement('button');
      item.type = 'button';
      item.className = 'skill-menu-item';
      const label = document.createElement('span');
      label.textContent = skill.name;
      item.append(label);
      item.title = skill.type_label || skill.type;
      item.onclick = event => {
        event.stopPropagation();
        close();
        void openSkillDialog(hooks, skill);
      };
      const run = document.createElement('button');
      run.type = 'button';
      run.className = 'skill-menu-run';
      run.title = '运行';
      run.setAttribute('aria-label', `运行${skill.name}`);
      run.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><polygon points="8 5 19 12 8 19" fill="currentColor"></polygon></svg>';
      run.onclick = event => {
        event.stopPropagation();
        close();
        void quickRunSkill(hooks, skill, button, () => quickRunning, value => { quickRunning = value; });
      };
      row.append(item, run);
      menu.append(row);
    });
    const add = document.createElement('button');
    add.type = 'button';
    add.className = 'skill-menu-add';
    add.textContent = '新增技能';
    add.onclick = event => {
      event.stopPropagation();
      close();
      void openSkillDialog(hooks, null);
    };
    menu.append(add);
  };

  button.addEventListener('mousedown', event => event.preventDefault());
  button.onclick = async event => {
    event.stopPropagation();
    if (open) {
      close();
      return;
    }
    try {
      paint(await hooks.list());
    } catch (err) {
      window.dispatchEvent(new CustomEvent('office-error', { detail: err instanceof Error ? err.message : String(err) }));
      return;
    }
    open = true;
    menu.hidden = false;
    button.setAttribute('aria-expanded', 'true');
  };
  document.addEventListener('click', event => {
    if (!open || wrap.contains(event.target as Node)) return;
    close();
  });
}

function waitForWeReadLogin(initial: WeReadLoginSession): Promise<boolean> {
  return new Promise(resolve => {
    const overlay = document.createElement('div');
    overlay.className = 'dialog-overlay weread-login-overlay';
    overlay.innerHTML = `<div class="weread-login-dialog" role="dialog" aria-modal="true" aria-labelledby="weread-login-title">
      <header><div><h2 id="weread-login-title">登录微信读书</h2><p>请使用微信扫描二维码，并在手机上确认。</p></div><button type="button" data-close title="关闭" aria-label="关闭">×</button></header>
      <div class="weread-qr-wrap"><img data-qr alt="微信读书登录二维码"><p data-state>正在获取二维码…</p></div>
      <footer><button type="button" data-retry hidden>重新获取二维码</button><button type="button" data-cancel>取消</button></footer>
    </div>`;
    document.body.append(overlay);
    const image = overlay.querySelector<HTMLImageElement>('[data-qr]')!;
    const state = overlay.querySelector<HTMLElement>('[data-state]')!;
    const retry = overlay.querySelector<HTMLButtonElement>('[data-retry]')!;
    let session = initial;
    let settled = false;
    let timer = 0;
    const finish = (loggedIn: boolean) => {
      if (settled) return;
      settled = true;
      window.clearTimeout(timer);
      overlay.remove();
      resolve(loggedIn);
    };
    const paint = (next: WeReadLoginSession) => {
      session = next;
      if (next.qr_data_url) image.src = next.qr_data_url;
      image.hidden = !next.qr_data_url;
      if (next.status === 'authenticated') {
        finish(true);
        return;
      }
      if (next.status === 'failed' || next.status === 'expired') {
        state.textContent = next.error || '二维码已失效，请重新获取。';
        retry.hidden = false;
        return;
      }
      state.textContent = next.qr_data_url ? '等待扫码确认…' : '正在生成二维码…';
    };
    const poll = async () => {
      if (settled || !session.session_id) return;
      try {
        paint(await office.json<WeReadLoginSession>(`/integrations/weread/login/${session.session_id}`));
      } catch (error) {
        state.textContent = error instanceof Error ? error.message : String(error);
        retry.hidden = false;
        return;
      }
      if (!settled && session.status === 'pending') timer = window.setTimeout(() => { void poll(); }, 1200);
    };
    const restart = async () => {
      retry.hidden = true;
      state.textContent = '正在获取二维码…';
      try {
        paint(await office.json<WeReadLoginSession>('/integrations/weread/login', { method: 'POST' }));
        if (!settled && session.status === 'pending') void poll();
      } catch (error) {
        state.textContent = error instanceof Error ? error.message : String(error);
        retry.hidden = false;
      }
    };
    overlay.querySelectorAll<HTMLButtonElement>('[data-close], [data-cancel]').forEach(button => {
      button.addEventListener('click', () => finish(false));
    });
    overlay.addEventListener('click', event => { if (event.target === overlay) finish(false); });
    retry.addEventListener('click', () => { void restart(); });
    paint(session);
    if (session.status === 'pending') void poll();
  });
}

function quickRunSkill(
  hooks: Hooks,
  skill: DocumentSkill,
  anchor: HTMLElement,
  running: () => boolean,
  setRunning: (value: boolean) => void,
) {
  if (running()) return;
  setRunning(true);
  document.querySelector('.skill-run-pop')?.remove();
  const pop = document.createElement('div');
  pop.className = 'skill-run-pop';
  pop.setAttribute('role', 'dialog');
  pop.setAttribute('aria-label', skill.name);
  const title = document.createElement('h3');
  title.textContent = skill.name;
  const status = document.createElement('div');
  pop.append(title, status);
  document.body.append(pop);
  const rect = anchor.getBoundingClientRect();
  const width = pop.offsetWidth;
  const left = Math.min(Math.max(8, rect.right - width), window.innerWidth - width - 8);
  pop.style.left = `${left}px`;
  pop.style.top = `${rect.bottom + 8}px`;
  const thinking = mountThinkingStatus(status);
  thinking.begin();
  thinking.note('正在获取资讯…');
  let closed = false;
  const dismiss = () => {
    if (closed) return;
    closed = true;
    pop.remove();
    document.removeEventListener('mousedown', onDoc, true);
    window.removeEventListener('keydown', onKey);
  };
  const onDoc = (event: MouseEvent) => {
    if (pop.contains(event.target as Node)) return;
    dismiss();
  };
  const onKey = (event: KeyboardEvent) => {
    if (event.key === 'Escape') dismiss();
  };
  window.setTimeout(() => {
    document.addEventListener('mousedown', onDoc, true);
    window.addEventListener('keydown', onKey);
  }, 0);
  void (async () => {
    try {
      let nextDocumentId = '';
      let wereadLogin: WeReadLoginSession | null = null;
      await hooks.run(skill.id, event => {
        if (closed) return;
        if (event.type === 'status' && event.text) {
          if (String(event.text).includes('整理')) thinking.begin();
          else thinking.note(String(event.text));
        }
        if (event.type === 'delta' && event.part === 'reasoning' && event.text) thinking.append(String(event.text));
        if (event.type === 'done' && event.document_id) nextDocumentId = String(event.document_id);
        if (event.type === 'weread_login_required') wereadLogin = event as WeReadLoginSession;
      });
      if (wereadLogin) {
        const loggedIn = await waitForWeReadLogin(wereadLogin);
        if (!loggedIn || closed) {
          setRunning(false);
          if (!closed) thinking.note('微信读书登录未完成。');
          return;
        }
        dismiss();
        setRunning(false);
        quickRunSkill(hooks, skill, anchor, running, setRunning);
        return;
      }
      if (!closed) thinking.finish();
      await hooks.reloadDocument(nextDocumentId || undefined);
      dismiss();
    } catch (err) {
      setRunning(false);
      if (closed) return;
      thinking.finish();
      thinking.note(err instanceof Error ? err.message : String(err));
      return;
    }
    setRunning(false);
  })();
}

async function openSkillDialog(hooks: Hooks, current: DocumentSkill | null) {
  const types = await hooks.types();
  if (!types.length) throw new Error('后台还没有可用的技能类型。');
  const overlay = document.createElement('div');
  overlay.className = 'dialog-overlay skill-dialog-overlay';
  overlay.innerHTML = `
    <div class="skill-dialog" role="dialog" aria-labelledby="skill-dialog-title">
      <h2 id="skill-dialog-title">${current ? '修改技能' : '新增技能'}</h2>
      <div class="skill-form">
        <label class="skill-field"><span class="skill-label">名称</span><input data-name maxlength="40" /></label>
        <label class="skill-field"><span class="skill-label">类型</span><select data-type></select></label>
        <div class="skill-params" data-params></div>
      </div>
      <div class="digest-job-status" data-status></div>
      <div class="dialog-actions">
        ${current ? '<button type="button" data-delete>删除</button>' : ''}
        <button type="button" data-run>执行并写入</button>
        <button type="button" data-cancel>关闭</button>
        <button type="button" class="primary" data-save>保存</button>
      </div>
    </div>`;
  document.body.append(overlay);
  const nameInput = overlay.querySelector<HTMLInputElement>('[data-name]')!;
  const typeSelect = overlay.querySelector<HTMLSelectElement>('[data-type]')!;
  const form = overlay.querySelector<HTMLElement>('.skill-form')!;
  const paramsBox = overlay.querySelector<HTMLElement>('[data-params]')!;
  const status = overlay.querySelector<HTMLElement>('[data-status]')!;
  const longestLabel = Math.max(
    ...['名称', '类型', ...types.flatMap(type => type.params.map(field => field.label))].map(text => [...text].length),
  );
  form.style.setProperty('--skill-label', `${longestLabel}em`);
  form.querySelectorAll<HTMLElement>('.skill-label').forEach(spreadLabel);
  const thinking = mountThinkingStatus(status);
  types.forEach(type => {
    const option = document.createElement('option');
    option.value = type.id;
    option.textContent = type.label;
    typeSelect.append(option);
  });
  nameInput.value = current?.name || '';
  typeSelect.value = current?.type || types[0].id;
  if (current) typeSelect.disabled = true;

  const selectedType = () => types.find(type => type.id === typeSelect.value) || types[0];
  const readParams = () => {
    const params: Record<string, string> = {};
    paramsBox.querySelectorAll<HTMLInputElement | HTMLTextAreaElement>('[data-param]').forEach(field => {
      params[field.dataset.param || ''] = field.value;
    });
    return params;
  };
  const paintParams = () => {
    const spec = selectedType();
    paramsBox.replaceChildren();
    spec.params.forEach(field => {
      const label = document.createElement(field.input === 'checks' || field.input === 'lookup' ? 'div' : 'label');
      label.className = 'skill-param';
      const caption = document.createElement('span');
      caption.className = 'skill-label';
      caption.textContent = field.label;
      spreadLabel(caption);
      const saved = current?.type === spec.id ? (current.params[field.key] || '') : '';
      if (field.input === 'lookup') {
        label.classList.add('skill-stock-field');
        label.append(...stockLookup(caption, saved));
      } else if (field.input === 'checks') {
        label.append(caption, checkGroup(field, saved));
      } else {
        const control = field.input === 'lines' ? document.createElement('textarea') : document.createElement('input');
        control.dataset.param = field.key;
        control.placeholder = field.placeholder || '';
        control.value = saved;
        label.append(caption, control);
      }
      paramsBox.append(label);
    });
  };
  paintParams();
  typeSelect.onchange = paintParams;

  function stockLookup(caption: HTMLElement, saved: string) {
    const control = document.createElement('div');
    control.className = 'skill-control';
    const wrap = document.createElement('div');
    wrap.className = 'skill-suggest';
    const stored = document.createElement('input');
    stored.type = 'hidden';
    stored.dataset.param = 'stocks';
    stored.value = saved.includes('|') ? saved : '';
    const chips = document.createElement('div');
    chips.className = 'skill-stock-chips';
    const box = document.createElement('input');
    box.placeholder = '输入名称或代码，从结果中选择';
    const list = document.createElement('div');
    list.className = 'skill-suggest-list';
    list.hidden = true;
    const selected = parseStocks(stored.value);
    const paintChips = () => {
      chips.replaceChildren();
      chips.hidden = selected.length === 0;
      selected.forEach((stock, index) => {
        const chip = document.createElement('span');
        chip.className = 'skill-stock-chip';
        chip.textContent = `${stock.name} ${stock.code}`;
        const remove = document.createElement('button');
        remove.type = 'button';
        remove.textContent = '×';
        remove.onclick = () => {
          selected.splice(index, 1);
          stored.value = selected.map(item => `${item.code}|${item.name}`).join('\n');
          paintChips();
        };
        chip.append(remove);
        chips.append(chip);
      });
    };
    paintChips();
    let timer = 0;
    box.addEventListener('input', () => {
      window.clearTimeout(timer);
      const query = box.value.trim();
      if (!query) {
        list.hidden = true;
        list.replaceChildren();
        return;
      }
      timer = window.setTimeout(async () => {
        try {
          const found = await hooks.searchSecurities(query);
          list.replaceChildren();
          (found.items || []).forEach(item => {
            const choice = document.createElement('button');
            choice.type = 'button';
            choice.textContent = `${item.name} ${item.ticker_code}`;
            choice.onclick = () => {
              if (!selected.some(stock => stock.code === item.ticker_code)) {
                selected.push({ code: item.ticker_code, name: item.name });
                stored.value = selected.map(stock => `${stock.code}|${stock.name}`).join('\n');
                paintChips();
              }
              box.value = '';
              list.hidden = true;
            };
            list.append(choice);
          });
          if (!list.childElementCount) {
            const empty = document.createElement('p');
            empty.className = 'skill-suggest-empty';
            empty.textContent = '没有匹配的股票';
            list.append(empty);
          }
          list.hidden = false;
        } catch (err) {
          list.hidden = true;
          thinking.note(err instanceof Error ? err.message : String(err));
        }
      }, 250);
    });
    control.append(wrap, chips);
    wrap.append(stored, box, list);
    return [caption, control];
  }

  function checkGroup(field: SkillParamField, saved: string) {
    const wrap = document.createElement('div');
    wrap.className = 'skill-checks';
    const stored = document.createElement('input');
    stored.type = 'hidden';
    stored.dataset.param = field.key;
    const allowed = new Set((field.options || []).map(option => option.id));
    const picked = new Set(saved.split(',').map(item => item.trim()).filter(item => allowed.has(item)));
    const sync = () => { stored.value = [...picked].join(','); };
    sync();
    (field.options || []).forEach(option => {
      const item = document.createElement('label');
      const box = document.createElement('input');
      box.type = 'checkbox';
      box.checked = picked.has(option.id);
      box.onchange = () => {
        if (box.checked) picked.add(option.id);
        else picked.delete(option.id);
        sync();
      };
      item.append(box, document.createTextNode(option.label));
      wrap.append(item);
    });
    wrap.append(stored);
    return wrap;
  }

  const close = () => overlay.remove();
  const payload = () => ({
    id: current?.id,
    name: nameInput.value,
    type: typeSelect.value,
    params: readParams(),
  });
  overlay.querySelector('[data-cancel]')!.addEventListener('click', close);
  overlay.addEventListener('click', event => { if (event.target === overlay) close(); });
  overlay.querySelector('[data-save]')!.addEventListener('click', async () => {
    thinking.clear();
    try {
      await hooks.save(payload());
      close();
    } catch (err) {
      thinking.note(err instanceof Error ? err.message : String(err));
    }
  });
  overlay.querySelector('[data-run]')!.addEventListener('click', async () => {
    thinking.clear();
    thinking.note('正在获取资讯并写入文档…');
    try {
      const saved = await hooks.save(payload());
      const run = async (): Promise<string> => {
        let nextDocumentId = '';
        let wereadLogin: WeReadLoginSession | null = null;
        await hooks.run(saved.id, event => {
          if (event.type === 'status' && event.text) {
            if (String(event.text).includes('整理')) thinking.begin();
            else thinking.note(String(event.text));
          }
          if (event.type === 'delta' && event.part === 'reasoning' && event.text) thinking.append(String(event.text));
          if (event.type === 'done' && event.document_id) nextDocumentId = String(event.document_id);
          if (event.type === 'weread_login_required') wereadLogin = event as WeReadLoginSession;
        });
        if (!wereadLogin) return nextDocumentId;
        if (!await waitForWeReadLogin(wereadLogin)) throw new Error('微信读书登录未完成。');
        thinking.clear();
        thinking.note('登录成功，正在继续获取资讯…');
        return run();
      };
      const nextDocumentId = await run();
      thinking.finish();
      await hooks.reloadDocument(nextDocumentId || undefined);
      close();
    } catch (err) {
      thinking.finish();
      thinking.note(err instanceof Error ? err.message : String(err));
    }
  });
  overlay.querySelector('[data-delete]')?.addEventListener('click', async () => {
    if (!current) return;
    thinking.clear();
    try {
      await hooks.remove(current.id);
      close();
    } catch (err) {
      thinking.note(err instanceof Error ? err.message : String(err));
    }
  });
}
