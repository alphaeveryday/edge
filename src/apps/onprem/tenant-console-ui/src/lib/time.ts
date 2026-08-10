/* KST 고정 시각 포맷(ALPHA-920) — 콘솔 원칙: 시각은 뷰어 타임존과 무관하게 거래소
 * 시간(KST)이다. `toLocaleString('sv-SE')` 직접 호출은 브라우저 타임존으로 렌더돼
 * 해외 타임존에서 같은 항목이 화면마다 다른 시각으로 보인다 — 반드시 이 유틸을 쓴다.
 * 모양은 서버 포맷(TimeText, "yyyy-MM-dd HH:mm KST")과 동일하게 맞춘다. */

const MINUTE = new Intl.DateTimeFormat('sv-SE', {
  timeZone: 'Asia/Seoul',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
});

const SECOND = new Intl.DateTimeFormat('sv-SE', {
  timeZone: 'Asia/Seoul',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
});

/** ISO 시각 → "yyyy-MM-dd HH:mm KST". 결측은 '—'. */
export function kstMinute(iso: string | null | undefined): string {
  return iso ? `${MINUTE.format(new Date(iso))} KST` : '—';
}

/** ISO 시각 → "yyyy-MM-dd HH:mm:ss KST" (검사·이력처럼 초까지 의미 있는 곳). 결측은 '—'. */
export function kstSecond(iso: string | null | undefined): string {
  return iso ? `${SECOND.format(new Date(iso))} KST` : '—';
}
