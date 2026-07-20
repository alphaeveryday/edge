/* 사용량 페이지 — usage 도메인 hook 만 의존한다 (mock 직접 import 없음). */
import { useState } from 'react';
import {
  AreaChart,
  BarChart,
  Icon,
  PageHeader,
  Panel,
  Segmented,
  StackedBars,
  toast,
} from '../components';
import { useUsage } from '../domains/usage';

/** 30일 차트 라벨 — 5일 간격으로만 일자를 표기해 밀집을 피한다. */
function monthlyLabels(len: number): (string | number)[] {
  return Array.from({ length: len }, (_, i) => (i % 5 === 0 ? i + 1 : ''));
}

export function UsagePage() {
  const { report, loading, error } = useUsage();
  const [range, setRange] = useState<'7일' | '30일'>('7일');

  return (
    <div>
      <PageHeader
        title="사용량"
        desc="앱별 위젯 호출량·지연(P95)·에러율과 MAU 추이를 분석합니다."
        actions={
          <button className="btn btn-ghost" onClick={() => toast('CSV로 내보냈습니다')}>
            <Icon n="list" s={16} />
            내보내기
          </button>
        }
      />

      {error ? (
        <Panel>
          <div className="empty-state">
            <div className="empty-ico">
              <Icon n="alert" s={26} />
            </div>
            <div style={{ marginTop: 12, fontSize: 14, fontWeight: 600, color: 'var(--n-700)' }}>
              사용량을 불러오지 못했습니다
            </div>
            <div className="muted" style={{ fontSize: 12.5, marginTop: 4 }}>
              {error.message}
            </div>
          </div>
        </Panel>
      ) : loading || !report ? (
        <Panel title="위젯 호출 추이">
          <div className="muted" style={{ fontSize: 13, padding: '40px 0', textAlign: 'center' }}>
            불러오는 중…
          </div>
        </Panel>
      ) : (
        <>
          <Panel
            title="위젯 호출 추이"
            sub={range === '7일' ? '최근 7일 · 단위 백만(M)' : '최근 30일 · 단위 백만(M)'}
            actions={
              <Segmented
                opts={['7일', '30일']}
                value={range}
                onChange={(v) => setRange(v as '7일' | '30일')}
              />
            }
          >
            {range === '7일' ? (
              <AreaChart
                data={report.callSeries.weekly}
                labels={report.callSeries.weekLabels}
                h={240}
              />
            ) : (
              <AreaChart
                data={report.callSeries.monthly}
                labels={monthlyLabels(report.callSeries.monthly.length)}
                h={240}
              />
            )}
          </Panel>

          <div className="two-col sec-gap">
            <Panel title="앱 점유율 (최근 7일)" sub="단위 백만 · 앱별 스택">
              <StackedBars
                rows={report.appShare.rows}
                labels={report.appShare.labels}
                names={report.appShare.names}
                h={230}
              />
            </Panel>
            <Panel title="MAU 추이" sub="최근 7개월 · 단위 천">
              <BarChart
                data={report.mau.months}
                labels={report.mau.labels}
                h={230}
                fmt={(v) => v + 'K'}
              />
            </Panel>
          </div>

          <Panel title="앱별 사용량" pad={false} style={{ marginTop: 20 }}>
            <table className="tbl">
              <thead>
                <tr>
                  <th>애플리케이션</th>
                  <th className="r">호출</th>
                  <th className="r">P95</th>
                  <th className="r">에러율</th>
                  <th>점유율</th>
                </tr>
              </thead>
              <tbody>
                {report.byApp.map((a) => (
                  <tr key={a.name}>
                    <td className="t-strong" style={{ fontSize: 13.5 }}>
                      {a.name}
                    </td>
                    <td className="r num" style={{ fontSize: 13 }}>
                      {a.calls}
                    </td>
                    <td className="r num" style={{ fontSize: 13 }}>
                      {a.p95}
                    </td>
                    <td
                      className="r num"
                      style={{
                        fontSize: 13,
                        color: parseFloat(a.err) > 1 ? 'var(--bad)' : 'var(--n-700)',
                      }}
                    >
                      {a.err}
                    </td>
                    <td>
                      <div className="row gap8" style={{ alignItems: 'center' }}>
                        <div
                          style={{
                            width: 52,
                            height: 6,
                            borderRadius: 4,
                            background: 'var(--n-150)',
                            overflow: 'hidden',
                          }}
                        >
                          <div
                            style={{
                              height: '100%',
                              width: a.share + '%',
                              borderRadius: 4,
                              background: 'var(--p-500)',
                            }}
                          />
                        </div>
                        <span className="num" style={{ fontSize: 12.5, color: 'var(--n-600)' }}>
                          {a.share}%
                        </span>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Panel>
        </>
      )}
    </div>
  );
}
