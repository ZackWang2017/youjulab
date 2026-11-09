document.querySelectorAll('time[datetime]').forEach(time => {
  time.textContent = new Date(time.dateTime).toLocaleString('zh-CN', { hour12: false });
});
document.querySelectorAll('form').forEach(form => {
  form.addEventListener('submit', event => {
    if (form.dataset.confirm && !confirm(form.dataset.confirm)) {
      event.preventDefault();
      return;
    }
    if (form.querySelector('[name="platform"]')?.value === 'win32' && form.querySelector('[name="arch"]')?.value !== 'x64') {
      event.preventDefault();
      alert('Windows 首版仅支持 x64。');
      return;
    }
    const button = event.submitter;
    if (button) { button.disabled = true; button.textContent = '处理中…'; }
  });
});
document.querySelectorAll('[name="platform"]').forEach(select => {
  select.addEventListener('change', () => {
    const arch = select.form.querySelector('[name="arch"]');
    arch.querySelector('[value="arm64"]').disabled = select.value === 'win32';
    if (select.value === 'win32') arch.value = 'x64';
  });
});
