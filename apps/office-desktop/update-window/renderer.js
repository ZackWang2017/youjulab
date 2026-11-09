const $ = id => document.getElementById(id);
let current;
function render(state) {
  current = state;
  $('heading').textContent = state.release ? '发现新版本' : '正在检查更新';
  $('version').textContent = state.release ? `当前 ${state.currentVersion}  →  ${state.release.version}` : `当前 ${state.currentVersion}`;
  $('mandatory').hidden = !state.release?.mandatory;
  $('message').textContent = state.message || '';
  $('details').hidden = !state.release;
  $('notes').textContent = state.release?.notes || '维护与体验优化';
  $('error').hidden = !state.error;
  $('error').textContent = state.error || '';
  $('download').hidden = !['downloading','installing','downloaded'].includes(state.phase);
  $('progress').value = state.percent || 0;
  $('percent').textContent = `${Math.round(state.percent || 0)}%`;
  $('phase').textContent = state.phase === 'installing' ? '准备安装' : state.phase === 'downloaded' ? '下载完成' : '正在下载';
  $('skip').hidden = !state.release || state.release.mandatory || ['checking','downloading','installing'].includes(state.phase);
  $('retry').hidden = !state.release || ['checking','downloading','installing'].includes(state.phase);
  $('update').hidden = !state.release || !['available','error'].includes(state.phase);
  $('cancel').hidden = state.phase !== 'downloading';
  $('open').hidden = !(state.platform === 'darwin' && state.phase === 'downloaded');
  $('manual').hidden = !(state.platform === 'darwin' && state.phase === 'downloaded');
  $('quit').disabled = state.phase === 'installing';
}
window.updater.onState(render);
window.updater.ready().then(render);
document.querySelectorAll('[data-action]').forEach(button => button.addEventListener('click', async () => {
  button.disabled = true;
  try { await window.updater.action(button.dataset.action); }
  catch (error) { $('error').hidden = false; $('error').textContent = error.message; }
  finally { if (current) render(current); button.disabled = false; }
}));
