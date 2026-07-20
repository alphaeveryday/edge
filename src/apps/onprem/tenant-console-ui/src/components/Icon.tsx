/* EDGE Console — icon set (lucide-style strokes) */
import type { CSSProperties } from 'react';

const ICONS: Record<string, string> = {
  home: 'M3 10.5 12 3l9 7.5M5 9.5V20a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V9.5',
  building:
    'M4 21V5a1 1 0 0 1 1-1h9a1 1 0 0 1 1 1v16M15 21V9h4a1 1 0 0 1 1 1v11M3 21h18M8 8h3M8 12h3M8 16h3',
  activity: 'M3 12h4l3 8 4-16 3 8h4',
  shield: 'M12 3 5 6v5c0 4.5 3 7.5 7 9 4-1.5 7-4.5 7-9V6z',
  shieldCheck: 'M12 3 5 6v5c0 4.5 3 7.5 7 9 4-1.5 7-4.5 7-9V6zM9 12l2 2 4-4',
  users:
    'M16 19v-1.5a3.5 3.5 0 0 0-3.5-3.5h-5A3.5 3.5 0 0 0 4 17.5V19M10 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7M20 19v-1.5a3.5 3.5 0 0 0-2.6-3.4M15 4.2a3.5 3.5 0 0 1 0 6.6',
  settings:
    'M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7ZM19.4 14.5a1.6 1.6 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.6 1.6 0 0 0-2.7 1.1V21a2 2 0 1 1-4 0v-.1a1.6 1.6 0 0 0-2.7-1.1l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.6 1.6 0 0 0-1.1-2.7H3a2 2 0 1 1 0-4h.1a1.6 1.6 0 0 0 1.1-2.7l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.6 1.6 0 0 0 2.7-1.1V3a2 2 0 1 1 4 0v.1a1.6 1.6 0 0 0 2.7 1.1l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.6 1.6 0 0 0-.3 1.8',
  search: 'M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14ZM20 20l-3.5-3.5',
  bell: 'M18 8a6 6 0 1 0-12 0c0 7-3 9-3 9h18s-3-2-3-9M13.7 21a2 2 0 0 1-3.4 0',
  chevR: 'M9 6l6 6-6 6',
  chevD: 'M6 9l6 6 6-6',
  chevL: 'M15 6l-6 6 6 6',
  plus: 'M12 5v14M5 12h14',
  check: 'M5 12l5 5 9-11',
  x: 'M6 6l12 12M18 6 6 18',
  copy: 'M9 9V6a2 2 0 0 1 2-2h7a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2h-3M4 11a2 2 0 0 1 2-2h7a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2z',
  logout: 'M14 4h4a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-4M9 12h11M16 8l4 4-4 4',
  key: 'M14.5 14.5a5 5 0 1 0-4.8-3.4L3 18v3h3v-2h2v-2h2l1.7-1.7a5 5 0 0 0 2.8.2ZM16.5 8.5h.01',
  webhook: 'M9 9a3 3 0 1 1 4 2.8L10.5 16M15 12a3 3 0 1 1-2.8 4H7M7.5 13.5A3 3 0 1 0 9 19l2-3.5',
  code: 'M9 8l-4 4 4 4M15 8l4 4-4 4',
  alert: 'M12 4 2.5 20h19zM12 10v4M12 17.5h.01',
  lock: 'M6 11V8a6 6 0 1 1 12 0v3M5 11h14a1 1 0 0 1 1 1v8a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1v-8a1 1 0 0 1 1-1Z',
  mail: 'M3 7a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2zM3.5 7l8.5 6 8.5-6',
  grid: 'M4 4h7v7H4zM13 4h7v7h-7zM13 13h7v7h-7zM4 13h7v7H4z',
  building2: 'M3 21h18M5 21V7l7-4 7 4v14M9 21v-4h6v4M9 10h.01M15 10h.01M9 13.5h.01M15 13.5h.01',
  list: 'M8 6h13M8 12h13M8 18h13M3.5 6h.01M3.5 12h.01M3.5 18h.01',
  sliders: 'M4 8h10M18 8h2M4 16h2M10 16h10M14 5v6M8 13v6',
  refresh: 'M20 11a8 8 0 0 0-14-4.5L3 9M3 4v5h5M4 13a8 8 0 0 0 14 4.5L21 15M21 20v-5h-5',
  trash: 'M4 7h16M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2M6 7l1 13a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1l1-13M10 11v6M14 11v6',
  globe: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18ZM3 12h18M12 3c2.5 2.5 3.8 5.6 3.8 9S14.5 18.5 12 21M12 3C9.5 5.5 8.2 8.6 8.2 12S9.5 18.5 12 21',
  layers: 'M12 3 3 8l9 5 9-5zM3 13l9 5 9-5M3 18l9 5 9-5',
};

export interface IconProps {
  n: string;
  s?: number;
  sw?: number;
  fill?: boolean;
  style?: CSSProperties;
  cls?: string;
}

export function Icon({ n, s = 18, sw = 1.8, fill = false, style, cls }: IconProps) {
  if (n === 'logoFill') {
    return (
      <svg width={s} height={s} viewBox="0 0 24 24" style={style} className={cls}>
        <rect x="3.5" y="13" width="4" height="7.5" rx="1.2" fill="currentColor" opacity="0.55" />
        <rect x="10" y="8" width="4" height="12.5" rx="1.2" fill="currentColor" opacity="0.8" />
        <rect x="16.5" y="3.5" width="4" height="17" rx="1.2" fill="currentColor" />
      </svg>
    );
  }
  const d = ICONS[n];
  return (
    <svg
      width={s}
      height={s}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={sw}
      strokeLinecap="round"
      strokeLinejoin="round"
      style={style}
      className={cls}
    >
      {d
        ? d
            .split('M')
            .filter(Boolean)
            .map((seg, i) => <path key={i} d={'M' + seg} fill={fill ? 'currentColor' : 'none'} />)
        : null}
    </svg>
  );
}
