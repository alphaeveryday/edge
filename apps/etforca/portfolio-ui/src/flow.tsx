import type { ReactNode } from 'react';
import type { Holding } from './api';

// 입력·시작·기다림 화면(8차 시안)이 함께 쓰는 작은 조각. 선 아이콘은 ui-kit 에 없는 것만 여기 둔다(currentColor 상속).
const glyphs = {
  search: 'm21 21-4.3-4.3M11 3a8 8 0 1 0 0 16 8 8 0 0 0 0-16z',
  lock: 'M5 11h14v10H5zM8 11V7a4 4 0 0 1 8 0v4',
  shield: 'M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1zM9 12l2 2 4-4',
  clock: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM12 7v5l3 2',
  calendar: 'M5 5h14v15H5zM8 3v4M16 3v4M5 10h14',
  sprout: 'M12 21v-9M12 12c0-3.5 2.5-6 7-6 0 3.5-2.5 6-7 6zM12 15c0-3-2-5-6-5 0 3 2 5 6 5',
  help: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM9.1 9a3 3 0 0 1 5.8 1c0 2-3 3-3 3M12 17h.01',
  wallet: 'M19 7V5a1 1 0 0 0-1-1H5a2 2 0 0 0 0 4h15a1 1 0 0 1 1 1v4h-3a2 2 0 0 0 0 4h3a1 1 0 0 0 1-1v-2M3 6v13a2 2 0 0 0 2 2h15a1 1 0 0 0 1-1v-4',
  layers: 'M12 2 2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5',
  link: 'M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71',
  info: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM12 16v-4M12 8h.01',
  star: 'M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01z',
  refresh: 'M3 12a9 9 0 0 1 15-6.7L21 8M21 3v5h-5M21 12a9 9 0 0 1-15 6.7L3 16M3 21v-5h5',
  check: 'M20 6 9 17l-5-5',
  plus: 'M12 5v14M5 12h14',
  minus: 'M5 12h14',
  right: 'm9 18 6-6-6-6',
  left: 'm15 18-6-6 6-6',
  briefcase: 'M4 7h16v13H4zM16 7V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v2',
  globe: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18',
  factory: 'M3 20V10l6 3.5V10l6 3.5V5h4v15zM3 20h18',
  alert: 'M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0zM12 9v4M12 17h.01',
  message: 'M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z',
  x: 'M18 6 6 18M6 6l12 12',
};
export type GlyphName = keyof typeof glyphs;
export function Glyph({ name, size = 20 }: { name: GlyphName; size?: number }) {
  return <svg className="fl-glyph" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={glyphs[name]} /></svg>;
}

// 큰 금액은 만·억으로 줄여 한눈에(1,250만 · 1.2억), 10만 아래는 소수 한 자리(1.5만), 만 원 아래는 원 그대로.
// 만으로 먼저 반올림해 9,999.5만 원이 '10,000만'이 아니라 '1억'으로 읽히게 한다.
export const man = (n: number) => Math.round(n / 1e4) >= 1e4 ? `${(n / 1e8).toLocaleString('ko-KR', { maximumFractionDigits: 1 })}억`
  : n >= 1e4 ? `${(n / 1e4).toLocaleString('ko-KR', { maximumFractionDigits: n < 1e5 ? 1 : 0 })}만` : n.toLocaleString('ko-KR');
// 만 원 단위 숫자 칸(전체 금액·월 납입): 숫자와 점 하나, 소수 넷째 자리(=원)까지만 남긴다. 다른 점·글자는 버린다.
// 정수는 15자리까지 — 상한(10조 원 = 10자리)을 넘는 값은 그대로 남아 화면이 고치게 하고, 잘라서 맞는 값이 되는 일은 없다.
export const manInput = (value: string) => { const [whole, ...rest] = value.replace(/[^\d.]/g, '').split('.'); return whole.replace(/^0+(?=\d)/, '').slice(0, 15) + (rest.length ? '.' + rest.join('').slice(0, 4) : ''); };
export const won = (n: number) => n >= 1e4 ? `${man(n)} 원` : `${n.toLocaleString('ko-KR')}원`;

// 종목 동그라미에 넣는 짧은 이름: ETF 는 운용사 이름(TIGER·KODEX), 미국 상장은 티커, 주식은 앞 두 글자.
export function shortName(h: Pick<Holding, 'name' | 'kind' | 'instrument_id'>) {
  if (h.kind === 'cash') return '현금';
  if (h.instrument_id?.startsWith('US:')) return h.instrument_id.slice(3, 8);
  return h.kind === 'stock' ? h.name.slice(0, 2) : h.name.split(/\s/)[0].slice(0, 6);
}
export function Avatar({ holding, color }: { holding: Pick<Holding, 'name' | 'kind' | 'instrument_id'>; color?: string }) {
  return <span className="fl-avatar" data-kind={holding.kind === 'stock' ? 'stock' : 'etf'} aria-hidden="true">{shortName(holding)}{color && <i style={{ background: color }} />}</span>;
}

export function Brand({ children }: { children?: ReactNode }) {
  return <header className="fl-brand"><b>ETF Orca</b>{children}</header>;
}
// 남은 횟수는 서버가 알려 주지 않는다 — 규칙(24시간에 3번)만 적는다.
export function Quota({ children }: { children?: ReactNode }) {
  return <p className="fl-quota"><span className="fl-quota__dots" aria-hidden="true"><i /><i /><i /></span>24시간에 3번 무료{children}</p>;
}
