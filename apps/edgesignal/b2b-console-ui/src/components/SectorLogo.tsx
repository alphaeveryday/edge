import { sectorKind, themeColor, type SectorKind } from '../report.ts';

const W = '#fff';

function Glyph({ kind, bg }: { kind: SectorKind; bg: string }) {
  switch (kind) {
    case 'chip':
      return (
        <>
          {[8.25, 11.1, 13.95].map((x) => (
            <g key={x}>
              <rect x={x} y="1.6" width="1.8" height="3.6" rx=".9" fill={W} />
              <rect x={x} y="18.8" width="1.8" height="3.6" rx=".9" fill={W} />
              <rect x="1.6" y={x} width="3.6" height="1.8" rx=".9" fill={W} />
              <rect x="18.8" y={x} width="3.6" height="1.8" rx=".9" fill={W} />
            </g>
          ))}
          <rect x="4.6" y="4.6" width="14.8" height="14.8" rx="3.6" fill={W} />
          <rect x="8.9" y="8.9" width="6.2" height="6.2" rx="1.7" fill={bg} />
        </>
      );
    case 'defense':
      return (
        <>
          <path d="M12 1.9l8.2 3v6.6c0 5-3.3 8.9-8.2 10.6-4.9-1.7-8.2-5.6-8.2-10.6V4.9z" fill={W} />
          <path d="M8.3 11.9l2.6 2.6 4.8-5.2" fill="none" stroke={bg} strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
        </>
      );
    case 'bio':
      return (
        <>
          <path d="M8.4 2.2h7.2a1.3 1.3 0 010 2.6h-.5v4l4.6 8.6c1.1 2-.4 4.4-2.6 4.4H6.9c-2.2 0-3.7-2.4-2.6-4.4l4.6-8.6v-4h-.5a1.3 1.3 0 010-2.6z" fill={W} />
          <circle cx="10.2" cy="17.2" r="1.5" fill={bg} />
          <circle cx="14.1" cy="14.9" r="1.1" fill={bg} />
        </>
      );
    case 'green':
      return (
        <>
          <path d="M20.6 3.4c.4 9.6-4.4 14.4-11.2 14.4H6.2c-1 0-1.7-.9-1.5-1.9C6.2 8.3 11.5 3.4 20.6 3.4z" fill={W} />
          <path d="M4.6 20.6c1.2-3.8 3.6-6.8 7.2-8.9" fill="none" stroke={W} strokeWidth="2.2" strokeLinecap="round" />
        </>
      );
    case 'power':
      return <path d="M13.8 1.9L4.6 13.4h6.3l-1.3 8.7 9.8-12h-6.5z" fill={W} stroke={W} strokeWidth=".8" strokeLinejoin="round" />;
    case 'machine':
      return (
        <>
          <rect x="10.3" y="1.6" width="3.4" height="20.8" rx="1.2" fill={W} />
          <rect x="1.6" y="10.3" width="20.8" height="3.4" rx="1.2" fill={W} />
          <path d="M18.15 3.44L20.56 5.85L5.85 20.56L3.44 18.15z M20.56 18.15L18.15 20.56L3.44 5.85L5.85 3.44z" fill={W} />
          <circle cx="12" cy="12" r="7.2" fill={W} />
          <circle cx="12" cy="12" r="2.9" fill={bg} />
        </>
      );
    default:
      return (
        <>
          <circle cx="12" cy="12" r="9.4" fill={W} />
          <path d="M7.6 13.9l3.1-3.4 2.5 2.2 3.4-4" fill="none" stroke={bg} strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
        </>
      );
  }
}

/** ETF Orca 앱 SectorIcon 이식 — 테마 해시 배경색 원 + 업종 글리프. */
export function SectorLogo({ theme, size }: { theme: string; size: number }) {
  const bg = themeColor(theme);
  const inner = size - 2 * Math.round(size * 0.22);
  return (
    <span className="logo" style={{ width: size, height: size, background: bg }} aria-hidden="true">
      <svg width={inner} height={inner} viewBox="0 0 24 24">
        <Glyph kind={sectorKind(theme)} bg={bg} />
      </svg>
    </span>
  );
}
