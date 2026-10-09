import type { Palette } from '@/theme/tokens';

export const won = (n: number) => '₩ ' + n.toLocaleString('en-US');
export const pct = (n: number) => (n > 0 ? '+' : '') + n.toFixed(1) + '%';
export const chgColor = (c: Palette, n: number) => (n < 0 ? c.down : n > 0 ? c.up : c.textSub);

// 새벽 분석의 장전 제공 시각
// 한국 시간 08:30 이전의 전날 기준
export const analysisAsOf = (now = new Date()) => {
  const kst = new Date(now.getTime() + 9 * 3600_000);
  return kst.getUTCHours() * 60 + kst.getUTCMinutes() < 8 * 60 + 30 ? '어제 08:30 기준' : '오늘 08:30 기준';
};
