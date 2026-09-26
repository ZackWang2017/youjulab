export type ProjectTerm = { en: string; zh: string; forbidden: string[] };

export function openTermsDialog(hooks: {
  load: () => Promise<ProjectTerm[]>;
  save: (term: ProjectTerm) => Promise<void>;
  remove: (en: string) => Promise<void>;
}) {
  const prev = document.querySelector('.terms-dialog');
  prev?.remove();
  const root = document.createElement('div');
  root.className = 'terms-dialog';
  root.innerHTML = `
    <div class="terms-card" role="dialog" aria-label="项目术语">
      <h2>项目术语</h2>
      <p>这里的译法会覆盖内置财报词表。英文、中文必填，禁用译法用逗号分隔。</p>
      <div class="terms-list" data-list></div>
      <form class="terms-form">
        <input name="en" required maxlength="120" placeholder="英文，如 revenue" aria-label="英文">
        <input name="zh" required maxlength="120" placeholder="中文译法" aria-label="中文">
        <input name="forbidden" maxlength="200" placeholder="禁用译法，可选" aria-label="禁用译法">
        <button type="submit" class="primary">添加</button>
      </form>
      <div class="dialog-actions"><button type="button" data-close>关闭</button></div>
    </div>`;
  document.body.append(root);
  const list = root.querySelector<HTMLElement>('[data-list]')!;
  const form = root.querySelector<HTMLFormElement>('form')!;

  const close = () => root.remove();
  root.querySelector<HTMLButtonElement>('[data-close]')!.onclick = close;
  root.addEventListener('click', e => { if (e.target === root) close(); });

  const draw = (terms: ProjectTerm[]) => {
    list.replaceChildren();
    if (!terms.length) {
      const empty = document.createElement('p');
      empty.textContent = '还没有项目术语。';
      list.append(empty);
      return;
    }
    for (const term of terms) {
      const row = document.createElement('div');
      row.className = 'terms-row';
      const label = document.createElement('span');
      label.textContent = term.forbidden.length
        ? `${term.en} → ${term.zh}（不用 ${term.forbidden.join('、')}）`
        : `${term.en} → ${term.zh}`;
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = '删除';
      button.onclick = () => {
        void hooks.remove(term.en).then(reload).catch(showError);
      };
      row.append(label, button);
      list.append(row);
    }
  };

  const showError = (e: unknown) => {
    window.dispatchEvent(new CustomEvent('office-error', { detail: e instanceof Error ? e.message : String(e) }));
  };

  const reload = () => hooks.load().then(draw).catch(showError);
  form.addEventListener('submit', e => {
    e.preventDefault();
    const data = new FormData(form);
    const forbidden = String(data.get('forbidden') || '').split(/[,，]/).map(item => item.trim()).filter(Boolean);
    void hooks.save({
      en: String(data.get('en') || '').trim(),
      zh: String(data.get('zh') || '').trim(),
      forbidden,
    }).then(() => {
      form.reset();
      return reload();
    }).catch(showError);
  });
  void reload();
}
