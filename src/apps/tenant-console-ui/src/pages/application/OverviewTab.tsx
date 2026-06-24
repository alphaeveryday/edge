/* 앱 개요 탭 — applications 도메인 hook 만 의존한다 (mock 직접 import 없음). */
import { useState } from 'react';
import { useParams } from 'react-router-dom';
import { AreaChart, EnvBadge, Panel, Segmented, StatCard } from '../../components';
import { useApplication } from '../../domains/applications';

const WEEK = [0.9, 1.0, 0.97, 1.12, 1.06, 1.18, 1.24];
const WEEK_LABELS = ['월', '화', '수', '목', '금', '토', '일'];
const MONTH = [
  0.8, 0.86, 0.83, 0.9, 0.88, 0.95, 0.92, 1.0, 0.97, 1.04, 1.01, 0.96, 1.05, 1.02, 1.1, 1.07, 1.14,
  1.11, 1.06, 1.13, 1.09, 1.16, 1.12, 1.08, 1.18, 1.15, 1.1, 1.19, 1.22, 1.24,
];

export function OverviewTab() {
  const { appId } = useParams();
  const { app } = useApplication(appId);
  const [range, setRange] = useState('7일');

  if (!app) return null;

  const data = range === '7일' ? WEEK : MONTH;
  const labels: (string | number)[] =
    range === '7일' ? WEEK_LABELS : MONTH.map((_, i) => (i % 5 === 0 ? i + 1 : ''));

  return (
    <div>
      <div className="kpi-grid">
        <StatCard k="위젯 호출 (오늘)" v={app.calls} d="▲ 4.0%" note="전일" tone="up" />
        <StatCard k="MAU" v={app.mau} note="위젯 노출 사용자" />
        <StatCard k="에러율" v={app.callsErr} d="정상" tone="good" />
        <StatCard k="P95 레이턴시" v={app.p95} d="▼ 8ms" note="개선" tone="good" />
      </div>

      <div className="two-col sec-gap">
        <Panel
          title="위젯 호출 추이"
          actions={<Segmented opts={['7일', '30일']} value={range} onChange={setRange} />}
        >
          <AreaChart data={data} labels={labels} h={230} />
        </Panel>

        <Panel title="상태 요약" pad={false}>
          {[
            { label: '환경', value: <EnvBadge env={app.env} /> },
            { label: '활성 키', value: app.keys + '개' },
            { label: '웹훅', value: app.hooks + '개 활성' },
            { label: '분석 카테고리', value: '5종' },
            { label: '면책문구', value: 'v4 상속' },
          ].map((r) => (
            <div key={r.label} className="lrow">
              <span style={{ flex: 1, fontSize: 13, color: 'var(--n-600)' }}>{r.label}</span>
              <span className="num t-strong" style={{ fontSize: 13.5 }}>
                {r.value}
              </span>
            </div>
          ))}
        </Panel>
      </div>
    </div>
  );
}
