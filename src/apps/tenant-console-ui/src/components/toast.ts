/* EDGE Console — global toast (vanilla DOM, callable from anywhere) */

type ToastTone = 'ok' | 'bad' | 'info';

export function toast(msg: string, tone: ToastTone = 'ok') {
  let host = document.getElementById('toast-host');
  if (!host) {
    host = document.createElement('div');
    host.id = 'toast-host';
    host.className = 'toast-host';
    document.body.appendChild(host);
  }
  const el = document.createElement('div');
  el.className = 'toast toast-' + tone;
  const ic =
    tone === 'bad'
      ? 'M6 6l12 12M18 6 6 18'
      : tone === 'info'
        ? 'M12 8h.01M11 12h1v4h1'
        : 'M5 12l5 5 9-11';
  el.innerHTML = `<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="${ic}"/></svg><span></span>`;
  el.querySelector('span')!.textContent = msg;
  host.appendChild(el);
  requestAnimationFrame(() => el.classList.add('in'));
  setTimeout(() => {
    el.classList.remove('in');
    setTimeout(() => el.remove(), 260);
  }, 2600);
}

export function copyText(text: string, msg?: string) {
  try {
    navigator.clipboard?.writeText(text);
  } catch {
    /* clipboard 미지원 환경 무시 */
  }
  toast(msg ?? '클립보드에 복사되었습니다');
}
