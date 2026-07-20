/* EDGE Console — charts & stat primitives (presentational, no data deps).
 * 원본 디자인(ui.jsx)의 SVG 차트를 TS 로 포팅.
 */
import { useEffect, useRef, useState } from 'react';
import type { CSSProperties } from 'react';

type StatTone = 'up' | 'down' | 'good' | 'warn' | 'flat';

export function StatCard({
  k,
  v,
  d,
  note,
  tone,
  pct,
}: {
  k: string;
  v: string;
  d?: string;
  note?: string;
  tone?: StatTone;
  pct?: number;
}) {
  const dColor =
    tone === 'up' || tone === 'good'
      ? 'var(--ok)'
      : tone === 'warn'
        ? 'var(--warn)'
        : tone === 'down'
          ? 'var(--bad)'
          : 'var(--n-500)';
  return (
    <div className="card card-pad" style={{ display: 'flex', flexDirection: 'column', gap: 9 }}>
      <div style={{ fontSize: 12.5, color: 'var(--n-500)', fontWeight: 600 }}>{k}</div>
      <div
        className="num"
        style={{
          fontSize: 29,
          fontWeight: 720,
          letterSpacing: '-.03em',
          color: 'var(--n-900)',
          lineHeight: 1,
        }}
      >
        {v}
      </div>
      <div className="row gap8" style={{ alignItems: 'center' }}>
        {d && (
          <span className="num" style={{ fontSize: 12.5, fontWeight: 650, color: dColor }}>
            {d}
          </span>
        )}
        {note && <span style={{ fontSize: 12, color: 'var(--n-400)' }}>{note}</span>}
      </div>
      {pct != null && (
        <div
          style={{
            height: 6,
            borderRadius: 4,
            background: 'var(--n-150)',
            overflow: 'hidden',
            marginTop: 2,
          }}
        >
          <div
            style={{
              height: '100%',
              width: pct + '%',
              borderRadius: 4,
              background: pct > 80 ? 'var(--warn)' : 'var(--p-500)',
            }}
          />
        </div>
      )}
    </div>
  );
}

export function ProgressRow({
  k,
  used,
  lim,
  pct,
}: {
  k: string;
  used: string;
  lim: string;
  pct: number;
}) {
  const unlimited = lim === '무제한';
  const c = pct >= 75 ? 'var(--warn)' : 'var(--p-500)';
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div className="row spread">
        <span style={{ fontSize: 13.5, fontWeight: 600, color: 'var(--n-700)' }}>{k}</span>
        <span className="num" style={{ fontSize: 13, color: 'var(--n-500)' }}>
          <b style={{ color: 'var(--n-800)' }}>{used}</b> / {lim}
          {!unlimited && (
            <>
              {' · '}
              <b style={{ color: c }}>{pct}%</b>
            </>
          )}
        </span>
      </div>
      <div style={{ height: 8, borderRadius: 5, background: 'var(--n-150)', overflow: 'hidden' }}>
        <div
          style={{
            height: '100%',
            width: (unlimited ? 12 : pct) + '%',
            borderRadius: 5,
            background: `linear-gradient(90deg,var(--p-500),${unlimited ? 'var(--p-400)' : c})`,
          }}
        />
      </div>
    </div>
  );
}

function smoothPath(pts: number[][]): string {
  if (pts.length < 2) return '';
  let d = `M ${pts[0][0]} ${pts[0][1]}`;
  for (let i = 0; i < pts.length - 1; i++) {
    const [x0, y0] = pts[i];
    const [x1, y1] = pts[i + 1];
    const mx = (x0 + x1) / 2;
    d += ` C ${mx} ${y0}, ${mx} ${y1}, ${x1} ${y1}`;
  }
  return d;
}

