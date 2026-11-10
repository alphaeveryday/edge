import { demoCandles, hhmm } from '../report.ts';

const W = 560;
const H = 190;
const PL = 42;
const PR = 8;
const PT = 10;
const PB = 22;

const signed = (v: number) => (v > 0 ? '+' : v < 0 ? '−' : '') + Math.abs(v).toFixed(2);

/** 장 시작부터 수신 시각까지의 10분봉(전일 종가 대비 %) — 시연용 생성 데이터. */
export function CandleChart({ code, pct, recv }: { code: string; pct: number; recv: number }) {
  const cs = demoCandles(code, pct, recv);
  const n = cs.length;
  let lo = Math.min(0, ...cs.map((c) => c.l));
  let hi = Math.max(0, ...cs.map((c) => c.h));
  const pd = (hi - lo) * 0.08;
  lo -= pd;
  hi += pd;
  const y = (v: number) => PT + ((hi - v) / (hi - lo)) * (H - PT - PB);
  const cw = (W - PL - PR) / n;
  const x = (i: number) => PL + cw * i + cw / 2;
  const ticks = [hi - pd, (hi + lo) / 2, lo + pd];
  const bw = Math.max(2, cw * 0.62);

  return (
    <>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ display: 'block' }} role="img" aria-label="10분봉 차트">
        {ticks.map((t) => (
          <g key={t}>
            <line x1={PL} x2={W - PR} y1={y(t)} y2={y(t)} stroke="var(--border-faint)" />
            <text x={PL - 6} y={y(t) + 3.5} textAnchor="end" fontSize="10" fill="var(--fg-4)">
              {signed(t)}%
            </text>
          </g>
        ))}
        <line x1={PL} x2={W - PR} y1={y(0)} y2={y(0)} stroke="var(--border-strong)" strokeDasharray="3 3" />
        {cs.map((c, i) => {
          const col = c.c >= c.o ? 'var(--up)' : 'var(--down)';
          return (
            <g key={i}>
              <line x1={x(i)} x2={x(i)} y1={y(c.h)} y2={y(c.l)} stroke={col} strokeWidth="1" />
              <rect
                x={x(i) - bw / 2}
                y={y(Math.max(c.o, c.c))}
                width={bw}
                height={Math.max(1, Math.abs(y(c.o) - y(c.c)))}
                fill={col}
              />
            </g>
          );
        })}
        <text x={PL} y={H - 6} fontSize="10" fill="var(--fg-4)">
          09:00
        </text>
        <text x={W - PR} y={H - 6} fontSize="10" fill="var(--fg-4)" textAnchor="end">
          {hhmm(recv)}
        </text>
      </svg>
      <div className="chart-legend">
        <span>10분봉 · 전일 종가 대비</span>
        <span>점선 = 전일 종가</span>
      </div>
    </>
  );
}
