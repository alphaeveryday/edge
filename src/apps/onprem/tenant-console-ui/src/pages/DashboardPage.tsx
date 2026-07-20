/* 대시보드 페이지 — dashboard 도메인 hook 만 의존한다 (mock 직접 import 없음). */
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  AreaChart,
  Badge,
  EnvBadge,
  Icon,
  ImpactBar,
  PageHeader,
  Panel,
  Segmented,
  StatCard,
} from '../components';
import { useDashboard } from '../domains/dashboard';

export function DashboardPage() {
  const nav = useNavigate();
  const { overview, loading, error } = useDashboard();
  const [range, setRange] = useState('7일');

  if (error) {
    return (
      <div>
        <PageHeader title="홈 / 대시보드" desc="조직 전체 현황을 한눈에 확인합니다." />
        <Panel>
          <div className="empty-state">
            <div className="empty-ico">
              <Icon n="alert" s={26} />
            </div>
            <div style={{ marginTop: 12, fontSize: 14, fontWeight: 600, color: 'var(--n-700)' }}>
              대시보드를 불러오지 못했습니다
            </div>
            <div className="muted" style={{ fontSize: 12.5, marginTop: 4 }}>
              {error.message}
            </div>
          </div>
        </Panel>
      </div>
    );
  }

  if (loading || !overview) {
    return (
      <div>
        <PageHeader
          title="홈 / 대시보드"
          badge={<Badge tone="ok" dot>실시간</Badge>}
          desc="조직 전체 위젯 호출·MAU·에러율·분석 출력과 최근 분석 이벤트를 한눈에 확인합니다."
        />
        <div className="kpi-grid">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="card card-pad">
              <div className="skel-bar" style={{ height: 56, width: '100%' }} />
            </div>
          ))}
        </div>
      </div>
    );
  }

  const { kpis, callSeries, mauByApp, events, apps, complianceSummary } = overview;
  const { weekly, monthly, weekLabels } = callSeries;
  const data = range === '7일' ? weekly : monthly;
  const labels: (string | number)[] =
    range === '7일' ? weekLabels : monthly.map((_, i) => (i % 5 === 0 ? i + 1 : ''));

  return (
    <div>
      <PageHeader
        title="홈 / 대시보드"
        badge={<Badge tone="ok" dot>실시간</Badge>}
        desc="조직 전체 위젯 호출·MAU·에러율·분석 출력과 최근 분석 이벤트를 한눈에 확인합니다."
        actions={
          <button className="btn btn-pri" onClick={() => nav('/applications')}>
            <Icon n="plus" s={16} />
            애플리케이션
          </button>
        }
      />

      <div className="kpi-grid">
        {kpis.map((k) => (
          <StatCard key={k.k} {...k} />
        ))}
      </div>

      <div className="two-col sec-gap">
        <Panel
          title="위젯 호출 추이"
          sub={range === '7일' ? '최근 7일 · 단위 백만' : '최근 30일 · 단위 백만'}
          actions={<Segmented opts={['7일', '30일']} value={range} onChange={setRange} />}
        >
          <AreaChart data={data} labels={labels} h={230} />
        </Panel>
        <Panel title="앱별 MAU" sub="위젯 노출 월간 순 사용자" pad={false}>
          {mauByApp.map((a) => (
            <div key={a.name} className="lrow">
              <div className="app-ico">
                <Icon n="users" s={17} />
              </div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div className="t-strong" style={{ fontSize: 13.5 }}>
                  {a.name}
                </div>
              </div>
              <div style={{ textAlign: 'right' }}>
                <div className="num t-strong" style={{ fontSize: 14 }}>
                  {a.mau}
                </div>
                <div
                  className="num"
                  style={{ fontSize: 11.5, color: 'var(--ok)', fontWeight: 600 }}
                >
                  {a.d}
                </div>
              </div>
            </div>
          ))}
        </Panel>
      </div>

      <div className="two-col sec-gap">
        <Panel
          title="최근 분석 이벤트"
          sub="이벤트 기반 ETF 영향 분석"
          pad={false}
          actions={
            <button className="btn btn-quiet btn-sm" onClick={() => nav('/compliance')}>
              전체 보기
              <Icon n="chevR" s={14} />
            </button>
          }
        >
          <table className="tbl hover">
            <thead>
              <tr>
                <th>ID</th>
                <th>이벤트</th>
                <th>대상 ETF</th>
                <th>영향도</th>
                <th className="r">시각</th>
              </tr>
            </thead>
            <tbody>
              {events.map((e) => (
                <tr key={e.id} onClick={() => nav('/compliance')}>
                  <td className="mono" style={{ fontSize: 12, color: 'var(--n-500)' }}>
                    {e.id}
                  </td>
                  <td>
                    <div className="row gap8">
                      <Badge tone="neutral">{e.cat}</Badge>
                      <span
                        className="t-strong"
                        style={{
                          whiteSpace: 'nowrap',
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          maxWidth: 230,
                        }}
                      >
                        {e.title}
                      </span>
                    </div>
                  </td>
                  <td style={{ whiteSpace: 'nowrap' }}>{e.etf}</td>
                  <td>
                    <ImpactBar v={e.impact} />
                  </td>
                  <td className="r muted" style={{ fontSize: 12.5, whiteSpace: 'nowrap' }}>
                    {e.t}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
        <div className="col gap20">
          <Panel
            title="애플리케이션"
            sub={apps.length + '개 운영 중'}
            pad={false}
            actions={
              <button className="btn btn-quiet btn-sm" onClick={() => nav('/applications')}>
                관리
              </button>
            }
          >
            {apps.map((a) => (
              <div
                key={a.id}
                className="lrow"
                style={{ cursor: 'pointer' }}
                onClick={() => nav(`/applications/${a.slug}/overview`)}
              >
                <div className="app-ico">
                  <Icon n={a.type === '모바일' ? 'grid' : 'building2'} s={18} />
                </div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div className="t-strong" style={{ fontSize: 13.5 }}>
                    {a.name}
                  </div>
                  <div className="num" style={{ fontSize: 11.5, color: 'var(--n-500)' }}>
                    {a.calls}호출 · 에러 {a.callsErr}
                  </div>
                </div>
                <EnvBadge env={a.env} />
              </div>
            ))}
          </Panel>
          <Panel
            title="컴플라이언스 요약"
            pad={false}
            actions={
              <button className="btn btn-quiet btn-sm" onClick={() => nav('/compliance')}>
                면책·키워드
              </button>
            }
          >
            {complianceSummary.map((r) => (
              <div key={r.label} className="lrow">
                <span style={{ flex: 1, fontSize: 13, color: 'var(--n-600)' }}>{r.label}</span>
                <span
                  className="num"
                  style={{
                    fontWeight: 650,
                    fontSize: 13.5,
                    color:
                      r.tone === 'bad'
                        ? 'var(--bad)'
                        : r.tone === 'warn'
                          ? 'var(--warn)'
                          : r.tone === 'ok'
                            ? 'var(--ok)'
                            : 'var(--n-800)',
                  }}
                >
                  {r.value}
                </span>
              </div>
            ))}
          </Panel>
        </div>
      </div>
    </div>
  );
}
