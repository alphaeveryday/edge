export const colors = {
  bg: '#FFFFFF',
  bgAlt: '#F7F8FA',
  surface: '#F2F4F6',
  line: '#E5E8EB',
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
  down: '#3182F6',
  neutral: '#8B95A1',
  warn: '#E8A13D',
  positive: '#0E8A6C',
} as const;

// 전망 스티커 5단계. 디자인 스크립트가 잘려 강/약 색 구분은 확인 후 조정
export const signal = {
  strongUp: { label: '강력 상승', color: '#F04452', bg: '#FFF0F1' },
  up: { label: '상승', color: '#F04452', bg: '#FFF5F6' },
  neutral: { label: '중립', color: '#8B95A1', bg: '#F2F4F6' },
  down: { label: '하락', color: '#3182F6', bg: '#F0F6FF' },
  strongDown: { label: '강력 하락', color: '#1B64DA', bg: '#EAF2FF' },
} as const;
export type Signal = keyof typeof signal;

export const font = {
  sans: 'Pretendard',
  mono: 'JetBrainsMono',
} as const;

export const size = {
  caption: 11,
  small: 12,
  body: 14,
  base: 15,
  title: 17,
  h3: 20,
  h2: 26,
} as const;

export const space = { xs: 4, sm: 8, md: 12, lg: 16, xl: 20, xxl: 28 } as const;
export const radius = { sm: 8, md: 14, lg: 22, pill: 999 } as const;
