const light = {
  bg: '#FFFFFF',
  card: '#F7F8FA',
  surface: '#F2F4F6',
  line: '#E5E8EB',
  lineStrong: '#D5DAE0',
  text: '#191F28',
  textSub: '#4E5968',
  textMuted: '#6B7684',
  textFaint: '#8B95A1',
  textDisabled: '#B0B8C1',
  onPrimary: '#FFFFFF',
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
  upSoft: 'rgba(240,68,82,0.12)',
  downSoft: 'rgba(49,130,246,0.12)',
  upLight: '#F7A1A8',
  downLight: '#8FBBFA',
  warnDeep: '#C9820E',
  success: '#22C55E',
  primaryTint: '#DDD9FB',
  highlight: 'rgba(49,130,246,0.18)',
  voteUpBg: '#FFF0F1',
  voteDownBg: '#EAF2FF',
  unreadBg: '#F8FAFF',
  toastBg: '#2B2F3A',
  scrim: 'rgba(0,0,0,0.35)',
  tabBarLine: 'rgba(0,0,0,0.07)',
  chartMa20: '#E0891A',
  // 게이지 강력 하락 칸
  gaugeStrongDown: '#1B64DA',
  // 스티커 광택 그라데이션과 테두리
  gloss: ['rgba(255,255,255,0.8)', 'rgba(255,255,255,0.42)'] as readonly [string, string],
  glossLine: 'rgba(255,255,255,0.75)',
  // 히트맵 칸 바탕: 온도 3단과 등락 5단
  heat: { help: '#FBD5D8', neutral: '#E9EBEF', burden: '#CFE0FB', up2: '#F9C2C7', up1: '#FCE3E5', flat: '#E9EBEF', down1: '#DCE9FC', down2: '#BFD7FA' },
  signalAlpha: { up: 'rgba(240,68,82,0.18)', neutral: 'rgba(142,142,147,0.22)', neutralLine: 'rgba(142,142,147,0.2)', down: 'rgba(49,130,246,0.18)' },
};
export type Palette = typeof light;

const dark: Palette = {
  bg: '#17171C',
  card: '#1F1F25',
  surface: '#26262D',
  line: '#2C2C34',
  lineStrong: '#3A3A44',
  text: '#ECEEF1',
  textSub: '#B6BCC5',
  textMuted: '#9199A4',
  textFaint: '#747C87',
  textDisabled: '#4E545D',
  onPrimary: '#FFFFFF',
  primary: '#7B74F2',
  primaryPressed: '#6058E0',
  primarySoft: '#25234A',
  accent: '#8D80F8',
  up: '#F2555F',
  upDeep: '#FF7A84',
  down: '#4B93F7',
  downDeep: '#79AEFA',
  neutral: '#8E8E93',
  neutralDeep: '#AEAEB2',
  warn: '#EBAA4D',
  positive: '#2DB38F',
  upSoft: 'rgba(242,85,95,0.18)',
  downSoft: 'rgba(75,147,247,0.18)',
  upLight: '#7A3238',
  downLight: '#2D4F80',
  warnDeep: '#E0A03A',
  success: '#2FD06A',
  primaryTint: '#3A3670',
  highlight: 'rgba(75,147,247,0.26)',
  voteUpBg: '#3A1F23',
  voteDownBg: '#1C2A40',
  unreadBg: '#1C1E2A',
  toastBg: '#3A3D48',
  scrim: 'rgba(0,0,0,0.6)',
  tabBarLine: 'rgba(255,255,255,0.08)',
  chartMa20: '#F0A040',
  gaugeStrongDown: '#4B93F7',
  gloss: ['rgba(255,255,255,0.14)', 'rgba(255,255,255,0.04)'],
  glossLine: 'rgba(255,255,255,0.12)',
  heat: { help: 'rgba(242,85,95,0.32)', neutral: '#2C2C34', burden: 'rgba(75,147,247,0.32)', up2: 'rgba(242,85,95,0.42)', up1: 'rgba(242,85,95,0.2)', flat: '#2C2C34', down1: 'rgba(75,147,247,0.2)', down2: 'rgba(75,147,247,0.42)' },
  signalAlpha: { up: 'rgba(242,85,95,0.22)', neutral: 'rgba(142,142,147,0.24)', neutralLine: 'rgba(142,142,147,0.2)', down: 'rgba(75,147,247,0.22)' },
};

export type Scheme = 'light' | 'dark';
export const palettes: Record<Scheme, Palette> = { light, dark };

// 전망 스티커 5단계 색
const SIGNAL_META = {
  strongUp: { label: '강력 상승', mark: '▲', double: true, dir: 'up' },
  up: { label: '상승', mark: '▲', double: false, dir: 'up' },
  neutral: { label: '중립', mark: '■', double: false, dir: 'neutral' },
  down: { label: '하락', mark: '▼', double: false, dir: 'down' },
  strongDown: { label: '강력 하락', mark: '▼', double: true, dir: 'down' },
} as const;
export type Signal = keyof typeof SIGNAL_META;
export interface SignalStyle { label: string; mark: string; double: boolean; color: string; labelColor: string; bg: string; line: string }

export const makeSignal = (c: Palette): Record<Signal, SignalStyle> => {
  const tone = {
    up: { color: c.up, labelColor: c.upDeep, bg: c.signalAlpha.up, line: c.signalAlpha.up },
    neutral: { color: c.neutral, labelColor: c.neutralDeep, bg: c.signalAlpha.neutral, line: c.signalAlpha.neutralLine },
    down: { color: c.down, labelColor: c.downDeep, bg: c.signalAlpha.down, line: c.signalAlpha.down },
  };
  const out = {} as Record<Signal, SignalStyle>;
  for (const k of Object.keys(SIGNAL_META) as Signal[]) {
    const m = SIGNAL_META[k];
    out[k] = { label: m.label, mark: m.mark, double: m.double, ...tone[m.dir] };
  }
  return out;
};
export const SIGNAL_LABEL = Object.fromEntries(Object.entries(SIGNAL_META).map(([k, v]) => [k, v.label])) as Record<Signal, string>;
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
