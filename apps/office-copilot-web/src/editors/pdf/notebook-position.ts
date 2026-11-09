/** Hidden editors report zero even though their last reading position is valid. */
export function notebookPositionPatch(host: Pick<HTMLElement, 'scrollTop' | 'clientHeight'>, visible: boolean) {
  return visible && host.clientHeight > 0 ? { notebookScrollTop: host.scrollTop } : {};
}

export function restoreNotebookPosition(
  host: Pick<HTMLElement, 'scrollTop' | 'scrollHeight' | 'clientHeight'>,
  top: number,
  current: () => boolean,
  done: () => void,
) {
  const target = Number.isFinite(top) && top >= 0 ? top : 0;
  let cancelled = false;
  let frame = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const apply = (attempt: number) => {
    if (cancelled || !current()) return;
    const available = Math.max(0, host.scrollHeight - host.clientHeight);
    if (host.clientHeight > 0) host.scrollTop = target;
    if ((host.clientHeight === 0 || available + 1 < target) && attempt < 30) {
      frame = requestAnimationFrame(() => apply(attempt + 1));
      return;
    }
    // Tiptap may scroll its initial selection into view after setContent.
    timer = setTimeout(() => {
      if (cancelled || !current()) return;
      host.scrollTop = target;
      done();
    }, 80);
  };
  frame = requestAnimationFrame(() => apply(0));
  return () => {
    cancelled = true;
    cancelAnimationFrame(frame);
    clearTimeout(timer);
  };
}
