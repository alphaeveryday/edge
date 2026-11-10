import { useState } from 'react';
import { Delta, deltaClass, toast } from 'ui-kit';
import { DEMO_DATE, direction, formatPp, hhmm, orderContributions, type FactorDir, type FeedItem } from '../report.ts';
import { CandleChart } from './CandleChart';
import { SectorLogo } from './SectorLogo';

const MARK: Record<FactorDir, [string, string]> = {
  help: ['▲', 'var(--up)'],
  burden: ['▼', 'var(--down)'],
  neutral: ['■', 'var(--fg-4)'],
};

function CopyIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
      <rect x="9" y="9" width="13" height="13" rx="2" />
      <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
    </svg>
  );
}

function Bar({ value, max }: { value: number; max: number }) {
  const width = Math.min(50, (Math.abs(value) / max) * 50);
  return (
    <div className="bar-track">
      <span className="mid" />
      <span
        className="bar-fill"
        style={{
          ...(value >= 0 ? { left: '50%' } : { right: '50%' }),
          width: `${width}%`,
          background: value >= 0 ? 'var(--up)' : 'var(--down)',
        }}
      />
    </div>
  );
}

async function copyScript(text: string) {
  try {
    await navigator.clipboard.writeText(text);
    toast('고객 안내 문구를 복사했습니다.');
  } catch {
    toast('복사가 막혀 있습니다. 문구를 직접 선택해 복사해 주십시오.');
  }
}

/** 리포트 상세 — 선택이 바뀌면 부모가 key 로 새로 그려 펼침 상태를 초기화한다. */
export function ReportDetail({ report: r }: { report: FeedItem }) {
  const [showAll, setShowAll] = useState(false);
  const dir = direction(r.pct);
  const { lead, all } = orderContributions(r.pct, r.contrib);
  const shown = showAll ? all : lead.slice(0, 3);
  const maxC = Math.max(...all.map((c) => Math.abs(c[1])), 0.01);

  return (
    <div className="wrap">
      <div className="top">
        <SectorLogo theme={r.theme} size={44} />
        <div>
          <div className="top-title">
            <h1>{r.name}</h1>
            <span className="top-code">{r.code}</span>
          </div>
          <div className="sub">
            {DEMO_DATE} {hhmm(r.recv)} 기준 · 전일 종가 대비
          </div>
        </div>
        <Delta direction={dir} pct={Math.abs(r.pct)} style={{ marginLeft: 'auto', fontSize: 28, lineHeight: '32px' }} />
      </div>

      <div className="script">
        <div className="script-head">
          <span>고객 안내</span>
          <button className="btn btn-sm" type="button" onClick={() => copyScript(r.say)}>
            <CopyIcon />
            복사
          </button>
        </div>
        <p>{r.say}</p>
      </div>

      <div className="card mv">
        <span className="t-label">가격 변동 요인</span>
        <div className="mv-title">{r.title}</div>
        {r.groups.map((g) => (
          <div className="mv-group" key={g.head}>
            <div className="mv-head">{g.head}</div>
            {g.items.map((it) => (
              <div className="mv-item" key={it.t}>
                <span className="mv-mark" style={{ color: MARK[it.dir][1] }}>
                  {MARK[it.dir][0]}
                </span>
                <div style={{ flex: 1 }}>
                  <div className="mv-t">
                    {it.t}
                    <span className="ax">{it.ax}</span>
                  </div>
                  <div className="mv-sub">{it.sub}</div>
                </div>
              </div>
            ))}
          </div>
        ))}
      </div>

      <div className="grid2">
        <div className="card blk">
          <div className="blk-head">
            <span className="t-label">{r.pct >= 0 ? '상승 기여' : '하락 기여'}</span>
            <span className="t-label">{showAll ? `전체 ${all.length}종목` : 'TOP 3'}</span>
          </div>
          {shown.map(([name, v]) => (
            <div className="bar-row" key={name}>
              <span className="nm">{name}</span>
              <Bar value={v} max={maxC} />
              <span className={`num ${deltaClass(direction(v))}`} style={{ textAlign: 'right' }}>
                {formatPp(v)}
              </span>
            </div>
          ))}
          {all.length > 3 && (
            <button className="btn btn-sm more" type="button" onClick={() => setShowAll((v) => !v)}>
              {showAll ? '접기' : `다 보기 (${all.length}종목)`}
            </button>
          )}
        </div>
        <div className="card blk">
          <div className="blk-head">
            <span className="t-label">차트</span>
          </div>
          <CandleChart code={r.code} pct={r.pct} recv={r.recv} />
        </div>
      </div>

      <div className="foot">
        EDGE가 제공하는 가격 변동 설명은 기준시각까지 확인된 사실을 정리한 것이며, 특정 금융투자상품의 매매를 권유하지
        않습니다. 시연용 데모 화면으로, 표시된 수치·문구는 가상 데이터입니다.
      </div>
    </div>
  );
}