/** 컨테이너 너비를 추적하는 hook (반응형 SVG 차트용). */
function useWidth(initial = 640): [React.RefObject<HTMLDivElement | null>, number] {
  const ref = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(initial);
  useEffect(() => {
    if (!ref.current) return;
    const ro = new ResizeObserver((es) => setW(es[0].contentRect.width));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  return [ref, w];
}

export function AreaChart({
  data,
  h = 210,
  labels,
  color = 'var(--p-500)',
}: {
  data: number[];
  h?: number;
  labels?: (string | number)[];
  color?: string;
}) {
  const [ref, w] = useWidth();
  const padL = 8;
  const padR = 8;
  const padT = 14;
  const padB = 24;
  const max = Math.max(...data) * 1.12;
  const min = Math.min(...data) * 0.86;
  const iw = w - padL - padR;
  const ih = h - padT - padB;
  const pts = data.map((v, i) => [
    padL + (iw * i) / (data.length - 1),
    padT + ih - ((v - min) / (max - min)) * ih,
  ]);
  const line = smoothPath(pts);
  const area = line + ` L ${pts[pts.length - 1][0]} ${padT + ih} L ${pts[0][0]} ${padT + ih} Z`;
  const gid = 'ag' + Math.round((h * 1000) % 9999);
  return (
    <div ref={ref} style={{ width: '100%' }}>
      <svg width={w} height={h} style={{ display: 'block' }}>
        <defs>
          <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity="0.22" />
            <stop offset="100%" stopColor={color} stopOpacity="0" />
          </linearGradient>
        </defs>
        {[0, 0.25, 0.5, 0.75, 1].map((g) => (
          <line
            key={g}
            x1={padL}
            x2={w - padR}
            y1={padT + ih * g}
            y2={padT + ih * g}
            stroke="var(--n-150)"
            strokeWidth="1"
          />
        ))}
        <path d={area} fill={`url(#${gid})`} />
        <path d={line} fill="none" stroke={color} strokeWidth="2.4" strokeLinecap="round" />
        {pts.map((p, i) =>
          i === pts.length - 1 ? (
            <g key={i}>
              <circle cx={p[0]} cy={p[1]} r="5.5" fill={color} opacity="0.18" />
              <circle cx={p[0]} cy={p[1]} r="3.5" fill="#fff" stroke={color} strokeWidth="2.2" />
            </g>
          ) : null,
        )}
        {labels?.map((l, i) => (
          <text
            key={i}
            x={pts[i][0]}
            y={h - 7}
            textAnchor="middle"
            fontSize="10.5"
            fill="var(--n-400)"
            fontFamily="var(--sans)"
          >
            {l}
          </text>
        ))}
      </svg>
    </div>
  );
}

export function StackedBars({
  rows,
  h = 210,
  labels,
  colors = ['var(--p-600)', 'var(--p-400)', 'var(--p-200)'],
  names,
}: {
  rows: number[][];
  h?: number;
  labels?: string[];
  colors?: string[];
  names?: string[];
}) {
  const [ref, w] = useWidth();
  const padT = 14;
  const padB = 24;
  const padL = 6;
  const padR = 6;
  const totals = rows.map((r) => r.reduce((a, b) => a + b, 0));
  const max = Math.max(...totals) * 1.14;
  const ih = h - padT - padB;
  const iw = w - padL - padR;
  const bw = Math.min(38, (iw / rows.length) * 0.56);
  const gap = iw / rows.length;
  return (
    <div ref={ref} style={{ width: '100%' }}>
      <svg width={w} height={h} style={{ display: 'block' }}>
        {[0, 0.5, 1].map((g) => (
          <line
            key={g}
            x1={padL}
            x2={w - padR}
            y1={padT + ih * g}
            y2={padT + ih * g}
            stroke="var(--n-150)"
          />
        ))}
        {rows.map((r, i) => {
          let y = padT + ih;
          const cx = padL + gap * i + gap / 2;
          return (
            <g key={i}>
              {r.map((v, j) => {
                const bh = (v / max) * ih;
                y -= bh;
                return (
                  <rect
                    key={j}
                    x={cx - bw / 2}
                    y={y}
                    width={bw}
                    height={Math.max(bh - 1.5, 0)}
                    rx="2.5"
                    fill={colors[j % colors.length]}
                  />
                );
              })}
              {labels && (
                <text x={cx} y={h - 7} textAnchor="middle" fontSize="10.5" fill="var(--n-400)">
                  {labels[i]}
                </text>
              )}
            </g>
          );
        })}
      </svg>
      {names && (
        <div className="row gap16" style={{ justifyContent: 'center', marginTop: 4 }}>
          {names.map((nm, j) => (
            <span key={j} className="row gap6" style={{ fontSize: 11.5, color: 'var(--n-500)' }}>
              <span
                style={{ width: 9, height: 9, borderRadius: 3, background: colors[j % colors.length] }}
              />
              {nm}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

export function BarChart({
  data,
  h = 200,
  labels,
  fmt = (v: number) => String(v),
}: {
  data: number[];
  h?: number;
  labels?: string[];
  fmt?: (v: number) => string;
}) {
  const [ref, w] = useWidth();
  const padT = 18;
  const padB = 24;
  const padL = 6;
  const padR = 6;
  const max = Math.max(...data) * 1.12;
  const ih = h - padT - padB;
  const iw = w - padL - padR;
  const gap = iw / data.length;
  const bw = Math.min(42, gap * 0.5);
  return (
    <div ref={ref} style={{ width: '100%' }}>
      <svg width={w} height={h} style={{ display: 'block' }}>
        {data.map((v, i) => {
          const bh = (v / max) * ih;
          const cx = padL + gap * i + gap / 2;
          const last = i === data.length - 1;
          return (
            <g key={i}>
              <rect
                x={cx - bw / 2}
                y={padT + ih - bh}
                width={bw}
                height={bh}
                rx="3"
                fill={last ? 'var(--p-600)' : 'var(--p-200)'}
              />
              <text
                x={cx}
                y={padT + ih - bh - 6}
                textAnchor="middle"
                fontSize="10.5"
                fontWeight="600"
                fill={last ? 'var(--p-700)' : 'var(--n-400)'}
              >
                {fmt(v)}
              </text>
              {labels && (
                <text x={cx} y={h - 7} textAnchor="middle" fontSize="10.5" fill="var(--n-400)">
                  {labels[i]}
                </text>
              )}
            </g>
          );
        })}
      </svg>
    </div>
  );
}

export function DonutGauge({
  pct,
  size = 132,
  label,
  sub,
  color,
}: {
  pct: number;
  size?: number;
  label?: string;
  sub?: string;
  color?: string;
}) {
  const c = color || (pct > 80 ? 'var(--warn)' : 'var(--p-500)');
  const r = size / 2 - 11;
  const C = 2 * Math.PI * r;
  return (
    <div style={{ position: 'relative', width: size, height: size }}>
      <svg width={size} height={size}>
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--n-150)" strokeWidth="11" />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke={c}
          strokeWidth="11"
          strokeLinecap="round"
          strokeDasharray={`${(C * pct) / 100} ${C}`}
          transform={`rotate(-90 ${size / 2} ${size / 2})`}
        />
      </svg>
      <div
        style={{
          position: 'absolute',
          inset: 0,
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        <div
          className="num"
          style={{ fontSize: 26, fontWeight: 740, color: 'var(--n-900)', letterSpacing: '-.03em' }}
        >
          {label || pct + '%'}
        </div>
        {sub && <div style={{ fontSize: 11, color: 'var(--n-500)', marginTop: 2 }}>{sub}</div>}
      </div>
    </div>
  );
}

export function Sparkline({
  data,
  w = 92,
  h = 30,
  color = 'var(--p-500)',
}: {
  data: number[];
  w?: number;
  h?: number;
  color?: string;
}) {
  const max = Math.max(...data);
  const min = Math.min(...data);
  const pts = data.map((v, i) => [
    (i / (data.length - 1)) * w,
    h - 2 - ((v - min) / (max - min || 1)) * (h - 4),
  ]);
  return (
    <svg width={w} height={h}>
      <path d={smoothPath(pts)} fill="none" stroke={color} strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

export function ImpactBar({ v, style }: { v: number; style?: CSSProperties }) {
  const c = v >= 60 ? 'var(--bad)' : v >= 45 ? 'var(--warn)' : 'var(--p-500)';
  return (
    <div className="row gap8" style={{ alignItems: 'center', ...style }}>
      <div style={{ width: 52, height: 6, borderRadius: 4, background: 'var(--n-150)', overflow: 'hidden' }}>
        <div style={{ height: '100%', width: v + '%', background: c, borderRadius: 4 }} />
      </div>
      <span className="num" style={{ fontSize: 12.5, fontWeight: 650, color: c, width: 20 }}>
        {v}
      </span>
    </div>
  );
}
