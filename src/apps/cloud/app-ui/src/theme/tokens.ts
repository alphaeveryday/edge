export const colors = {
  bg: '#FAFAF8',
  card: '#F7F8FA',
  surface: '#F2F4F6',
  white: '#FFFFFF',
  line: '#E5E8EB',
  lineStrong: '#D5DAE0',
  text: '#191F28',
  textSub: '#4E5968',
  textMuted: '#6B7684',
  textFaint: '#8B95A1',
  textDisabled: '#B0B8C1',
  primary: '#3D34E0',
  primaryPressed: '#2A22B8',
  primarySoft: '#EEF0FF',
  accent: '#6C5CF5',
  up: '#F04452',
  upDeep: '#D22F3D',
  down: '#3182F6',
  downDeep: '#1B64DA',
  neutral: '#8E8E93',
  neutralDeep: '#636366',
  warn: '#E8A13D',
  positive: '#0E8A6C',
} as const;

// 전망 스티커 5단계 색
export const signal = {
  strongUp: { label: '강력 상승', mark: '▲', double: true, color: '#F04452', labelColor: '#D22F3D', bg: 'rgba(240,68,82,0.18)', line: 'rgba(240,68,82,0.18)' },
  up: { label: '상승', mark: '▲', double: false, color: '#F04452', labelColor: '#D22F3D', bg: 'rgba(240,68,82,0.18)', line: 'rgba(240,68,82,0.18)' },
  neutral: { label: '중립', mark: '■', double: false, color: '#8E8E93', labelColor: '#636366', bg: 'rgba(142,142,147,0.22)', line: 'rgba(142,142,147,0.2)' },
  down: { label: '하락', mark: '▼', double: false, color: '#3182F6', labelColor: '#1B64DA', bg: 'rgba(49,130,246,0.18)', line: 'rgba(49,130,246,0.18)' },
  strongDown: { label: '강력 하락', mark: '▼', double: true, color: '#3182F6', labelColor: '#1B64DA', bg: 'rgba(49,130,246,0.18)', line: 'rgba(49,130,246,0.18)' },
} as const;
export type Signal = keyof typeof signal;
export const SIGNAL_ORDER: Signal[] = ['strongDown', 'down', 'neutral', 'up', 'strongUp'];

export const space = { xs: 4, sm: 8, md: 12, lg: 16, xl: 20, xxl: 28 } as const;
export const radius = { tag: 8, control: 10, field: 12, button: 14, card: 16, sheet: 22, pill: 999 } as const;

export const shadow = {
  sheet: { shadowColor: '#000', shadowOpacity: 0.18, shadowRadius: 18, shadowOffset: { width: 0, height: -10 } },
  floating: { shadowColor: '#1C1C1E', shadowOpacity: 0.08, shadowRadius: 4, shadowOffset: { width: 0, height: 2 }, elevation: 2 },
  fab: { shadowColor: '#191F28', shadowOpacity: 0.32, shadowRadius: 11, shadowOffset: { width: 0, height: 8 }, elevation: 6 },
  toast: { shadowColor: '#000', shadowOpacity: 0.28, shadowRadius: 15, shadowOffset: { width: 0, height: 10 }, elevation: 8 },
} as const;
export const PAGE_X = 20;
